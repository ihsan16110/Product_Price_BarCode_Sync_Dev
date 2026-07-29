import asyncio
import random
from datetime import datetime, timedelta

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


class RetryEntry:
    """Represents a failed outlet queued for retry."""

    def __init__(self, outlet: dict, error: str, attempt: int = 1):
        self.outlet = outlet
        self.outlet_code: str = outlet.get("Outlet", "Unknown")
        self.server: str = outlet.get("Server", "N/A")
        self.attempt = attempt
        self.max_attempts = settings.RETRY_MAX_ATTEMPTS
        self.last_error = error
        self.added_at = datetime.now()
        self.next_retry_at = self._calculate_next_retry()

    def _calculate_next_retry(self) -> datetime:
        """Exponential backoff with jitter: base * 2^attempt + random(0, 5)"""
        delay = settings.RETRY_BASE_DELAY * (2 ** (self.attempt - 1))
        jitter = random.uniform(0, 5)
        return datetime.now() + timedelta(seconds=delay + jitter)

    def to_dict(self) -> dict:
        return {
            "outlet_code": self.outlet_code,
            "server": self.server,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "next_retry_at": self.next_retry_at.isoformat(),
            "last_error": self.last_error,
            "added_at": self.added_at.isoformat(),
        }

    @classmethod
    def from_persisted(cls, data: dict) -> "RetryEntry":
        outlet = {
            "Outlet": data["outlet_code"], "Server": data["server"],
            "Database": settings.LOCAL_DB, "User": settings.OUTLET_DB_USER,
            "Password": settings.OUTLET_DB_PASSWORD,
        }
        entry = cls(outlet, data.get("last_error", ""), int(data["attempt"]))
        entry.added_at = datetime.fromisoformat(data["added_at"])
        entry.next_retry_at = datetime.fromisoformat(data["next_retry_at"])
        return entry


class RetryQueue:
    """
    In-memory retry queue for failed outlet syncs.
    Deduplicated by outlet code - only one entry per outlet at a time.
    """

    def __init__(self):
        self._queue: dict[str, RetryEntry] = {}
        self._permanently_failed: dict[str, RetryEntry] = {}

    def add(self, outlet: dict, error: str, attempt: int = 1) -> None:
        """Add or update a retry entry for a failed outlet."""
        code = outlet.get("Outlet", "Unknown")

        if attempt >= settings.RETRY_MAX_ATTEMPTS:
            logger.warning(
                f"Outlet {code} reached max attempts ({attempt}/{settings.RETRY_MAX_ATTEMPTS}), "
                f"marking as permanently failed for this cycle"
            )
            entry = RetryEntry(outlet, error, attempt)
            self._permanently_failed[code] = entry
            self._queue.pop(code, None)
            return

        entry = RetryEntry(outlet, error, attempt)
        self._queue[code] = entry
        logger.info(
            f"Queued retry for {code} (attempt {attempt}/{settings.RETRY_MAX_ATTEMPTS}, "
            f"next at {entry.next_retry_at.strftime('%H:%M:%S')})"
        )

    def get_due(self) -> list[RetryEntry]:
        """Return entries whose next_retry_at has passed."""
        now = datetime.now()
        due = [e for e in self._queue.values() if e.next_retry_at <= now]
        return due

    def remove(self, outlet_code: str) -> None:
        """Remove an entry on successful retry."""
        self._queue.pop(outlet_code, None)
        self._permanently_failed.pop(outlet_code, None)

    def get_all(self) -> list[dict]:
        """Return all queued entries for API visibility."""
        entries = [e.to_dict() for e in self._queue.values()]
        for e in self._permanently_failed.values():
            d = e.to_dict()
            d["permanently_failed"] = True
            entries.append(d)
        return entries

    def clear(self) -> None:
        """Clear the entire retry queue."""
        count = len(self._queue) + len(self._permanently_failed)
        self._queue.clear()
        self._permanently_failed.clear()
        logger.info(f"Retry queue cleared ({count} entries removed)")

    def restore(self, entries: list[dict]) -> None:
        """Replace in-memory contents with rows loaded from the log database."""
        self._queue.clear()
        self._permanently_failed.clear()
        for data in entries:
            entry = RetryEntry.from_persisted(data)
            # Re-evaluate persisted entries against the current policy. This
            # revives entries exhausted under an older, lower retry limit and
            # also handles a limit that has subsequently been reduced.
            target = (
                self._permanently_failed
                if entry.attempt >= settings.RETRY_MAX_ATTEMPTS
                else self._queue
            )
            target[entry.outlet_code] = entry
        logger.info(f"Restored {len(entries)} retry entries from the log database")

    @property
    def size(self) -> int:
        return len(self._queue) + len(self._permanently_failed)

    @property
    def pending_count(self) -> int:
        return len(self._queue)


async def start_retry_processor(sync_manager) -> None:
    """
    Background loop that checks for due retries every 10 seconds
    and dispatches them through the sync manager.
    """
    logger.info("Retry processor started")
    while not sync_manager.stopped:
        try:
            due_entries = sync_manager.retry_queue.get_due()
            if due_entries:
                logger.info(f"Processing {len(due_entries)} due retries")
                for entry in due_entries:
                    if sync_manager.stopped:
                        break
                    # Remove from queue before processing
                    sync_manager.retry_queue._queue.pop(entry.outlet_code, None)
                    # Dispatch retry through semaphore
                    asyncio.create_task(
                        sync_manager.retry_single_outlet(entry)
                    )
        except Exception as e:
            logger.error(f"Error in retry processor: {e}")

        await asyncio.sleep(10)

    logger.info("Retry processor stopped")

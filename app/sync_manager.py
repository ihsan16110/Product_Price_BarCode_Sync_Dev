import asyncio
from datetime import datetime
from uuid import uuid4

from app.config import settings
from app.database import build_outlet_list, load_outlet_data, make_connection
from app.db_logger import (
    load_cycle_dashboard_state,
    load_retry_queue,
    replace_retry_queue,
    save_cycle_summary,
)
from app.logger import get_logger
from app.retry_queue import RetryQueue
from app.sync_engine import run_on_outlet

logger = get_logger(__name__)


class SyncManager:
    """
    Central orchestrator for sync operations.
    Manages concurrency, state tracking, and retry queue.
    """

    def __init__(self):
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SYNCS)
        self.sync_lock = asyncio.Lock()
        self.retry_queue = RetryQueue()
        self.current_cycle_task: asyncio.Task | None = None
        self.outlet_tasks: dict[asyncio.Task, dict] = {}
        self.cancel_requested = asyncio.Event()
        self.outlet_locks: dict[str, asyncio.Lock] = {}
        self.outlet_operations: dict[str, asyncio.Task] = {}
        self.draining_outlets: dict[str, asyncio.Task] = {}

        # State tracking
        self.is_running = False
        self.stopped = False
        self.current_trigger = ""
        self.sync_started_at: datetime | None = None
        self.sync_finished_at: datetime | None = None
        self.total_outlets = 0
        self.completed_count = 0
        self.failed_count = 0
        self.in_progress_count = 0
        self.excluded_count = 0
        self.cancelled_count = 0
        self.audit_failure_count = 0
        self.current_run_id: str | None = None
        self.active_outlets: dict[str, dict] = {}

        # Results from last/current cycle
        self.outlet_results: list[dict] = []

        # Lifetime stats
        self.total_syncs_completed = 0
        self.total_syncs_attempted = 0
        self.total_syncs_partial_failure = 0
        self.total_syncs_cancelled = 0
        self.total_syncs_timed_out = 0
        self.total_syncs_failed = 0
        self.last_cycle_outcome = ""
        self.service_started_at = datetime.now()

    async def restore_persisted_state(self) -> None:
        """Restore the last dashboard snapshot, lifetime totals, and retry queue."""
        dashboard = await load_cycle_dashboard_state()
        self.total_syncs_attempted = dashboard["attempted"]
        self.total_syncs_completed = dashboard["completed"]
        self.total_syncs_partial_failure = dashboard["partial_failure"]
        self.total_syncs_cancelled = dashboard["cancelled_total"]
        self.total_syncs_timed_out = dashboard["timed_out"]
        self.total_syncs_failed = dashboard["failed_total"]
        last = dashboard.get("last")
        if last:
            self.current_run_id = last["run_id"]
            self.current_trigger = last["trigger"] or ""
            self.sync_started_at = last["started_at"]
            self.sync_finished_at = last["finished_at"]
            self.last_cycle_outcome = last["outcome"]
            self.total_outlets = last["total_outlets"]
            self.completed_count = last["successful"]
            self.failed_count = last["failed"]
            self.excluded_count = last["excluded"]
            self.cancelled_count = last["cancelled"]
            self.audit_failure_count = last["audit_failed"]
        self.retry_queue.restore(await load_retry_queue())

    async def persist_retry_queue(self) -> None:
        await replace_retry_queue(self.retry_queue.get_all())

    async def clear_retry_queue(self) -> int:
        """Clear SQL first so a failed database write cannot lose retry entries."""
        size = self.retry_queue.size
        await replace_retry_queue([])
        self.retry_queue.clear()
        return size

    def get_status(self) -> dict:
        """Return current sync cycle status."""
        if self.is_running and self.cancel_requested.is_set():
            state = "stopping"
        else:
            state = "running" if self.is_running else "idle"
        return {
            "state": state,
            "started_at": self.sync_started_at.isoformat() if self.sync_started_at else None,
            "finished_at": self.sync_finished_at.isoformat() if self.sync_finished_at else None,
            "total_outlets": self.total_outlets,
            "completed": self.completed_count,
            "failed": self.failed_count,
            "in_progress": self.in_progress_count,
            "excluded": self.excluded_count,
            "cancelled": self.cancelled_count,
            "audit_failed": self.audit_failure_count,
            "run_id": self.current_run_id,
            "active_outlets": list(self.active_outlets.values()),
            "draining_outlets": sorted(self.draining_outlets),
            "trigger": self.current_trigger,
            "outcome": self.last_cycle_outcome,
        }

    def start_full_sync(self, trigger: str = "manual") -> asyncio.Task | None:
        """Atomically create and retain the current full-cycle task."""
        if self.current_cycle_task and not self.current_cycle_task.done():
            return None

        self.cancel_requested.clear()
        task = asyncio.create_task(self.run_full_sync(trigger=trigger))
        self.current_cycle_task = task

        def _clear_current(completed_task: asyncio.Task) -> None:
            if self.current_cycle_task is completed_task:
                self.current_cycle_task = None

        task.add_done_callback(_clear_current)
        return task

    def request_cancellation(self) -> dict:
        """
        Gracefully stop the current cycle.

        Tasks that have not entered an outlet operation are cancelled immediately.
        Active blocking ODBC calls are allowed to finish or reach their query timeout.
        """
        task = self.current_cycle_task
        if task is None or task.done():
            return {
                "status": "idle",
                "message": "No synchronization cycle is running",
                "active_outlets": [],
                "pending_outlets_cancelled": 0,
            }

        self.cancel_requested.set()
        active_codes = set(self.active_outlets)
        pending_cancelled = 0
        for outlet_task, outlet in list(self.outlet_tasks.items()):
            code = outlet.get("Outlet", "Unknown")
            if code not in active_codes and not outlet_task.done():
                outlet_task.cancel()
                pending_cancelled += 1

        logger.warning(
            f"Sync cancellation requested; cancelled {pending_cancelled} pending outlets; "
            f"waiting for {len(active_codes)} active outlets"
        )
        return {
            "status": "stopping",
            "message": "Cancellation requested; active database operations will drain",
            "active_outlets": sorted(active_codes),
            "pending_outlets_cancelled": pending_cancelled,
        }

    def get_outlet_results(self) -> list[dict]:
        """Return all outlet results from the last/current cycle."""
        return list(self.outlet_results)

    def get_outlet_result(self, outlet_code: str) -> dict | None:
        """Return result for a specific outlet."""
        for r in self.outlet_results:
            if r["outlet_code"].upper() == outlet_code.upper():
                return r
        return None

    @staticmethod
    async def _check_central_db_health() -> tuple[bool, bool]:
        """
        Health check on both central (SOURCE) and log (LOG) databases.
        Returns (source_healthy, log_healthy).
        """
        async def _check_one(label: str, server: str, db: str, user: str, pw: str) -> bool:
            conn = None
            try:
                conn = make_connection(
                    server=server,
                    database=db,
                    user=user,
                    password=pw,
                    autocommit=True,
                    timeout=5,
                )
                conn.timeout = 5
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return True
            except Exception as e:
                logger.error(f"{label} health check failed: {e}")
                return False
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        source_ok = await _check_one(
            "Source DB",
            settings.SOURCE_SERVER,
            settings.SOURCE_DATABASE,
            settings.SOURCE_USER,
            settings.SOURCE_PASSWORD,
        )
        log_ok = await _check_one(
            "Log DB",
            settings.LOG_SERVER,
            settings.LOG_DATABASE,
            settings.LOG_USER,
            settings.LOG_PASSWORD,
        )
        return source_ok, log_ok

    async def _run_outlet_with_watchdog(self, outlet: dict) -> dict:
        """Run any outlet operation with concurrency and timeout protection."""
        name = outlet.get("Outlet", "Unknown")
        operation_key = name.upper()
        existing = self.outlet_operations.get(operation_key)
        if existing is not None and not existing.done():
            raise RuntimeError(
                f"Outlet {name} already has an active database operation; "
                "retry after it has drained"
            )

        async with self.semaphore:
            if self.cancel_requested.is_set():
                raise asyncio.CancelledError
            self.in_progress_count += 1
            self.active_outlets[name] = {
                "outlet_code": name,
                "server": outlet.get("Server", "N/A"),
                "started_at": datetime.now().isoformat(),
                "state": "running",
            }
            lock = self.outlet_locks.setdefault(operation_key, asyncio.Lock())

            async def _run_with_outlet_lock():
                async with lock:
                    return await run_on_outlet(outlet)

            operation_task = asyncio.create_task(_run_with_outlet_lock())
            self.outlet_operations[operation_key] = operation_task
            try:
                return await asyncio.wait_for(
                    asyncio.shield(operation_task),
                    timeout=settings.OUTLET_SYNC_TIMEOUT,
                )
            except asyncio.TimeoutError:
                error_msg = (
                    f"Outlet watchdog timed out after {settings.OUTLET_SYNC_TIMEOUT} seconds"
                )
                logger.error(
                    f"{error_msg}: {name} at {outlet.get('Server', 'N/A')}"
                )
                self._track_draining_operation(operation_key, name, operation_task)
                return self._failure_result(outlet, error_msg)
            except asyncio.CancelledError:
                if not operation_task.done():
                    self._track_draining_operation(operation_key, name, operation_task)
                raise
            finally:
                self.in_progress_count -= 1
                if operation_task.done():
                    if self.outlet_operations.get(operation_key) is operation_task:
                        self.outlet_operations.pop(operation_key, None)
                    self.active_outlets.pop(name, None)

    def _track_draining_operation(
        self, operation_key: str, name: str, task: asyncio.Task
    ) -> None:
        """Keep a timed-out/cancelled ODBC wrapper visible until its worker exits."""
        self.draining_outlets[operation_key] = task
        active = self.active_outlets.get(name)
        if active is not None:
            active["state"] = "draining"
            active["timed_out_at"] = datetime.now().isoformat()

        def _drained(completed_task: asyncio.Task) -> None:
            if self.draining_outlets.get(operation_key) is completed_task:
                self.draining_outlets.pop(operation_key, None)
            if self.outlet_operations.get(operation_key) is completed_task:
                self.outlet_operations.pop(operation_key, None)
            self.active_outlets.pop(name, None)
            try:
                completed_task.result()
            except asyncio.CancelledError:
                logger.warning(f"Draining outlet operation cancelled: {name}")
            except Exception as error:
                logger.error(f"Draining outlet operation finished with error for {name}: {error}")
            else:
                logger.info(f"Draining outlet operation finished: {name}")

        task.add_done_callback(_drained)

    async def _sync_outlet_with_semaphore(self, outlet: dict) -> dict:
        """Run a full-cycle outlet sync and update cycle/retry counters."""
        try:
            result = await self._run_outlet_with_watchdog(outlet)
            if result["status"] == "Success":
                self.completed_count += 1
            elif result["status"] == "Partial":
                self.completed_count += 1
                self.audit_failure_count += 1
            else:
                self.failed_count += 1
                # Queue for retry
                self.retry_queue.add(outlet, result["remarks"], attempt=1)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.failed_count += 1
            error_msg = str(e)
            logger.error(f"Unexpected error syncing {outlet.get('Outlet')}: {error_msg}")
            self.retry_queue.add(outlet, error_msg, attempt=1)
            return self._failure_result(outlet, error_msg)

    @staticmethod
    def _failure_result(outlet: dict, error_msg: str) -> dict:
        return {
            "outlet_code": outlet.get("Outlet", "Unknown"),
            "ip": outlet.get("Server", "N/A"),
            "status": "N",
            "remarks": error_msg,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": 0.0,
            "run_id": outlet.get("_run_id") or str(uuid4()),
            "trigger": outlet.get("_trigger", "unknown"),
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
            "ho_ack_status": "NotAttempted",
            "ho_ack_count": 0,
        }

    @staticmethod
    def _excluded_outlet_codes() -> set[str]:
        return {
            code.strip().upper()
            for code in settings.EXCLUDED_OUTLETS.split(",")
            if code.strip()
        }

    async def run_full_sync(self, trigger: str = "manual") -> dict:
        """
        Execute a full sync cycle across all outlets.
        Returns a summary dict. Only one cycle can run at a time.
        """
        if self.sync_lock.locked():
            active = ", ".join(sorted(self.active_outlets)) or "unknown"
            logger.warning(
                f"Rejected {trigger} sync trigger: a cycle is already running; "
                f"active outlets: {active}"
            )
            return {"error": "Sync cycle already in progress", "status": "conflict"}

        async with self.sync_lock:
            self.is_running = True
            self.current_trigger = trigger
            self.sync_started_at = datetime.now()
            self.sync_finished_at = None
            self.completed_count = 0
            self.failed_count = 0
            self.in_progress_count = 0
            self.total_outlets = 0
            self.excluded_count = 0
            self.cancelled_count = 0
            self.audit_failure_count = 0
            self.current_run_id = str(uuid4())
            self.active_outlets.clear()
            self.outlet_tasks.clear()
            self.outlet_results = []
            cycle_timed_out = False
            cycle_outcome = "running"
            self.total_syncs_attempted += 1

            logger.info(
                f"=== Starting full sync cycle RunId={self.current_run_id} "
                f"(trigger: {trigger}) ==="
            )

            try:
                # Health check on both source (outlet data) and log databases
                source_ok, log_ok = await self._check_central_db_health()
                if not source_ok:
                    logger.error("Source database unreachable - aborting sync cycle")
                    cycle_outcome = "db_unreachable"
                    return {
                        "error": "Source database unreachable",
                        "status": "db_unreachable",
                    }
                if not log_ok:
                    logger.warning(
                        "Log database unreachable - sync will proceed but logging will fail"
                    )

                # Load outlets from central DB
                outlet_load_timeout = settings.CONNECT_TIMEOUT + settings.QUERY_TIMEOUT
                df_outlets = await asyncio.wait_for(
                    load_outlet_data(),
                    timeout=outlet_load_timeout,
                )
                outlets = build_outlet_list(df_outlets)

                if self.cancel_requested.is_set():
                    cycle_outcome = "cancelled"
                    return {"status": "cancelled", "trigger": trigger}

                if not outlets:
                    logger.warning("No valid outlets found, aborting sync cycle")
                    cycle_outcome = "empty"
                    return {"error": "No valid outlets found", "status": "empty"}

                self.total_outlets = len(outlets)
                excluded_codes = self._excluded_outlet_codes()
                excluded = [
                    outlet for outlet in outlets
                    if outlet["Outlet"].upper() in excluded_codes
                ]
                outlets = [
                    outlet for outlet in outlets
                    if outlet["Outlet"].upper() not in excluded_codes
                ]
                outlets = [
                    {**outlet, "_run_id": self.current_run_id, "_trigger": trigger}
                    for outlet in outlets
                ]
                self.excluded_count = len(excluded)
                for outlet in excluded:
                    self.outlet_results.append({
                        "outlet_code": outlet["Outlet"],
                        "ip": outlet["Server"],
                        "status": "Excluded",
                        "remarks": "Temporarily excluded by EXCLUDED_OUTLETS",
                        "timestamp": datetime.now().isoformat(),
                        "duration_seconds": 0.0,
                    })
                if excluded:
                    logger.warning(
                        "Skipping configured outlets: "
                        + ", ".join(o["Outlet"] for o in excluded)
                    )
                logger.info(
                    f"Processing {len(outlets)} of {self.total_outlets} outlets with max "
                    f"{settings.MAX_CONCURRENT_SYNCS} concurrent"
                )

                # Dispatch all outlets through semaphore-bounded tasks
                task_outlets = {
                    asyncio.create_task(self._sync_outlet_with_semaphore(outlet)): outlet
                    for outlet in outlets
                }
                self.outlet_tasks = task_outlets

                # A cycle-level watchdog is a final safety net in addition to
                # the ODBC statement and per-outlet timeouts.
                done, pending = await asyncio.wait(
                    task_outlets,
                    timeout=settings.FULL_SYNC_TIMEOUT,
                )
                if pending:
                    cycle_timed_out = True
                    blockers = ", ".join(sorted(self.active_outlets)) or "unknown"
                    logger.error(
                        f"Full-cycle watchdog timed out after {settings.FULL_SYNC_TIMEOUT} "
                        f"seconds; active outlets: {blockers}; cancelling {len(pending)} tasks"
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                for task in done:
                    outlet = task_outlets[task]
                    if task.cancelled():
                        self.cancelled_count += 1
                        self.outlet_results.append(
                            self._cancelled_result(outlet, "Cancelled before outlet sync started")
                        )
                        continue
                    try:
                        r = task.result()
                    except Exception as task_error:
                        r = task_error
                    if isinstance(r, Exception):
                        logger.error(f"Task exception: {r}")
                    elif isinstance(r, dict):
                        self.outlet_results.append(r)

                for task in pending:
                    outlet = task_outlets[task]
                    if self.cancel_requested.is_set():
                        self.cancelled_count += 1
                        self.outlet_results.append(
                            self._cancelled_result(outlet, "Cancelled by operator")
                        )
                        continue
                    error_msg = (
                        f"Full-cycle watchdog timed out after {settings.FULL_SYNC_TIMEOUT} seconds"
                    )
                    self.failed_count += 1
                    self.retry_queue.add(outlet, error_msg, attempt=1)
                    self.outlet_results.append(self._failure_result(outlet, error_msg))

            except asyncio.CancelledError:
                cycle_outcome = "cancelled"
                logger.warning("Sync cycle cancelled during runtime")
                raise
            except Exception as e:
                cycle_outcome = "error"
                logger.error(f"Fatal error in sync cycle: {e}")
                return {"error": str(e), "status": "error"}

            finally:
                self.is_running = False
                self.outlet_tasks.clear()
                self.sync_finished_at = datetime.now()
                duration = (self.sync_finished_at - self.sync_started_at).total_seconds()
                if self.cancel_requested.is_set():
                    cycle_outcome = "cancelled"
                elif cycle_timed_out:
                    cycle_outcome = "timed_out"
                elif cycle_outcome not in {"db_unreachable", "empty", "error"}:
                    cycle_outcome = (
                        "partial_failure"
                        if self.failed_count or self.audit_failure_count
                        else "completed"
                    )
                self.last_cycle_outcome = cycle_outcome
                if cycle_outcome == "completed":
                    self.total_syncs_completed += 1
                elif cycle_outcome == "partial_failure":
                    self.total_syncs_partial_failure += 1
                elif cycle_outcome == "cancelled":
                    self.total_syncs_cancelled += 1
                elif cycle_outcome == "timed_out":
                    self.total_syncs_timed_out += 1
                else:
                    self.total_syncs_failed += 1
                logger.info(
                    f"=== Sync cycle completed: {self.completed_count} success, "
                    f"{self.failed_count} failed, {duration:.1f}s total ==="
                )
                try:
                    await self.persist_retry_queue()
                except Exception as retry_state_error:
                    logger.error(f"Retry queue persistence failed: {retry_state_error}")
                try:
                    await save_cycle_summary({
                        "run_id": self.current_run_id, "trigger": self.current_trigger,
                        "started_at": self.sync_started_at, "finished_at": self.sync_finished_at,
                        "outcome": cycle_outcome, "total_outlets": self.total_outlets,
                        "successful": self.completed_count, "failed": self.failed_count,
                        "excluded": self.excluded_count, "cancelled": self.cancelled_count,
                        "audit_failed": self.audit_failure_count,
                        "retry_queue_size": self.retry_queue.size,
                        "duration_seconds": round(duration, 2),
                    })
                except Exception as cycle_state_error:
                    logger.error(f"Cycle summary persistence failed: {cycle_state_error}")

            return {
                "status": cycle_outcome,
                "trigger": trigger,
                "run_id": self.current_run_id,
                "total": self.total_outlets,
                "successful": self.completed_count,
                "failed": self.failed_count,
                "excluded": self.excluded_count,
                "cancelled": self.cancelled_count,
                "audit_failed": self.audit_failure_count,
                "duration_seconds": round(duration, 2),
                "retry_queue_size": self.retry_queue.pending_count,
            }

    async def sync_single_outlet(self, outlet_code: str) -> dict:
        """
        Sync a specific outlet by code. Looks up the outlet from central DB.
        """
        logger.info(f"Manual sync requested for outlet {outlet_code}")
        try:
            outlet_load_timeout = settings.CONNECT_TIMEOUT + settings.QUERY_TIMEOUT
            df_outlets = await asyncio.wait_for(
                load_outlet_data(),
                timeout=outlet_load_timeout,
            )
            outlets = build_outlet_list(df_outlets)

            target = None
            for o in outlets:
                if o["Outlet"].upper() == outlet_code.upper():
                    target = o
                    break

            if not target:
                return {"error": f"Outlet {outlet_code} not found", "status": "not_found"}

            target = {**target, "_run_id": str(uuid4()), "_trigger": "single"}
            result = await self._run_outlet_with_watchdog(target)
            # Update in results list if exists
            self.outlet_results = [
                r for r in self.outlet_results
                if r["outlet_code"].upper() != outlet_code.upper()
            ]
            self.outlet_results.append(result)
            # Remove from retry queue on success
            if result["status"] == "Success":
                self.retry_queue.remove(outlet_code)
                await self.persist_retry_queue()
            return result

        except Exception as e:
            logger.error(f"Error in single outlet sync for {outlet_code}: {e}")
            return {"error": str(e), "status": "error"}

    async def retry_single_outlet(self, entry) -> None:
        """
        Retry a single outlet from the retry queue.
        Called by the retry processor.
        """
        outlet = entry.outlet
        name = entry.outlet_code
        active_operation = self.outlet_operations.get(name.upper())
        if active_operation is not None and not active_operation.done():
            logger.info(
                f"Retry deferred for {name}: previous database operation is still active"
            )
            return
        attempt = entry.attempt + 1

        if attempt > settings.RETRY_MAX_ATTEMPTS:
            logger.warning(
                f"Retry suppressed for {name}: max attempts already reached "
                f"({entry.attempt}/{settings.RETRY_MAX_ATTEMPTS})"
            )
            self.retry_queue.add(outlet, entry.last_error, attempt=entry.attempt)
            await self.persist_retry_queue()
            return

        logger.info(f"Retrying outlet {name} (attempt {attempt}/{settings.RETRY_MAX_ATTEMPTS})")

        try:
            outlet = {**outlet, "_run_id": str(uuid4()), "_trigger": "retry"}
            result = await self._run_outlet_with_watchdog(outlet)

            if result["status"] in {"Success", "Partial"}:
                logger.info(f"Retry successful for {name} on attempt {attempt}")
                self.retry_queue.remove(name)
                # Update results
                self.outlet_results = [
                    r for r in self.outlet_results
                    if r["outlet_code"].upper() != name.upper()
                ]
                self.outlet_results.append(result)
                self.completed_count += 1
                self.failed_count = max(0, self.failed_count - 1)
            else:
                # Re-queue with incremented attempt
                self.retry_queue.add(outlet, result["remarks"], attempt=attempt)

        except Exception as e:
            logger.error(f"Error during retry for {name}: {e}")
            self.retry_queue.add(outlet, str(e), attempt=attempt)
        finally:
            await self.persist_retry_queue()

    def stop(self) -> None:
        """Signal graceful shutdown."""
        self.stopped = True
        self.cancel_requested.set()
        for task, outlet in list(self.outlet_tasks.items()):
            if outlet.get("Outlet") not in self.active_outlets and not task.done():
                task.cancel()
        logger.info("SyncManager shutdown signal received")

    @staticmethod
    def _cancelled_result(outlet: dict, remarks: str) -> dict:
        return {
            "outlet_code": outlet.get("Outlet", "Unknown"),
            "ip": outlet.get("Server", "N/A"),
            "status": "Cancelled",
            "remarks": remarks,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": 0.0,
            "run_id": outlet.get("_run_id") or str(uuid4()),
            "trigger": outlet.get("_trigger", "unknown"),
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
            "ho_ack_status": "NotAttempted",
            "ho_ack_count": 0,
        }

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.events import EVENT_JOB_MAX_INSTANCES
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logger import get_logger, setup_logging
from app.db_logger import cleanup_price_changes, initialize_audit_schema
from app.retry_queue import start_retry_processor
from app.routers import cleanup, retry, settings as settings_router, status, sync
from app.scheduling import DayWiseScheduleTrigger, VisualScheduleTrigger
from app.state import get_persisted_schedule, initialize_state, is_schedule_enabled
from app.sync_manager import SyncManager

logger = get_logger(__name__)

# Singleton instances
sync_manager = SyncManager()
scheduler = AsyncIOScheduler()


def _configure_thread_pool() -> ThreadPoolExecutor:
    """Install an explicitly sized executor for blocking ODBC operations."""
    executor = ThreadPoolExecutor(
        max_workers=settings.THREAD_POOL_MAX_WORKERS,
        thread_name_prefix="product-sync-odbc",
    )
    asyncio.get_running_loop().set_default_executor(executor)
    return executor


def _is_visual_schedule_active(persisted: dict, now: datetime) -> bool:
    """Return whether ``now`` is inside the persisted day/time window."""
    days = {str(day).lower() for day in persisted.get("active_days", [])}
    start = int(persisted.get("active_hours_start", 0)) * 60 + int(
        persisted.get("active_minutes_start", 0)
    )
    end = int(persisted.get("active_hours_end", 23)) * 60 + int(
        persisted.get("active_minutes_end", 0)
    )
    current = now.hour * 60 + now.minute
    current_day = now.strftime("%a").lower()
    if start <= end:
        return current_day in days and start <= current <= end
    if current >= start:
        return current_day in days
    previous_day = (now.weekday() - 1) % 7
    previous_name = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[previous_day]
    return current <= end and previous_name in days


def _is_daywise_schedule_active(persisted: dict, now: datetime) -> bool:
    """Return whether any enabled day-wise rule owns the current window."""
    for rule in persisted.get("schedule_rules", []):
        if rule.get("enabled", True) and _is_visual_schedule_active(
            {
                "active_days": [rule.get("day")],
                "active_hours_start": rule.get("active_hours_start", 0),
                "active_minutes_start": rule.get("active_minutes_start", 0),
                "active_hours_end": rule.get("active_hours_end", 23),
                "active_minutes_end": rule.get("active_minutes_end", 0),
            },
            now,
        ):
            return True
    return False


async def scheduled_sync():
    """Called by APScheduler on interval or cron trigger."""
    persisted = get_persisted_schedule()
    if persisted and persisted.get("schedule_mode") == "visual":
        now = datetime.now()
        if not _is_visual_schedule_active(persisted, now):
            logger.debug("Scheduled sync skipped outside configured visual active window")
            return
    if persisted and persisted.get("schedule_mode") == "daywise":
        if not _is_daywise_schedule_active(persisted, datetime.now()):
            logger.debug("Scheduled sync skipped outside configured day-wise windows")
            return
    logger.info("Scheduled sync cycle triggered")
    task = sync_manager.start_full_sync(trigger="scheduled")
    if task is None:
        logger.warning("Scheduled sync skipped because another cycle is active")
        return
    await task


def _log_scheduler_event(event) -> None:
    """Make max-instance skips visible with the currently blocking outlets."""
    active = ", ".join(sorted(sync_manager.active_outlets)) or "unknown"
    logger.warning(
        f"Scheduler skipped job {event.job_id}: maximum running instances reached; "
        f"active outlets: {active}"
    )


def _build_schedule_job_kwargs(persisted: dict | None) -> dict:
    """
    Build APScheduler add_job kwargs from persisted state or .env defaults.
    Returns dict with 'trigger' and optionally 'minutes' or CronTrigger.
    """
    if persisted is None:
        # No dashboard override - use .env default interval
        return {
            "trigger": "interval",
            "minutes": settings.SYNC_INTERVAL_MINUTES,
            "_description": f"Every {settings.SYNC_INTERVAL_MINUTES} minutes (from .env default)",
        }

    mode = persisted.get("schedule_mode")

    if mode == "cron":
        cron_expr = persisted.get("cron_expression", "")
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            return {
                "trigger": CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                ),
                "_description": f"Cron: {cron_expr} (restored from dashboard)",
            }
        # Fallback if cron expression is invalid
        logger.warning(f"Invalid persisted cron expression '{cron_expr}', falling back to .env default")
        return {
            "trigger": "interval",
            "minutes": settings.SYNC_INTERVAL_MINUTES,
            "_description": f"Every {settings.SYNC_INTERVAL_MINUTES} minutes (fallback)",
        }

    if mode == "daywise":
        rules = persisted.get("schedule_rules", [])
        if rules:
            return {
                "trigger": DayWiseScheduleTrigger(rules=rules, timezone=scheduler.timezone),
                "_description": f"Day-wise schedule with {len(rules)} active rule(s) (restored from dashboard)",
            }
        logger.warning("Persisted day-wise schedule has no active rules; falling back to .env default")
        return {
            "trigger": "interval",
            "minutes": settings.SYNC_INTERVAL_MINUTES,
            "_description": f"Every {settings.SYNC_INTERVAL_MINUTES} minutes (fallback)",
        }

    # Visual mode
    interval = persisted.get("interval_minutes") or settings.SYNC_INTERVAL_MINUTES
    h_start = persisted.get("active_hours_start", 0)
    m_start = persisted.get("active_minutes_start", 0)
    h_end = persisted.get("active_hours_end", 23)
    m_end = persisted.get("active_minutes_end", 0)
    days = persisted.get("active_days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    time_range = f"{int(h_start):02d}:{int(m_start):02d}-{int(h_end):02d}:{int(m_end):02d}"
    day_str = ",".join(days)
    return {
        "trigger": VisualScheduleTrigger(
            interval_minutes=interval,
            start_hour=int(h_start),
            start_minute=int(m_start),
            end_hour=int(h_end),
            end_minute=int(m_end),
            active_days=days,
            timezone=scheduler.timezone,
        ),
        "_description": f"Every {interval}min within {time_range} on {day_str} (restored from dashboard)",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle."""
    # --- Startup ---
    setup_logging(settings.LOG_DIR)
    thread_pool = _configure_thread_pool()

    try:
        await initialize_audit_schema()
        logger.info("ProductPrice audit schema verified")
    except Exception as audit_schema_error:
        logger.error(f"ProductPrice audit schema initialization failed: {audit_schema_error}")

    try:
        await initialize_state()
    except Exception as state_error:
        logger.error(f"Database service-state initialization failed; using defaults: {state_error}")

    try:
        await sync_manager.restore_persisted_state()
    except Exception as restore_error:
        logger.error(f"Dashboard/retry state restoration failed: {restore_error}")

    # Build schedule from persisted state or .env defaults
    persisted = get_persisted_schedule()
    job_kwargs = _build_schedule_job_kwargs(persisted)
    description = job_kwargs.pop("_description")

    logger.info("=" * 60)
    logger.info("ProductPriceSync Service starting up")
    logger.info(f"Schedule: {description}")
    logger.info(f"Max concurrent syncs: {settings.MAX_CONCURRENT_SYNCS}")
    logger.info(f"ODBC thread pool workers: {settings.THREAD_POOL_MAX_WORKERS}")
    logger.info(f"Retry max attempts: {settings.RETRY_MAX_ATTEMPTS}")
    logger.info(f"Query timeout: {settings.QUERY_TIMEOUT}s")
    logger.info(f"Outlet watchdog: {settings.OUTLET_SYNC_TIMEOUT}s")
    logger.info(f"Full-cycle watchdog: {settings.FULL_SYNC_TIMEOUT}s")
    logger.info(f"Excluded outlets: {settings.EXCLUDED_OUTLETS or 'none'}")
    logger.info("=" * 60)

    # Inject sync_manager into routers
    sync.sync_manager = sync_manager
    status.sync_manager = sync_manager
    status.scheduler = scheduler
    retry.sync_manager = sync_manager
    settings_router.sync_manager = sync_manager
    settings_router.scheduler = scheduler

    # Start APScheduler with restored or default schedule
    scheduler.add_listener(_log_scheduler_event, EVENT_JOB_MAX_INSTANCES)
    scheduler.add_job(
        scheduled_sync,
        id="sync_cycle",
        name="Full Sync Cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        **job_kwargs,
    )
    scheduler.start()

    if not is_schedule_enabled():
        scheduler.pause_job("sync_cycle")
        logger.warning("Scheduled sync cycles are paused by persisted service state")

    job = scheduler.get_job("sync_cycle")
    if job and job.next_run_time:
        logger.info(f"Scheduler started - next run at {job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        logger.info("Scheduler started")

    # Start retry processor in background
    retry_task = asyncio.create_task(start_retry_processor(sync_manager))

    # Schedule daily price change log cleanup at midnight
    scheduler.add_job(
        cleanup_price_changes,
        id="price_change_cleanup",
        name="Price Change Log Cleanup",
        trigger="cron",
        hour=0,
        minute=0,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        f"Price change cleanup scheduled daily at midnight "
        f"(retention: {settings.PRICE_CHANGE_RETENTION_DAYS} days)"
    )

    yield

    # --- Shutdown ---
    logger.info("Shutting down ProductPriceSync Service...")
    scheduler.shutdown(wait=False)
    sync_manager.stop()
    retry_task.cancel()
    try:
        await retry_task
    except asyncio.CancelledError:
        pass
    thread_pool.shutdown(wait=False, cancel_futures=False)
    logger.info("Shutdown complete")


app = FastAPI(
    title="ProductPriceSync Service",
    description="Production-grade service for synchronizing Head Office Product / ProductPrice / BarCode data to Outlet servers",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/ProductSync/static", StaticFiles(directory=str(static_dir)), name="static")

# Include routers
app.include_router(sync.router)
app.include_router(status.router)
app.include_router(retry.router)
app.include_router(settings_router.router)
app.include_router(cleanup.router)


@app.get("/ProductSync/Dashboard", include_in_schema=False)
async def dashboard():
    """Serve the dashboard HTML page."""
    return FileResponse(str(static_dir / "dashboard.html"))

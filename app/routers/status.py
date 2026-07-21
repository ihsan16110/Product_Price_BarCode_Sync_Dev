import asyncio
import os
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.config import settings
from app.database import make_connection
from app.db_logger import _ensure_price_change_table
from app.logger import get_logger
from app.state import is_schedule_enabled
from app.security import require_viewer
from app.scheduling import DayWiseScheduleTrigger, VisualScheduleTrigger

router = APIRouter(prefix="/ProductSync/api", tags=["status"])
logger = get_logger(__name__)

# Injected at startup
sync_manager = None
scheduler = None


@router.get("/health")
async def health_check():
    """Simple health check endpoint for Docker healthcheck."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@router.get("/status")
async def get_service_status():
    """Get overall service status including current sync cycle info."""
    if sync_manager is None:
        return {"status": "initializing"}

    next_run = None
    schedule_enabled = is_schedule_enabled()
    schedule_info = {
        "enabled": schedule_enabled,
        "mode": "interval",
        "description": f"Every {settings.SYNC_INTERVAL_MINUTES} min",
    }
    if scheduler:
        job = scheduler.get_job("sync_cycle")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
        if job and job.trigger:
            trigger = job.trigger
            if isinstance(trigger, DayWiseScheduleTrigger):
                schedule_info = {
                    "enabled": schedule_enabled,
                    "mode": "daywise",
                    "rules": trigger.rules,
                    "description": f"Day-wise schedule: {len(trigger.rules)} active day rule(s)",
                }
            elif isinstance(trigger, (IntervalTrigger, VisualScheduleTrigger)):
                mins = int(trigger.interval.total_seconds() / 60)
                schedule_info = {"enabled": schedule_enabled, "mode": "visual", "interval_minutes": mins, "description": f"Every {mins} min"}
            elif isinstance(trigger, CronTrigger):
                fields = {f.name: str(f) for f in trigger.fields}
                cron_str = f"{fields.get('minute', '*')} {fields.get('hour', '*')} {fields.get('day', '*')} {fields.get('month', '*')} {fields.get('day_of_week', '*')}"
                schedule_info = {"enabled": schedule_enabled, "mode": "cron", "cron_expression": cron_str, "description": f"Cron: {cron_str}"}

    return {
        "service": "ProductPriceSync",
        "uptime_seconds": round(
            (datetime.now() - sync_manager.service_started_at).total_seconds(), 1
        ),
        "current_sync": sync_manager.get_status(),
        "schedule": schedule_info,
        "next_scheduled_run": next_run,
        "retry_queue_size": sync_manager.retry_queue.size,
        "total_cycles": sync_manager.total_syncs_attempted,
        "total_syncs_completed": sync_manager.total_syncs_completed,
        "cycle_totals": {
            "attempted": sync_manager.total_syncs_attempted,
            "completed": sync_manager.total_syncs_completed,
            "partial_failure": sync_manager.total_syncs_partial_failure,
            "cancelled": sync_manager.total_syncs_cancelled,
            "timed_out": sync_manager.total_syncs_timed_out,
            "failed": sync_manager.total_syncs_failed,
        },
    }


@router.get("/outlets")
async def get_all_outlet_results():
    """Get all outlet results from the last/current sync cycle."""
    if sync_manager is None:
        return []
    return sync_manager.get_outlet_results()


@router.get("/outlets/{outlet_code}")
async def get_outlet_result(outlet_code: str):
    """Get result for a specific outlet."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    result = sync_manager.get_outlet_result(outlet_code)
    if not result:
        raise HTTPException(status_code=404, detail=f"No result found for outlet {outlet_code}")
    return result


@router.get("/logs", dependencies=[Depends(require_viewer)])
async def list_log_files():
    """List available log files."""
    log_dir = settings.LOG_DIR
    if not os.path.exists(log_dir):
        return {"files": []}

    files = []
    for f in sorted(os.listdir(log_dir), reverse=True):
        if f.startswith("ProductSyncLog") and f.endswith(".log"):
            filepath = os.path.join(log_dir, f)
            stat = os.stat(filepath)
            files.append({
                "filename": f,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files}


@router.get("/logs/archive/{date}", dependencies=[Depends(require_viewer)])
async def get_log_content(date: str):
    """
    Get log content for a specific date (format: YYYY-MM-DD).
    Returns the last 1000 lines by default.
    """
    log_dir = settings.LOG_DIR
    # Try dated file first
    filename = f"ProductSyncLog {date}.log"
    filepath = os.path.join(log_dir, filename)

    # Also check the active base log file (today's logs before rotation)
    base_filepath = os.path.join(log_dir, "ProductSyncLog.log")

    if os.path.exists(filepath):
        target = filepath
    elif date == datetime.now().strftime("%Y-%m-%d") and os.path.exists(base_filepath):
        target = base_filepath
    else:
        raise HTTPException(status_code=404, detail=f"Log file not found for date {date}")

    try:
        with open(target, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Return last 1000 lines
        content = "".join(lines[-1000:])
        return PlainTextResponse(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log file: {e}")


@router.get("/logs/stream", dependencies=[Depends(require_viewer)])
async def stream_logs(request: Request):
    """
    SSE endpoint that streams new log entries as they are written to today's log file.
    The client connects and receives new lines every 2 seconds.
    """
    log_dir = settings.LOG_DIR
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    async def event_generator():
        # Determine the active log file path
        today_str = datetime.now().strftime("%Y-%m-%d")
        dated_file = os.path.join(log_dir, f"ProductSyncLog {today_str}.log")
        base_file = os.path.join(log_dir, "ProductSyncLog.log")
        active_file = dated_file if os.path.exists(dated_file) else base_file

        # Start from the end of the file
        file_position = 0
        if os.path.exists(active_file):
            file_position = os.path.getsize(active_file)

        # Send initial connection event
        yield f"event: connected\ndata: {datetime.now().isoformat()}\n\n"

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Re-check which file is active (may roll over at midnight)
                current_today = datetime.now().strftime("%Y-%m-%d")
                if current_today != today_str:
                    today_str = current_today
                    dated_file = os.path.join(log_dir, f"ProductSyncLog {today_str}.log")
                    active_file = dated_file if os.path.exists(dated_file) else base_file
                    file_position = 0

                if os.path.exists(active_file):
                    current_size = os.path.getsize(active_file)

                    if current_size < file_position:
                        # File was rotated or truncated
                        file_position = 0

                    if current_size > file_position:
                        with open(active_file, "r", encoding="utf-8") as f:
                            f.seek(file_position)
                            new_content = f.read()
                            file_position = f.tell()

                        if new_content:
                            lines = new_content.rstrip("\n").split("\n")
                            for line in lines:
                                # Escape for SSE data format
                                escaped = line.strip()
                                if escaped:
                                    yield f"data: {escaped}\n\n"

            except Exception as e:
                logger.warning(f"SSE log stream error: {e}")
                yield f"event: error\ndata: {e}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/price-changes", dependencies=[Depends(require_viewer)])
async def get_price_changes(
    outlet_code: str | None = None,
    days: int = 7,
    limit: int = 100,
):
    """
    Get price change history from ProductPriceChangeLog.
    Filters by outlet_code (optional) and number of days back (default 7).
    """
    try:
        changes = await _fetch_price_changes(outlet_code, days, limit)
        return {
            "changes": changes,
            "count": len(changes),
            "filters": {
                "outlet_code": outlet_code,
                "days": days,
                "limit": limit,
            },
        }
    except Exception as e:
        logger.error(f"Error fetching price changes: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query price changes: {e}")


def _fetch_price_changes_sync(
    outlet_code: str | None,
    days: int,
    limit: int,
) -> list[dict]:
    """Blocking query against ProductPriceChangeLog in central DB."""
    conn = None
    try:
        conn = make_connection(
            server=settings.LOG_SERVER,
            database=settings.LOG_DATABASE,
            user=settings.LOG_USER,
            password=settings.LOG_PASSWORD,
            autocommit=True,
        )
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_price_change_table(cursor)

        where_clauses = ["ChangeOccurrenceTime >= DATEADD(DAY, -?, GETDATE())"]
        params: list = [days]

        if outlet_code:
            where_clauses.append("OutletCode = ?")
            params.append(outlet_code)

        where_sql = " AND ".join(where_clauses)

        cursor.execute(
            f"""
            SELECT TOP (?)
                LogID,
                EventID,
                RunID,
                ChangeType,
                ProductCode,
                DepotCode,
                PreviousUnitPrice,
                PreviousModifiedDate,
                CurrentUnitPrice,
                CurrentModifiedDate,
                ChangeOccurrenceTime,
                OutletCode,
                ChangedBy
            FROM ProductPriceChangeLog
            WHERE {where_sql}
            ORDER BY ChangeOccurrenceTime DESC
            """,
            limit,
            *params,
        )

        rows = cursor.fetchall()
        changes = []
        for row in rows:
            changes.append({
                "log_id": row[0],
                "event_id": str(row[1]),
                "run_id": str(row[2]) if row[2] else None,
                "change_type": row[3],
                "product_code": row[4],
                "depot_code": row[5],
                "previous_unit_price": float(row[6]) if row[6] is not None else None,
                "previous_modified_date": row[7].isoformat() if row[7] else None,
                "current_unit_price": float(row[8]),
                "current_modified_date": row[9].isoformat() if row[9] else None,
                "change_occurrence_time": row[10].isoformat() if row[10] else None,
                "outlet_code": row[11],
                "changed_by": row[12],
            })
        return changes

    except Exception as e:
        logger.error(f"Error in _fetch_price_changes_sync: {e}")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


async def _fetch_price_changes(
    outlet_code: str | None,
    days: int,
    limit: int,
) -> list[dict]:
    """Async wrapper for fetching price changes from central DB."""
    return await asyncio.to_thread(
        _fetch_price_changes_sync, outlet_code, days, limit
    )

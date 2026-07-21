from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.logger import get_logger
from app.models import ScheduleUpdateRequest
from app.scheduling import DayWiseScheduleTrigger, VisualScheduleTrigger
from app.security import limit_expensive_operation, require_operator
from app.state import get_persisted_schedule, is_schedule_enabled, set_persisted_schedule, set_schedule_enabled

router = APIRouter(prefix="/ProductSync/api/settings", tags=["settings"])
logger = get_logger(__name__)

# Injected at startup
sync_manager = None
scheduler = None

# Valid intervals for visual mode
VALID_INTERVALS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120]

# Day name mapping for APScheduler cron
DAY_MAP = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}


def _get_current_schedule_info() -> dict:
    """Read the current schedule from APScheduler job."""
    info = {
        "enabled": is_schedule_enabled(),
        "mode": "visual",
        "interval_minutes": settings.SYNC_INTERVAL_MINUTES,
        "active_hours_start": 0,
        "active_minutes_start": 0,
        "active_hours_end": 23,
        "active_minutes_end": 0,
        "active_days": list(DAY_MAP.keys()),
        "cron_expression": None,
        "description": "",
    }

    if not scheduler:
        return info

    persisted = get_persisted_schedule()
    if persisted and persisted.get("schedule_mode") == "daywise":
        rules = persisted.get("schedule_rules", [])
        info.update({
            "mode": "daywise",
            "rules": rules,
            "description": f"Day-wise schedule: {len(rules)} active day rule(s)",
        })
        return info

    job = scheduler.get_job("sync_cycle")
    if not job:
        return info

    trigger = job.trigger
    if isinstance(trigger, (IntervalTrigger, VisualScheduleTrigger)):
        mins = int(trigger.interval.total_seconds() / 60)
        info["interval_minutes"] = mins
        info["mode"] = "visual"
        if persisted and persisted.get("schedule_mode") == "visual":
            info["active_hours_start"] = persisted.get("active_hours_start", 0)
            info["active_minutes_start"] = persisted.get("active_minutes_start", 0)
            info["active_hours_end"] = persisted.get("active_hours_end", 23)
            info["active_minutes_end"] = persisted.get("active_minutes_end", 0)
            info["active_days"] = persisted.get("active_days", list(DAY_MAP))
            info["description"] = (
                f"Every {mins} minutes within "
                f"{info['active_hours_start']:02d}:{info['active_minutes_start']:02d}-"
                f"{info['active_hours_end']:02d}:{info['active_minutes_end']:02d} "
                f"on {','.join(info['active_days'])}"
            )
        else:
            info["description"] = f"Every {mins} minutes, all day, every day"
    elif isinstance(trigger, CronTrigger):
        info["mode"] = "cron"
        # Reconstruct cron expression from trigger fields
        fields = {f.name: str(f) for f in trigger.fields}
        cron_str = f"{fields.get('minute', '*')} {fields.get('hour', '*')} {fields.get('day', '*')} {fields.get('month', '*')} {fields.get('day_of_week', '*')}"
        info["cron_expression"] = cron_str
        info["description"] = f"Cron: {cron_str}"

    return info


@router.post("/schedule/pause", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def pause_schedule():
    """Pause future scheduled sync cycles without stopping the API service."""
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    job = scheduler.get_job("sync_cycle")
    if not job:
        raise HTTPException(status_code=500, detail="Scheduler job not found")

    await set_schedule_enabled(False)
    scheduler.pause_job("sync_cycle")
    logger.warning("Scheduled sync cycles paused by operator")
    return {
        "message": "Scheduled sync cycles paused",
        "enabled": False,
        "next_run": None,
    }


@router.post("/schedule/resume", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def resume_schedule():
    """Resume future scheduled sync cycles."""
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    job = scheduler.get_job("sync_cycle")
    if not job:
        raise HTTPException(status_code=500, detail="Scheduler job not found")

    await set_schedule_enabled(True)
    scheduler.resume_job("sync_cycle")
    job = scheduler.get_job("sync_cycle")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    logger.info("Scheduled sync cycles resumed by operator")
    return {
        "message": "Scheduled sync cycles resumed",
        "enabled": True,
        "next_run": next_run,
    }


@router.get("")
async def get_settings():
    """Get current service settings including full schedule info."""
    schedule = _get_current_schedule_info()

    return {
        "schedule": schedule,
        "valid_intervals": VALID_INTERVALS,
        "max_concurrent_syncs": settings.MAX_CONCURRENT_SYNCS,
        "thread_pool_max_workers": settings.THREAD_POOL_MAX_WORKERS,
        "price_change_insert_batch_size": settings.PRICE_CHANGE_INSERT_BATCH_SIZE,
        "retry_max_attempts": settings.RETRY_MAX_ATTEMPTS,
        "retry_base_delay_seconds": settings.RETRY_BASE_DELAY,
        "connect_timeout_seconds": settings.CONNECT_TIMEOUT,
        "query_timeout_seconds": settings.QUERY_TIMEOUT,
        "outlet_sync_timeout_seconds": settings.OUTLET_SYNC_TIMEOUT,
        "full_sync_timeout_seconds": settings.FULL_SYNC_TIMEOUT,
        "excluded_outlets": [
            code.strip() for code in settings.EXCLUDED_OUTLETS.split(",") if code.strip()
        ],
    }


@router.put("/schedule", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def update_schedule(request: ScheduleUpdateRequest):
    """
    Update the sync schedule. Supports two modes:

    **Visual mode** (mode="visual"):
    - interval_minutes: 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120
    - active_hours_start / active_hours_end: 0-23
    - active_minutes_start / active_minutes_end: 0 or 30
    - active_days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    **Cron mode** (mode="cron"):
    - cron_expression: standard 5-field cron (minute hour day month day_of_week)
    - Examples: "*/30 6-22 * * *", "0 8,12,18 * * mon-fri"

    Changes take effect immediately and persist across restarts.
    """
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")

    job = scheduler.get_job("sync_cycle")
    if not job:
        raise HTTPException(status_code=500, detail="Scheduler job not found")

    if request.mode == "cron":
        return await _apply_cron_schedule(job, request)
    if request.mode == "daywise":
        return await _apply_daywise_schedule(job, request)
    else:
        return await _apply_visual_schedule(job, request)


def _normalized_daywise_rules(request: ScheduleUpdateRequest) -> list[dict]:
    """Validate and normalize independent weekday rules."""
    valid_days = set(DAY_MAP)
    rules = []
    seen_days = set()
    for model in request.rules:
        rule = model.model_dump()
        day = rule["day"].lower()
        if day not in valid_days:
            raise HTTPException(status_code=400, detail=f"Invalid day '{day}'")
        if day in seen_days:
            raise HTTPException(status_code=400, detail=f"Duplicate rule for '{day}'")
        seen_days.add(day)
        if not rule["enabled"]:
            continue
        if rule["interval_minutes"] not in VALID_INTERVALS:
            raise HTTPException(
                status_code=400,
                detail=f"interval_minutes for {day} must be one of: {VALID_INTERVALS}",
            )
        if not (0 <= rule["active_hours_start"] <= 23 and 0 <= rule["active_hours_end"] <= 23):
            raise HTTPException(status_code=400, detail=f"Hours for {day} must be 0-23")
        if rule["active_minutes_start"] not in (0, 30) or rule["active_minutes_end"] not in (0, 30):
            raise HTTPException(status_code=400, detail=f"Minutes for {day} must be 0 or 30")
        rule["day"] = day
        rules.append(rule)
    if not rules:
        raise HTTPException(status_code=400, detail="Enable at least one day-wise schedule rule")
    return rules


async def _apply_daywise_schedule(job, request: ScheduleUpdateRequest) -> dict:
    """Apply independent interval/time windows for individual weekdays."""
    rules = _normalized_daywise_rules(request)
    trigger = DayWiseScheduleTrigger(rules=rules, timezone=scheduler.timezone)
    await set_persisted_schedule(mode="daywise", schedule_rules=rules)
    job.reschedule(trigger=trigger)
    current_job = scheduler.get_job("sync_cycle")
    next_run = (
        current_job.next_run_time.isoformat()
        if current_job and current_job.next_run_time else None
    )
    logger.info(f"Schedule updated (day-wise): {len(rules)} active rule(s)")
    return {
        "message": f"Day-wise schedule applied for {len(rules)} active day(s)",
        "mode": "daywise",
        "rules": rules,
        "next_run": next_run,
    }


async def _apply_visual_schedule(job, request: ScheduleUpdateRequest) -> dict:
    """Apply a visual picker schedule (interval + active hours + days)."""
    interval = request.interval_minutes
    if interval is None or interval not in VALID_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"interval_minutes must be one of: {VALID_INTERVALS}",
        )

    h_start = request.active_hours_start
    m_start = request.active_minutes_start
    h_end = request.active_hours_end
    m_end = request.active_minutes_end
    if not (0 <= h_start <= 23 and 0 <= h_end <= 23):
        raise HTTPException(status_code=400, detail="Hours must be 0-23")
    if m_start not in (0, 30) or m_end not in (0, 30):
        raise HTTPException(status_code=400, detail="Minutes must be 0 or 30")

    days = request.active_days
    valid_days = set(DAY_MAP.keys())
    if not days or not all(d.lower() in valid_days for d in days):
        raise HTTPException(status_code=400, detail=f"Invalid days. Use: {list(valid_days)}")

    days_lower = [d.lower() for d in days]
    # Anchor each day's cadence to Active From, so applying or restarting the
    # service cannot shift the first eligible run away from that boundary.
    trigger = VisualScheduleTrigger(
        interval_minutes=interval,
        start_hour=h_start,
        start_minute=m_start,
        end_hour=h_end,
        end_minute=m_end,
        active_days=days_lower,
        timezone=scheduler.timezone,
    )
    time_range = f"{h_start:02d}:{m_start:02d}-{h_end:02d}:{m_end:02d}"
    day_str = ",".join(days_lower)
    cron_expr = None
    description = f"Every {interval}min within {time_range} on {day_str}"

    logger.info(f"Schedule updated (visual): {description}")

    # Commit the database source of truth before changing the live scheduler.
    await set_persisted_schedule(
        mode="visual",
        interval_minutes=interval,
        active_hours_start=h_start,
        active_minutes_start=m_start,
        active_hours_end=h_end,
        active_minutes_end=m_end,
        active_days=days_lower,
        cron_expression=cron_expr,
    )
    job.reschedule(trigger=trigger)

    job = scheduler.get_job("sync_cycle")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

    return {
        "message": description,
        "mode": "visual",
        "interval_minutes": interval,
        "active_hours": f"{h_start:02d}:{m_start:02d} - {h_end:02d}:{m_end:02d}",
        "active_days": days_lower,
        "next_run": next_run,
    }


async def _apply_cron_schedule(job, request: ScheduleUpdateRequest) -> dict:
    """Apply a raw cron expression schedule."""
    cron = request.cron_expression
    if not cron or not cron.strip():
        raise HTTPException(status_code=400, detail="cron_expression is required for cron mode")

    parts = cron.strip().split()
    if len(parts) != 5:
        raise HTTPException(
            status_code=400,
            detail="Cron expression must have 5 fields: minute hour day month day_of_week. Example: '*/30 6-22 * * mon-fri'",
        )

    try:
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")

    # Commit the database source of truth before changing the live scheduler.
    await set_persisted_schedule(mode="cron", cron_expression=cron)
    job.reschedule(trigger=trigger)
    logger.info(f"Schedule updated (cron): {cron}")

    job = scheduler.get_job("sync_cycle")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

    return {
        "message": f"Cron schedule applied: {cron}",
        "mode": "cron",
        "cron_expression": cron,
        "next_run": next_run,
    }

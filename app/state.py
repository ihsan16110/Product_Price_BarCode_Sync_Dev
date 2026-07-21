import asyncio
import json
import os
from threading import Lock

from app.config import settings
from app.db_logger import _ensure_audit_schema_cached, _make_log_connection
from app.logger import get_logger

logger = get_logger(__name__)
STATE_FILE = "data/service_state.json"  # one-time migration source only
_lock = Lock()

_DEFAULT_STATE = {
    "schedule_enabled": True,
    "schedule_mode": None,
    "interval_minutes": None,
    "active_hours_start": 0,
    "active_minutes_start": 0,
    "active_hours_end": 23,
    "active_minutes_end": 0,
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "cron_expression": None,
    "schedule_rules": [],
}
_state = dict(_DEFAULT_STATE)


def _load_db_state_blocking() -> dict:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        cursor.execute("""
            SELECT ScheduleEnabled, ScheduleMode, IntervalMinutes,
                ActiveHoursStart, ActiveMinutesStart, ActiveHoursEnd,
                ActiveMinutesEnd, ActiveDays, CronExpression, IsConfigured,
                ScheduleRulesJson
            FROM dbo.ProductSyncServiceState WHERE StateID = 1
        """)
        row = cursor.fetchone()
        if not row:
            return {**_DEFAULT_STATE, "_configured": False}
        return {
            "schedule_enabled": bool(row[0]), "schedule_mode": row[1],
            "interval_minutes": row[2], "active_hours_start": int(row[3]),
            "active_minutes_start": int(row[4]), "active_hours_end": int(row[5]),
            "active_minutes_end": int(row[6]),
            "active_days": [d for d in str(row[7]).split(",") if d],
            "cron_expression": row[8], "_configured": bool(row[9]),
            "schedule_rules": json.loads(row[10]) if row[10] else [],
        }
    finally:
        if conn is not None:
            conn.close()


def _save_db_state_blocking(state: dict) -> None:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        cursor.execute("""
            UPDATE dbo.ProductSyncServiceState SET
                ScheduleEnabled=?, ScheduleMode=?, IntervalMinutes=?,
                ActiveHoursStart=?, ActiveMinutesStart=?, ActiveHoursEnd=?,
                ActiveMinutesEnd=?, ActiveDays=?, CronExpression=?, ScheduleRulesJson=?,
                IsConfigured=1, UpdatedAt=SYSDATETIME()
            WHERE StateID=1
        """,
            bool(state["schedule_enabled"]), state.get("schedule_mode"),
            state.get("interval_minutes"), state["active_hours_start"],
            state["active_minutes_start"], state["active_hours_end"],
            state["active_minutes_end"], ",".join(state["active_days"]),
            state.get("cron_expression"), json.dumps(state.get("schedule_rules", [])),
        )
    finally:
        if conn is not None:
            conn.close()


def _load_legacy_file() -> dict | None:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                return {**_DEFAULT_STATE, **json.load(file)}
    except Exception as error:
        logger.warning(f"Legacy state migration file could not be read: {error}")
    return None


async def initialize_state() -> dict:
    """Load SQL state and migrate the old JSON file once when SQL is unconfigured."""
    global _state
    db_state = await asyncio.to_thread(_load_db_state_blocking)
    configured = db_state.pop("_configured", False)
    if not configured:
        legacy = _load_legacy_file()
        if legacy:
            await asyncio.to_thread(_save_db_state_blocking, legacy)
            db_state = legacy
            logger.info("Migrated legacy schedule state into ProductSyncServiceState")
    with _lock:
        _state = {**_DEFAULT_STATE, **db_state}
    logger.info("Loaded schedule state from log database")
    return load_state()


def load_state() -> dict:
    """Return a copy of the process cache hydrated from SQL at startup."""
    with _lock:
        return dict(_state)


async def save_state(state: dict) -> None:
    """Persist SQL first, then publish the new process cache."""
    global _state
    normalized = {**_DEFAULT_STATE, **state}
    await asyncio.to_thread(_save_db_state_blocking, normalized)
    with _lock:
        _state = normalized


def get_persisted_schedule() -> dict | None:
    state = load_state()
    return None if state.get("schedule_mode") is None else state


def is_schedule_enabled() -> bool:
    return bool(load_state().get("schedule_enabled", True))


async def set_schedule_enabled(enabled: bool) -> None:
    state = load_state()
    state["schedule_enabled"] = bool(enabled)
    await save_state(state)


async def set_persisted_schedule(
    mode: str,
    interval_minutes: int | None = None,
    active_hours_start: int = 0,
    active_minutes_start: int = 0,
    active_hours_end: int = 23,
    active_minutes_end: int = 0,
    active_days: list[str] | None = None,
    cron_expression: str | None = None,
    schedule_rules: list[dict] | None = None,
) -> None:
    state = load_state()
    state.update({
        "schedule_mode": mode, "interval_minutes": interval_minutes,
        "active_hours_start": active_hours_start,
        "active_minutes_start": active_minutes_start,
        "active_hours_end": active_hours_end,
        "active_minutes_end": active_minutes_end,
        "active_days": active_days or list(_DEFAULT_STATE["active_days"]),
        "cron_expression": cron_expression,
        "schedule_rules": schedule_rules or [],
    })
    await save_state(state)

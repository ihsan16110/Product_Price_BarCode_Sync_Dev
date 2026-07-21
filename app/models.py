from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class OutletInfo(BaseModel):
    outlet_code: str
    server: str
    database: str
    user: str
    password: str


class SyncResult(BaseModel):
    outlet_code: str
    ip: str
    status: str  # "Success" or "N"
    remarks: str
    timestamp: datetime
    duration_seconds: Optional[float] = None
    run_id: Optional[str] = None
    trigger: Optional[str] = None
    captured_count: int = 0
    logged_count: int = 0
    audit_status: str = "NotApplicable"


class SyncCycleStatus(BaseModel):
    state: str  # "idle", "running"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    total_outlets: int = 0
    completed: int = 0
    failed: int = 0
    in_progress: int = 0
    trigger: str = ""  # "scheduled", "manual", ""


class RetryEntry(BaseModel):
    outlet_code: str
    server: str
    attempt: int
    max_attempts: int
    next_retry_at: datetime
    last_error: str
    added_at: datetime


class ServiceStatus(BaseModel):
    service: str = "ProductPriceSync"
    uptime_seconds: float
    current_sync: SyncCycleStatus
    schedule_interval_minutes: int
    next_scheduled_run: Optional[datetime] = None
    retry_queue_size: int
    total_syncs_completed: int


class DayScheduleRule(BaseModel):
    """One calendar day's visual scheduling rule."""
    day: str
    enabled: bool = True
    interval_minutes: int = 120
    active_hours_start: int = 7
    active_minutes_start: int = 0
    active_hours_end: int = 0
    active_minutes_end: int = 30


class ScheduleUpdateRequest(BaseModel):
    """
    Schedule update - supports two modes:
    1. Visual picker: interval_minutes + active hour/minute bounds + active_days
    2. Day-wise picker: one independent rule per weekday
    3. Advanced: raw cron_expression
    """
    mode: str = "visual"  # "visual" or "cron"

    # Visual picker fields
    interval_minutes: Optional[int] = None
    active_hours_start: int = 0   # 0-23
    active_minutes_start: int = 0  # 0 or 30
    active_hours_end: int = 23    # 0-23
    active_minutes_end: int = 0    # 0 or 30
    active_days: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    # Day-wise visual fields
    rules: list[DayScheduleRule] = []

    # Advanced cron field
    cron_expression: Optional[str] = None

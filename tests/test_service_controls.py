"""Regression tests for scheduler pause state and graceful sync cancellation."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app import state
from app.main import _configure_thread_pool, _is_visual_schedule_active
from app.routers import settings as settings_router
from app.models import ScheduleUpdateRequest
from app.scheduling import DayWiseScheduleTrigger, VisualScheduleTrigger
from app.sync_manager import SyncManager


def test_configure_thread_pool_uses_explicit_worker_capacity():
    fake_loop = MagicMock()
    fake_executor = MagicMock()
    with (
        patch("app.main.asyncio.get_running_loop", return_value=fake_loop),
        patch("app.main.ThreadPoolExecutor", return_value=fake_executor) as factory,
    ):
        result = _configure_thread_pool()

    factory.assert_called_once_with(
        max_workers=settings_router.settings.THREAD_POOL_MAX_WORKERS,
        thread_name_prefix="product-sync-odbc",
    )
    fake_loop.set_default_executor.assert_called_once_with(fake_executor)
    assert result is fake_executor


@pytest.mark.asyncio
async def test_schedule_enabled_state_persists(monkeypatch):
    monkeypatch.setattr(state, "_state", dict(state._DEFAULT_STATE))
    db_thread = AsyncMock(return_value=None)
    monkeypatch.setattr(state.asyncio, "to_thread", db_thread)
    assert state.is_schedule_enabled() is True
    await state.set_schedule_enabled(False)
    assert state.is_schedule_enabled() is False
    await state.set_schedule_enabled(True)
    assert state.is_schedule_enabled() is True
    assert db_thread.await_count == 2


def test_visual_schedule_half_hour_and_overnight_windows():
    daytime = {
        "active_hours_start": 12,
        "active_minutes_start": 30,
        "active_hours_end": 21,
        "active_minutes_end": 30,
        "active_days": ["sat"],
    }
    assert not _is_visual_schedule_active(daytime, datetime(2026, 7, 18, 12, 29))
    assert _is_visual_schedule_active(daytime, datetime(2026, 7, 18, 12, 30))
    assert _is_visual_schedule_active(daytime, datetime(2026, 7, 18, 21, 30))
    assert not _is_visual_schedule_active(daytime, datetime(2026, 7, 18, 21, 31))

    overnight = {
        "active_hours_start": 21,
        "active_minutes_start": 30,
        "active_hours_end": 2,
        "active_minutes_end": 30,
        "active_days": ["sat"],
    }
    assert _is_visual_schedule_active(overnight, datetime(2026, 7, 18, 23, 0))
    assert _is_visual_schedule_active(overnight, datetime(2026, 7, 19, 1, 30))
    assert not _is_visual_schedule_active(overnight, datetime(2026, 7, 19, 3, 0))


@pytest.mark.asyncio
async def test_visual_schedule_accepts_new_interval_and_persists_half_hours(monkeypatch):
    class FakeJob:
        next_run_time = None

        def __init__(self):
            self.reschedule_args = None

        def reschedule(self, **kwargs):
            self.reschedule_args = kwargs

    class FakeScheduler:
        def __init__(self, job):
            self.job = job
            self.timezone = ZoneInfo("Asia/Dhaka")

        def get_job(self, _job_id):
            return self.job

    job = FakeJob()
    persisted = {}
    monkeypatch.setattr(settings_router, "scheduler", FakeScheduler(job))
    async def persist_schedule(**kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(settings_router, "set_persisted_schedule", persist_schedule)

    response = await settings_router._apply_visual_schedule(
        job,
        ScheduleUpdateRequest(
            interval_minutes=35,
            active_hours_start=12,
            active_minutes_start=30,
            active_hours_end=21,
            active_minutes_end=30,
            active_days=["sat"],
        ),
    )

    trigger = job.reschedule_args["trigger"]
    assert isinstance(trigger, VisualScheduleTrigger)
    assert trigger.interval.total_seconds() == 35 * 60
    assert persisted["active_minutes_start"] == 30
    assert persisted["active_minutes_end"] == 30
    assert response["active_hours"] == "12:30 - 21:30"


def test_visual_trigger_is_anchored_to_active_from_and_never_reports_early_check():
    timezone = ZoneInfo("Asia/Dhaka")
    trigger = VisualScheduleTrigger(
        interval_minutes=45,
        start_hour=7,
        start_minute=0,
        end_hour=0,
        end_minute=30,
        active_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        timezone=timezone,
    )

    before_window = datetime(2026, 7, 18, 4, 54, tzinfo=timezone)
    assert trigger.get_next_fire_time(None, before_window) == datetime(
        2026, 7, 18, 7, 0, tzinfo=timezone
    )

    during_window = datetime(2026, 7, 18, 7, 1, tzinfo=timezone)
    assert trigger.get_next_fire_time(None, during_window) == datetime(
        2026, 7, 18, 7, 45, tzinfo=timezone
    )

    previous = datetime(2026, 7, 19, 0, 15, tzinfo=timezone)
    assert trigger.get_next_fire_time(previous, previous) == datetime(
        2026, 7, 19, 7, 0, tzinfo=timezone
    )


@pytest.mark.asyncio
async def test_daywise_schedule_applies_independent_rules(monkeypatch):
    class FakeJob:
        next_run_time = None
        def reschedule(self, **kwargs):
            self.trigger = kwargs["trigger"]

    class FakeScheduler:
        timezone = ZoneInfo("Asia/Dhaka")
        def __init__(self, job):
            self.job = job
        def get_job(self, _job_id):
            return self.job

    job = FakeJob()
    persisted = {}
    monkeypatch.setattr(settings_router, "scheduler", FakeScheduler(job))
    async def persist_schedule(**kwargs):
        persisted.update(kwargs)
    monkeypatch.setattr(settings_router, "set_persisted_schedule", persist_schedule)

    response = await settings_router._apply_daywise_schedule(
        job,
        ScheduleUpdateRequest(mode="daywise", rules=[
            {"day": "sun", "interval_minutes": 120, "active_hours_start": 7,
             "active_hours_end": 0, "active_minutes_end": 30},
            {"day": "thu", "interval_minutes": 30, "active_hours_start": 9,
             "active_hours_end": 18},
            {"day": "fri", "enabled": False},
        ]),
    )

    assert isinstance(job.trigger, DayWiseScheduleTrigger)
    assert [rule["day"] for rule in persisted["schedule_rules"]] == ["sun", "thu"]
    assert response["mode"] == "daywise"


def test_daywise_trigger_combines_weekday_cadences_and_overnight_end():
    timezone = ZoneInfo("Asia/Dhaka")
    trigger = DayWiseScheduleTrigger(rules=[
        {"day": "wed", "enabled": True, "interval_minutes": 120,
         "active_hours_start": 7, "active_minutes_start": 0,
         "active_hours_end": 0, "active_minutes_end": 30},
        {"day": "thu", "enabled": True, "interval_minutes": 30,
         "active_hours_start": 9, "active_minutes_start": 0,
         "active_hours_end": 18, "active_minutes_end": 0},
    ], timezone=timezone)

    assert trigger.get_next_fire_time(None, datetime(2026, 7, 22, 22, 45, tzinfo=timezone)) == datetime(
        2026, 7, 22, 23, 0, tzinfo=timezone
    )
    assert trigger.get_next_fire_time(None, datetime(2026, 7, 23, 8, 45, tzinfo=timezone)) == datetime(
        2026, 7, 23, 9, 0, tzinfo=timezone
    )


@pytest.mark.asyncio
async def test_graceful_cancel_stops_pending_outlets_and_drains_active_one():
    manager = SyncManager()
    manager.semaphore = asyncio.Semaphore(1)
    release_active = asyncio.Event()

    outlets = pd.DataFrame(
        [
            {"outletid": "B001", "server": "10.0.0.1"},
            {"outletid": "B002", "server": "10.0.0.2"},
            {"outletid": "B003", "server": "10.0.0.3"},
        ]
    )

    async def successful_but_blocked(outlet):
        await release_active.wait()
        return {
            "outlet_code": outlet["Outlet"],
            "ip": outlet["Server"],
            "status": "Success",
            "remarks": "Y",
            "timestamp": "2026-07-17T00:00:00",
            "duration_seconds": 0.1,
        }

    manager._check_central_db_health = AsyncMock(return_value=(True, True))

    with (
        patch("app.sync_manager.load_outlet_data", AsyncMock(return_value=outlets)),
        patch("app.sync_manager.run_on_outlet", side_effect=successful_but_blocked),
    ):
        cycle_task = manager.start_full_sync(trigger="manual")
        assert cycle_task is not None

        for _ in range(50):
            if len(manager.active_outlets) == 1:
                break
            await asyncio.sleep(0)

        response = manager.request_cancellation()
        assert response["status"] == "stopping"
        assert response["pending_outlets_cancelled"] == 2
        assert manager.get_status()["state"] == "stopping"

        release_active.set()
        summary = await cycle_task

    assert summary["status"] == "cancelled"
    assert summary["successful"] == 1
    assert summary["cancelled"] == 2
    assert manager.get_status()["state"] == "idle"
    assert manager.retry_queue.size == 0


@pytest.mark.asyncio
async def test_start_full_sync_rejects_second_cycle_without_race():
    manager = SyncManager()
    release_cycle = asyncio.Event()

    async def fake_cycle(trigger="manual"):
        await release_cycle.wait()
        return {"status": "completed", "trigger": trigger}

    manager.run_full_sync = fake_cycle
    first = manager.start_full_sync("manual")
    second = manager.start_full_sync("manual")

    assert first is not None
    assert second is None

    release_cycle.set()
    await first

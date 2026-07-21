from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.sync_engine import _extract_price_changes, run_on_outlet


class ResultSetCursor:
    def __init__(self, result_sets):
        self.result_sets = result_sets
        self.index = 0
        self.description = [("column",)] if result_sets else None

    def fetchall(self):
        return self.result_sets[self.index]

    def nextset(self):
        self.index += 1
        if self.index >= len(self.result_sets):
            self.description = None
            return False
        self.description = [("column",)]
        return True


def test_extracts_insert_and_update_events_across_result_sets():
    now = datetime(2026, 7, 18, 10, 30)
    cursor = ResultSetCursor([
        [("PRICE_CHANGE_SUMMARY", 2)],
        [
            ("PRICE_CHANGES", "INSERT", "P001", "B004", None, 10, None, now, "admin"),
            ("PRICE_CHANGES", "UPDATE", "P002", "B004", 20, 25, now, now, "admin"),
        ],
    ])

    changes, captured = _extract_price_changes(cursor)

    assert captured == 2
    assert [change["change_type"] for change in changes] == ["INSERT", "UPDATE"]
    assert changes[0]["old_unit_price"] is None
    assert changes[1]["old_unit_price"] == 20.0
    assert changes[0]["new_modified_date"] is now
    assert changes[1]["old_modified_date"] is now
    assert changes[1]["new_modified_date"] is now
    assert all(change["event_id"] for change in changes)


def test_zero_change_summary_is_explicit_and_valid():
    changes, captured = _extract_price_changes(ResultSetCursor([
        [("PRICE_CHANGE_SUMMARY", 0)],
        [],
    ]))
    assert captured == 0
    assert changes == []


def test_missing_summary_is_rejected():
    with pytest.raises(RuntimeError, match="summary result set"):
        _extract_price_changes(ResultSetCursor([[]]))


def test_count_mismatch_is_rejected():
    with pytest.raises(RuntimeError, match="count mismatch"):
        _extract_price_changes(ResultSetCursor([
            [("PRICE_CHANGE_SUMMARY", 1)],
            [],
        ]))


@pytest.mark.asyncio
async def test_end_to_end_audit_success_updates_counts_and_history():
    result = {
        "outlet_code": "B004", "ip": "10.0.0.4", "status": "Success",
        "remarks": "Y", "timestamp": "2026-07-18T10:30:00",
        "duration_seconds": 1.2, "run_id": "11111111-1111-1111-1111-111111111111",
        "trigger": "single", "captured_count": 1, "logged_count": 0,
        "audit_status": "Pending", "price_changes": [{"event_id": "evt", "change_type": "INSERT"}],
    }
    with (
        patch("app.sync_engine.asyncio.to_thread", AsyncMock(return_value=result)),
        patch("app.sync_engine.log_product_price_changes", AsyncMock(return_value=1)) as log_changes,
        patch("app.sync_engine.update_product_sync_log", AsyncMock(return_value=True)),
        patch("app.sync_engine.log_sync_history", AsyncMock(return_value=True)) as history,
    ):
        actual = await run_on_outlet({"Outlet": "B004"})

    assert actual["status"] == "Success"
    assert actual["audit_status"] == "Logged"
    assert actual["logged_count"] == 1
    log_changes.assert_awaited_once()
    history.assert_awaited_once_with(actual)


@pytest.mark.asyncio
async def test_end_to_end_audit_failure_becomes_partial():
    result = {
        "outlet_code": "B004", "ip": "10.0.0.4", "status": "Success",
        "remarks": "Y", "timestamp": "2026-07-18T10:30:00",
        "duration_seconds": 1.2, "run_id": "11111111-1111-1111-1111-111111111111",
        "trigger": "single", "captured_count": 1, "logged_count": 0,
        "audit_status": "Pending", "price_changes": [{"event_id": "evt", "change_type": "UPDATE"}],
    }
    with (
        patch("app.sync_engine.asyncio.to_thread", AsyncMock(return_value=result)),
        patch("app.sync_engine.log_product_price_changes", AsyncMock(side_effect=Exception("log offline"))),
        patch("app.sync_engine.update_product_sync_log", AsyncMock(return_value=True)),
        patch("app.sync_engine.log_sync_history", AsyncMock(return_value=True)),
    ):
        actual = await run_on_outlet({"Outlet": "B004"})

    assert actual["status"] == "Partial"
    assert actual["audit_status"] == "AuditFailed"
    assert actual["logged_count"] == 0
    assert "log offline" in actual["remarks"]

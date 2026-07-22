from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sync_engine import (
    _acknowledge_ho_blocking,
    _extract_ho_acknowledgements,
    run_on_outlet,
)


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


def test_extracts_ho_acknowledgements_across_result_sets():
    cursor = ResultSetCursor([
        [("HO_ACK_SUMMARY", 2)],
        [
            ("HO_ACKNOWLEDGEMENTS", "P001", "B004"),
            ("HO_ACKNOWLEDGEMENTS", "P002", "B004"),
        ],
    ])

    acknowledgements, expected = _extract_ho_acknowledgements(cursor)

    assert expected == 2
    assert acknowledgements == [("P001", "B004"), ("P002", "B004")]


def test_zero_acknowledgement_summary_is_explicit_and_valid():
    acknowledgements, expected = _extract_ho_acknowledgements(ResultSetCursor([
        [("HO_ACK_SUMMARY", 0)],
        [],
    ]))
    assert expected == 0
    assert acknowledgements == []


def test_missing_acknowledgement_summary_is_rejected():
    with pytest.raises(RuntimeError, match="summary result set"):
        _extract_ho_acknowledgements(ResultSetCursor([[]]))


def test_acknowledgement_count_mismatch_is_rejected():
    with pytest.raises(RuntimeError, match="count mismatch"):
        _extract_ho_acknowledgements(ResultSetCursor([
            [("HO_ACK_SUMMARY", 1)],
            [],
        ]))


def test_empty_acknowledgement_key_is_rejected():
    with pytest.raises(RuntimeError, match="empty key"):
        _extract_ho_acknowledgements(ResultSetCursor([
            [("HO_ACK_SUMMARY", 1)],
            [("HO_ACKNOWLEDGEMENTS", "", "B004")],
        ]))


def test_ho_acknowledgement_is_parameterized_and_committed():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    with patch("app.sync_engine.make_connection", return_value=connection) as connect:
        count = _acknowledge_ho_blocking([
            ("P001", "B004"),
            ("P001", "B004"),
            ("P002", "B004"),
        ])

    assert count == 2
    connect.assert_called_once_with(
        server="test-ho-server",
        database="TestHODB",
        user="test_ho_user",
        password="test_ho_pass",
        autocommit=False,
    )
    sql, params = cursor.executemany.call_args.args
    assert "SET SyncStatus = 'Y', SentTime = GETDATE()" in sql
    assert "ProductCode = ?" in sql
    assert "DepotCode = ?" in sql
    assert params == [("P001", "B004"), ("P002", "B004")]
    connection.commit.assert_called_once_with()
    connection.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_post_commit_ho_acknowledgement_success():
    result = {
        "outlet_code": "B004", "ip": "10.0.0.4", "status": "Success",
        "remarks": "Y", "timestamp": "2026-07-18T10:30:00",
        "duration_seconds": 1.2, "run_id": "11111111-1111-1111-1111-111111111111",
        "trigger": "single", "captured_count": 0, "logged_count": 0,
        "audit_status": "Disabled", "ho_ack_status": "Pending", "ho_ack_count": 0,
        "ho_acknowledgements": [("P001", "B004")],
    }
    with (
        patch("app.sync_engine.asyncio.to_thread", AsyncMock(side_effect=[result, 1])),
        patch("app.sync_engine.update_product_sync_log", AsyncMock(return_value=True)),
        patch("app.sync_engine.log_sync_history", AsyncMock()) as history,
    ):
        actual = await run_on_outlet({"Outlet": "B004"})

    assert actual["status"] == "Success"
    assert actual["ho_ack_status"] == "Acknowledged"
    assert actual["ho_ack_count"] == 1
    assert "ho_acknowledgements" not in actual
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_ho_acknowledgement_failure_becomes_partial():
    result = {
        "outlet_code": "B004", "ip": "10.0.0.4", "status": "Success",
        "remarks": "Y", "timestamp": "2026-07-18T10:30:00",
        "duration_seconds": 1.2, "run_id": "11111111-1111-1111-1111-111111111111",
        "trigger": "single", "captured_count": 0, "logged_count": 0,
        "audit_status": "Disabled", "ho_ack_status": "Pending", "ho_ack_count": 0,
        "ho_acknowledgements": [("P001", "B004")],
    }
    with (
        patch(
            "app.sync_engine.asyncio.to_thread",
            AsyncMock(side_effect=[result, Exception("HO offline")]),
        ),
        patch("app.sync_engine.update_product_sync_log", AsyncMock(return_value=True)),
    ):
        actual = await run_on_outlet({"Outlet": "B004"})

    assert actual["status"] == "Partial"
    assert actual["ho_ack_status"] == "Failed"
    assert actual["ho_ack_count"] == 0
    assert "HO offline" in actual["remarks"]

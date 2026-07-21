"""
Unit tests for the db_logger module.

Tests cover:
- _insert_price_changes_blocking with various inputs (empty, single, multiple)
- cleanup_price_changes configuration and behavior
- Async wrapper delegation
- Edge cases: empty changes, missing fields, None values
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.db_logger import (
    _make_log_connection,
    _cleanup_price_changes_blocking,
    _ensure_audit_schema_cached,
    _ensure_price_change_table,
    _insert_price_changes_blocking,
    _insert_sync_history_blocking,
    _reset_audit_schema_cache,
    cleanup_price_changes,
    log_product_price_changes,
)
from app.config import settings


@pytest.fixture(autouse=True)
def reset_schema_cache():
    """Keep process-local schema caching deterministic between unit tests."""
    _reset_audit_schema_cache()
    yield
    _reset_audit_schema_cache()


def test_log_connection_uses_dedicated_log_settings():
    """Status and price-audit tables must not use the source DB connection."""
    with patch("app.db_logger.make_connection") as mock_make_connection:
        _make_log_connection()

    mock_make_connection.assert_called_once_with(
        server=settings.LOG_SERVER,
        database=settings.LOG_DATABASE,
        user=settings.LOG_USER,
        password=settings.LOG_PASSWORD,
        autocommit=True,
    )


@patch("app.db_logger.make_connection")
def test_sync_history_binds_attempt_time_as_datetime(mock_make_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_make_connection.return_value = mock_conn

    _insert_sync_history_blocking({
        "run_id": "11111111-1111-1111-1111-111111111111",
        "trigger": "single",
        "outlet_code": "E015",
        "status": "Success",
        "audit_status": "NoChanges",
        "captured_count": 0,
        "logged_count": 0,
        "timestamp": "2026-07-18T04:23:53.183000",
        "duration_seconds": 1.2,
        "remarks": "Y",
    })

    insert_call = next(
        call for call in mock_cursor.execute.call_args_list
        if "INSERT INTO ProductSyncLogHistory" in call.args[0]
    )
    assert insert_call.args[8] == datetime(2026, 7, 18, 4, 23, 53, 183000)
    assert isinstance(insert_call.args[8], datetime)


class TestInsertPriceChanges:
    """Tests for _insert_price_changes_blocking and its async wrapper."""

    def test_empty_changes_returns_zero(self):
        """Empty changes list should return 0 without making a DB call."""
        result = _insert_price_changes_blocking([], "B001")
        assert result == 0

    @patch("app.db_logger.make_connection")
    def test_single_change(self, mock_make_connection):
        """A single price change should be inserted correctly."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        changes = [
            {
                "product_code": "PRD001",
                "depot_code": "B004",
                "old_unit_price": 10.50,
                "new_unit_price": 15.75,
                "old_modified_date": "2026-02-23T10:00:00",
                "new_modified_date": "2026-02-24T14:35:00",
                "modified_by": "admin",
            }
        ]

        result = _insert_price_changes_blocking(changes, "B004")

        assert result == 1
        mock_make_connection.assert_called_once()
        # Verify _ensure_price_change_table was called (table existence check inside IF NOT EXISTS)
        table_create_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "CREATE TABLE ProductPriceChangeLog" in str(c)
        ]
        assert len(table_create_calls) == 1, \
            "_ensure_price_change_table should have been called"

        # Verify one fast batch was sent with one row.
        mock_cursor.executemany.assert_called_once()
        assert mock_cursor.fast_executemany is True
        assert len(mock_cursor.executemany.call_args.args[1]) == 1

    @patch("app.db_logger.make_connection")
    def test_multiple_changes(self, mock_make_connection):
        """Multiple price changes should all be inserted."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        changes = [
            {
                "product_code": "PRD001",
                "depot_code": "B004",
                "old_unit_price": 10.00,
                "new_unit_price": 12.00,
                "old_modified_date": None,
                "new_modified_date": "2026-02-24T10:00:00",
                "modified_by": None,
            },
            {
                "product_code": "PRD002",
                "depot_code": "B004",
                "old_unit_price": 25.00,
                "new_unit_price": 30.00,
                "old_modified_date": "2026-02-23T10:00:00",
                "new_modified_date": "2026-02-24T11:00:00",
                "modified_by": "user1",
            },
        ]

        result = _insert_price_changes_blocking(changes, "B004")

        assert result == 2
        mock_cursor.executemany.assert_called_once()
        assert len(mock_cursor.executemany.call_args.args[1]) == 2

    @patch("app.db_logger.make_connection")
    def test_splits_large_change_set_into_configured_batches(self, mock_make_connection):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [(1,), (1,)]
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn
        changes = [
            {
                "event_id": f"00000000-0000-0000-0000-00000000000{i}",
                "product_code": f"PRD00{i}",
                "depot_code": "B004",
                "old_unit_price": 10.0,
                "new_unit_price": 11.0,
                "old_modified_date": None,
                "new_modified_date": None,
                "modified_by": None,
            }
            for i in range(2)
        ]

        with patch.object(settings, "PRICE_CHANGE_INSERT_BATCH_SIZE", 1):
            result = _insert_price_changes_blocking(changes, "B004")

        assert result == 2
        assert mock_cursor.executemany.call_count == 2

    @patch("app.db_logger.make_connection")
    def test_rejects_duplicate_event_ids(self, mock_make_connection):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        mock_make_connection.return_value = mock_conn
        change = {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "product_code": "PRD001",
            "depot_code": "B004",
            "old_unit_price": 10.0,
            "new_unit_price": 11.0,
            "old_modified_date": None,
            "new_modified_date": None,
            "modified_by": None,
        }

        with pytest.raises(ValueError, match="Duplicate ProductPrice audit EventID"):
            _insert_price_changes_blocking([change, change.copy()], "B004")

    @patch("app.db_logger.make_connection")
    def test_connection_error_propagates(self, mock_make_connection):
        """Audit connection errors must propagate to produce Partial status."""
        mock_make_connection.side_effect = Exception("Connection refused")

        with pytest.raises(Exception, match="Connection refused"):
            _insert_price_changes_blocking(
                [{"product_code": "PRD001", "depot_code": "B004",
                  "old_unit_price": None, "new_unit_price": 10.0,
                  "old_modified_date": None, "new_modified_date": "2026-02-24T10:00:00",
                  "modified_by": None}],
                "B004",
            )

    @patch("app.db_logger.make_connection")
    def test_cursor_error_propagates(self, mock_make_connection):
        """Audit SQL errors must propagate to produce Partial status."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Invalid SQL")
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        changes = [
            {
                "product_code": "PRD001",
                "depot_code": "B004",
                "old_unit_price": 10.0,
                "new_unit_price": 15.0,
                "old_modified_date": None,
                "new_modified_date": "2026-02-24T10:00:00",
                "modified_by": None,
            }
        ]

        with pytest.raises(Exception, match="Invalid SQL"):
            _insert_price_changes_blocking(changes, "B004")

    @patch("app.db_logger.make_connection")
    def test_closes_connection(self, mock_make_connection):
        """Connection should always be closed in the finally block."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        _insert_price_changes_blocking(
            [{"product_code": "PRD001", "depot_code": "B004",
              "old_unit_price": None, "new_unit_price": 10.0,
              "old_modified_date": None, "new_modified_date": "2026-02-24T10:00:00",
              "modified_by": None}],
            "B004",
        )

        mock_conn.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.db_logger.asyncio.to_thread")
    async def test_async_wrapper_delegates(self, mock_to_thread):
        """The async wrapper should delegate to the blocking function."""
        mock_to_thread.return_value = 3

        result = await log_product_price_changes(
            [{"product_code": "PRD001", "depot_code": "B004",
              "old_unit_price": None, "new_unit_price": 10.0,
              "old_modified_date": None, "new_modified_date": "2026-02-24T10:00:00",
              "modified_by": None}],
            "B004",
        )

        assert result == 3
        mock_to_thread.assert_called_once()


class TestCleanupPriceChanges:
    """Tests for _cleanup_price_changes_blocking and its async wrapper."""

    @patch("app.db_logger.make_connection")
    def test_table_not_exists_returns_zero(self, mock_make_connection):
        """If the table doesn't exist yet, cleanup should return 0."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # First query returns 0 (table doesn't exist)
        mock_cursor.fetchone.return_value = [0]
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        result = _cleanup_price_changes_blocking(retention_days=90)

        assert result == 0
        # DELETE should not have been called
        delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "DELETE FROM ProductPriceChangeLog" in str(c[0][0])
        ]
        assert len(delete_calls) == 0

    @patch("app.db_logger.make_connection")
    def test_cleanup_deletes_old_records(self, mock_make_connection):
        """Cleanup should delete records older than retention days in batches."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchone.return_value = [1]
        # First DELETE batch returns 60, which is < 5000 → loop breaks after one round
        mock_cursor.rowcount = 60
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        result = _cleanup_price_changes_blocking(retention_days=90)

        assert result == 60

    @patch("app.db_logger.make_connection")
    def test_batched_multiple_rounds(self, mock_make_connection):
        """When rowcount == batch_size, loop runs multiple rounds."""
        mock_conn = MagicMock()

        # Use a custom class instead of MagicMock to avoid polluting the
        # MagicMock class with a class-level property descriptor.
        class _BatchCursor:
            def __init__(self):
                self.rowcount_values = [10, 10, 3]
                self.fetchone = MagicMock(return_value=[1])
                self.execute = MagicMock()

            @property
            def rowcount(self):
                return self.rowcount_values.pop(0) if self.rowcount_values else 0

        mock_cursor = _BatchCursor()
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        result = _cleanup_price_changes_blocking(retention_days=90, batch_size=10)

        assert result == 23  # 10 + 10 + 3

    @patch("app.db_logger.make_connection")
    def test_cleanup_default_retention(self, mock_make_connection):
        """When no retention_days passed, should use settings default."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchone.return_value = [1]
        mock_cursor.rowcount = 10
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        result = _cleanup_price_changes_blocking()

        assert result == 10
        delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "DELETE" in str(c[0][0]) and "ProductPriceChangeLog" in str(c[0][0])
        ]
        assert len(delete_calls) == 1

    @patch("app.db_logger.make_connection")
    def test_cleanup_connection_error(self, mock_make_connection):
        """Connection errors must propagate so the API cannot report false success."""
        mock_make_connection.side_effect = Exception("Connection failed")

        with pytest.raises(Exception, match="Connection failed"):
            _cleanup_price_changes_blocking(retention_days=30)

    @patch("app.db_logger.make_connection")
    def test_cleanup_closes_connection(self, mock_make_connection):
        """Connection should always be closed in finally block."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_conn.cursor.return_value = mock_cursor
        mock_make_connection.return_value = mock_conn

        _cleanup_price_changes_blocking(retention_days=90)
        mock_conn.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.db_logger.asyncio.to_thread")
    async def test_async_cleanup_wrapper(self, mock_to_thread):
        """Async wrapper should delegate to blocking function."""
        mock_to_thread.return_value = 25

        result = await cleanup_price_changes(retention_days=45)
        assert result == 25
        mock_to_thread.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.db_logger.asyncio.to_thread")
    async def test_async_cleanup_no_args(self, mock_to_thread):
        """Async wrapper without args should pass None retention."""
        mock_to_thread.return_value = 10

        result = await cleanup_price_changes()
        assert result == 10
        # Verify the blocking function was called with None retention
        call_args = mock_to_thread.call_args
        assert call_args[0][0] == _cleanup_price_changes_blocking
        assert call_args[0][1] is None  # retention_days defaults to None


class TestEnsurePriceChangeTable:
    """Tests for _ensure_price_change_table."""

    def test_creates_table(self):
        """Should execute table creation SQL."""
        mock_cursor = MagicMock()
        _ensure_price_change_table(mock_cursor)

        # Verify both CREATE TABLE and CREATE INDEX were called
        assert mock_cursor.execute.call_count >= 2

        # First call should check for table existence
        first_call = mock_cursor.execute.call_args_list[0]
        assert "IF NOT EXISTS" in first_call[0][0]
        assert "ProductPriceChangeLog" in first_call[0][0]

        all_sql = "\n".join(call[0][0] for call in mock_cursor.execute.call_args_list)
        create_sql = first_call[0][0]
        assert "PriceDeltaPercent       AS" not in create_sql
        assert "DROP COLUMN PriceDeltaPercent" in all_sql


def test_audit_schema_cache_runs_all_ddl_once():
    mock_cursor = MagicMock()
    with (
        patch("app.db_logger._ensure_sync_log_table") as ensure_summary,
        patch("app.db_logger._ensure_price_change_table") as ensure_prices,
        patch("app.db_logger._ensure_sync_history_table") as ensure_history,
        patch("app.db_logger._ensure_service_state_tables") as ensure_state,
    ):
        _ensure_audit_schema_cached(mock_cursor)
        _ensure_audit_schema_cached(mock_cursor)

    ensure_summary.assert_called_once_with(mock_cursor)
    ensure_prices.assert_called_once_with(mock_cursor)
    ensure_history.assert_called_once_with(mock_cursor)
    ensure_state.assert_called_once_with(mock_cursor)

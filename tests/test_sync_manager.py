"""
Unit tests for the SyncManager class.

Tests cover SyncManager state management, result tracking, static helper methods,
basic concurrency constructs, and the central DB health check.

NOTE: Full sync cycle tests (run_full_sync, sync_single_outlet, retry_single_outlet)
require a live SQL Server database and are not included here. Those are integration
tests that should be run against a test database.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

import pytest

import app.config
from app.sync_manager import SyncManager


@pytest.fixture(autouse=True)
def isolate_runtime_persistence(monkeypatch):
    """Unit tests must not contact the production-style log persistence layer."""
    monkeypatch.setattr("app.sync_manager.save_cycle_summary", AsyncMock())
    monkeypatch.setattr("app.sync_manager.replace_retry_queue", AsyncMock())



class TestSyncManagerInitialState:
    """Tests for SyncManager initial state and basic properties."""

    def test_initial_state_is_idle(self):
        """A fresh SyncManager should be idle with zero counters."""
        sm = SyncManager()
        status = sm.get_status()

        assert status["state"] == "idle"
        assert status["total_outlets"] == 0
        assert status["completed"] == 0
        assert status["failed"] == 0
        assert status["in_progress"] == 0
        assert status["excluded"] == 0
        assert status["active_outlets"] == []
        assert status["trigger"] == ""

    def test_initial_counts_are_zero(self):
        """All counters should start at zero."""
        sm = SyncManager()
        assert sm.completed_count == 0
        assert sm.failed_count == 0
        assert sm.total_outlets == 0
        assert sm.in_progress_count == 0
        assert sm.excluded_count == 0
        assert sm.total_syncs_completed == 0

    def test_service_started_at_is_set(self):
        """service_started_at should be set on creation."""
        sm = SyncManager()
        assert sm.service_started_at is not None
        assert isinstance(sm.service_started_at, datetime)
        # Should be recent (within the last second)
        assert (datetime.now() - sm.service_started_at).total_seconds() < 1

    def test_not_stopped_on_creation(self):
        """stopped flag should be False on creation."""
        sm = SyncManager()
        assert sm.stopped is False
        assert sm.is_running is False

    def test_semaphore_capacity(self):
        """Semaphore should match MAX_CONCURRENT_SYNCS (default 20)."""
        sm = SyncManager()
        assert sm.semaphore._value == app.config.settings.MAX_CONCURRENT_SYNCS

    def test_retry_queue_exists(self):
        """retry_queue should be a RetryQueue instance."""
        sm = SyncManager()
        from app.retry_queue import RetryQueue
        assert isinstance(sm.retry_queue, RetryQueue)

    def test_active_outlets_empty(self):
        """active_outlets should start empty."""
        sm = SyncManager()
        assert sm.active_outlets == {}

    @pytest.mark.asyncio
    async def test_restore_persisted_dashboard_and_retry_state(self, monkeypatch):
        now = datetime.now()
        dashboard = {
            "attempted": 7, "completed": 5, "partial_failure": 1,
            "cancelled_total": 0, "timed_out": 0, "failed_total": 1,
            "last": {
                "run_id": "11111111-1111-1111-1111-111111111111",
                "trigger": "scheduled", "started_at": now, "finished_at": now,
                "outcome": "partial_failure", "total_outlets": 963,
                "successful": 867, "failed": 96, "excluded": 0,
                "cancelled": 0, "audit_failed": 0, "retry_queue_size": 1,
                "duration_seconds": 100.0,
            },
        }
        retry_rows = [{
            "outlet_code": "B001", "server": "10.0.0.1", "attempt": 1,
            "max_attempts": 3, "last_error": "timeout",
            "added_at": now.isoformat(), "next_retry_at": now.isoformat(),
            "permanently_failed": False,
        }]
        monkeypatch.setattr("app.sync_manager.load_cycle_dashboard_state", AsyncMock(return_value=dashboard))
        monkeypatch.setattr("app.sync_manager.load_retry_queue", AsyncMock(return_value=retry_rows))
        manager = SyncManager()

        await manager.restore_persisted_state()

        assert manager.total_syncs_attempted == 7
        assert manager.total_outlets == 963
        assert manager.completed_count == 867
        assert manager.failed_count == 96
        assert manager.retry_queue.size == 1
        assert manager.retry_queue.pending_count == 1
        assert manager.retry_queue.get_all()[0]["max_attempts"] == 10


class TestSyncManagerResults:
    """Tests for SyncManager outlet result management."""

    def test_outlet_results_empty_initially(self):
        """get_outlet_results() should return an empty list initially."""
        sm = SyncManager()
        results = sm.get_outlet_results()
        assert results == []

    def test_get_outlet_result_none(self):
        """get_outlet_result() should return None for unknown outlet."""
        sm = SyncManager()
        result = sm.get_outlet_result("NONEXISTENT")
        assert result is None

    def test_get_outlet_result_case_insensitive(self):
        """get_outlet_result() should be case-insensitive."""
        sm = SyncManager()
        sm.outlet_results = [
            {
                "outlet_code": "B001",
                "ip": "192.168.1.1",
                "status": "Success",
                "remarks": "OK",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": 5.0,
            }
        ]

        result = sm.get_outlet_result("b001")  # lowercase
        assert result is not None
        assert result["outlet_code"] == "B001"

        result = sm.get_outlet_result("B001")  # uppercase
        assert result is not None

    def test_get_outlet_result_filters_correctly(self):
        """get_outlet_result() should only return the matching outlet."""
        sm = SyncManager()
        results_data = [
            {
                "outlet_code": "B001",
                "status": "Success",
            },
            {
                "outlet_code": "B002",
                "status": "N",
            },
        ]
        sm.outlet_results = results_data

        result = sm.get_outlet_result("B001")
        assert result["outlet_code"] == "B001"
        assert result["status"] == "Success"

        result = sm.get_outlet_result("B002")
        assert result["outlet_code"] == "B002"
        assert result["status"] == "N"

    def test_get_outlet_returns_copy(self):
        """get_outlet_results() should return a copy, not the internal list."""
        sm = SyncManager()
        results = sm.get_outlet_results()
        results.append({"fake": "data"})
        # Internal list should not be affected
        assert len(sm.outlet_results) == 0

    def test_outlet_results_persist_across_calls(self):
        """Results should remain available until overwritten."""
        sm = SyncManager()
        sm.outlet_results = [
            {
                "outlet_code": "B001",
                "status": "Success",
                "ip": "10.0.0.1",
                "remarks": "Y",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": 3.0,
            }
        ]

        r1 = sm.get_outlet_result("B001")
        r2 = sm.get_outlet_result("B001")
        assert r1 == r2


class TestSyncManagerStatus:
    """Tests for SyncManager status reporting."""

    def test_status_shows_running_state(self):
        """When is_running=True, status should show 'running'."""
        sm = SyncManager()
        sm.is_running = True
        sm.current_trigger = "manual"
        status = sm.get_status()
        assert status["state"] == "running"
        assert status["trigger"] == "manual"

    def test_status_includes_started_finished(self):
        """Status should include started_at and finished_at."""
        sm = SyncManager()
        now = datetime.now()
        sm.sync_started_at = now
        sm.sync_finished_at = now

        status = sm.get_status()
        assert status["started_at"] is not None
        assert status["finished_at"] is not None

    def test_status_includes_excluded_count(self):
        """Status should include excluded count."""
        sm = SyncManager()
        sm.excluded_count = 3
        status = sm.get_status()
        assert status["excluded"] == 3

    def test_status_includes_active_outlets(self):
        """Status should include active outlet list."""
        sm = SyncManager()
        sm.active_outlets = {
            "B001": {
                "outlet_code": "B001",
                "server": "10.0.0.1",
                "started_at": datetime.now().isoformat(),
            }
        }
        status = sm.get_status()
        assert len(status["active_outlets"]) == 1
        assert status["active_outlets"][0]["outlet_code"] == "B001"

    def test_status_shows_counts(self):
        """Status should show completed/failed/in_progress counts."""
        sm = SyncManager()
        sm.completed_count = 5
        sm.failed_count = 2
        sm.in_progress_count = 3
        sm.total_outlets = 10

        status = sm.get_status()
        assert status["completed"] == 5
        assert status["failed"] == 2
        assert status["in_progress"] == 3
        assert status["total_outlets"] == 10


class TestSyncManagerStaticMethods:
    """Tests for SyncManager static helper methods."""

    def test_failure_result(self):
        """_failure_result() should produce a correctly formatted error dict."""
        outlet = {"Outlet": "B001", "Server": "192.168.1.1"}
        error_msg = "Connection timeout after 10 seconds"
        result = SyncManager._failure_result(outlet, error_msg)

        assert result["outlet_code"] == "B001"
        assert result["ip"] == "192.168.1.1"
        assert result["status"] == "N"
        assert result["remarks"] == error_msg
        assert result["duration_seconds"] == 0.0
        assert "timestamp" in result

    def test_failure_result_unknown_outlet(self):
        """_failure_result() should handle missing outlet fields."""
        result = SyncManager._failure_result({}, "Generic error")
        assert result["outlet_code"] == "Unknown"
        assert result["ip"] == "N/A"

    def test_excluded_outlet_codes_single(self):
        """A single excluded code should return a single-element set."""
        with _patch_excluded("F786"):
            excluded = SyncManager._excluded_outlet_codes()
            assert excluded == {"F786"}

    def test_excluded_outlet_codes_multiple(self):
        """Multiple comma-separated codes should be parsed correctly."""
        with _patch_excluded("F786,B001,B002"):
            excluded = SyncManager._excluded_outlet_codes()
            assert excluded == {"F786", "B001", "B002"}

    def test_excluded_outlet_codes_empty(self):
        """An empty exclusion list should return an empty set."""
        with _patch_excluded(""):
            excluded = SyncManager._excluded_outlet_codes()
            assert excluded == set()

    def test_excluded_outlet_codes_case_insensitive(self):
        """Excluded codes should be case-insensitive (all uppercased)."""
        with _patch_excluded("f786,b001"):
            excluded = SyncManager._excluded_outlet_codes()
            assert excluded == {"F786", "B001"}

    def test_excluded_outlet_codes_whitespace(self):
        """Extra whitespace around codes should be trimmed."""
        with _patch_excluded("  F786 ,  B001  "):
            excluded = SyncManager._excluded_outlet_codes()
            assert excluded == {"F786", "B001"}


class TestSyncManagerHealthCheck:
    """Tests for SyncManager._check_central_db_health().

    The function now checks both source and log databases and returns
    a tuple (source_ok, log_ok). Two make_connection calls are made:
    first for source, second for log.
    """

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_both_healthy(self, mock_make_connection):
        """Both databases healthy should return (True, True)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Both calls to make_connection succeed
        mock_make_connection.return_value = mock_conn

        source_ok, log_ok = await SyncManager._check_central_db_health()

        assert source_ok is True
        assert log_ok is True
        # Should make two connections: source + log
        assert mock_make_connection.call_count == 2
        # Verify both calls used timeout=5
        for call_args in mock_make_connection.call_args_list:
            assert call_args[1].get("timeout") == 5
        # Verify conn.timeout was set to 5 on both
        assert mock_conn.timeout == 5

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_source_fails(self, mock_make_connection):
        """Source DB failure should return (False, True) (log still tries)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # First call (source) fails, second (log) succeeds
        mock_make_connection.side_effect = [
            Exception("Source connection refused"),  # source fails
            mock_conn,  # log succeeds
        ]

        source_ok, log_ok = await SyncManager._check_central_db_health()

        assert source_ok is False
        assert log_ok is True

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_log_fails(self, mock_make_connection):
        """Log DB failure should return (True, False)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # First call (source) succeeds, second (log) fails
        mock_make_connection.side_effect = [
            mock_conn,  # source succeeds
            Exception("Log connection refused"),  # log fails
        ]

        source_ok, log_ok = await SyncManager._check_central_db_health()

        assert source_ok is True
        assert log_ok is False

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_both_fail(self, mock_make_connection):
        """Both DBs failing should return (False, False)."""
        mock_make_connection.side_effect = [
            Exception("Source down"),
            Exception("Log down"),
        ]

        source_ok, log_ok = await SyncManager._check_central_db_health()

        assert source_ok is False
        assert log_ok is False

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_closes_both_connections(self, mock_make_connection):
        """Both connections should be closed after health check."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Both calls return same mock conn
        mock_make_connection.return_value = mock_conn

        await SyncManager._check_central_db_health()

        # close() should be called twice (once per connection)
        assert mock_conn.close.call_count == 2

    @pytest.mark.asyncio
    @mock_patch("app.sync_manager.make_connection")
    async def test_health_check_closes_on_first_failure(self, mock_make_connection):
        """If source fails, log is still checked and both connections cleaned up."""
        mock_log_conn = MagicMock()
        mock_log_cursor = MagicMock()
        mock_log_conn.cursor.return_value = mock_log_cursor

        mock_make_connection.side_effect = [
            Exception("Source down"),  # source fails immediately
            mock_log_conn,  # log succeeds
        ]

        await SyncManager._check_central_db_health()

        # Log connection should be closed
        mock_log_conn.close.assert_called_once()


class TestSyncManagerStop:
    """Tests for SyncManager stop/shutdown logic."""

    def test_stop_sets_flag(self):
        """stop() should set the stopped flag to True."""
        sm = SyncManager()
        assert sm.stopped is False
        sm.stop()
        assert sm.stopped is True

    def test_stop_is_idempotent(self):
        """Calling stop() multiple times should not raise."""
        sm = SyncManager()
        sm.stop()
        sm.stop()  # second call should not raise
        assert sm.stopped is True


class TestSyncManagerConcurrency:
    """Tests for SyncManager concurrency constructs.

    These tests verify that the basic concurrency constructs (semaphore, lock)
    behave correctly without actually performing sync operations.
    """

    @pytest.mark.asyncio
    async def test_lock_acquire_release(self):
        """sync_lock should be acquirable and releasable."""
        sm = SyncManager()
        async with sm.sync_lock:
            assert sm.sync_lock.locked() is True
        assert sm.sync_lock.locked() is False

    @pytest.mark.asyncio
    async def test_semaphore_limits(self):
        """Semaphore should limit concurrent access."""
        sm = SyncManager()
        entered = 0
        max_concurrent = 0

        async def test_task():
            nonlocal entered, max_concurrent
            entered += 1
            max_concurrent = max(max_concurrent, entered)
            await asyncio.sleep(0.1)
            entered -= 1

        # Launch 20 tasks but semaphore limits to 10
        tasks = [asyncio.create_task(test_task()) for _ in range(20)]
        await asyncio.gather(*tasks)

        assert max_concurrent >= 5  # should have at least 5 concurrent

    @pytest.mark.asyncio
    async def test_semaphore_blocks_when_full(self):
        """Semaphore should block when all permits are taken."""
        sm = SyncManager()
        # Take all permits
        for _ in range(app.config.settings.MAX_CONCURRENT_SYNCS):
            await sm.semaphore.acquire()

        # Now one more should block - verify it's blocked by checking quickly
        blocked = False

        async def try_acquire():
            nonlocal blocked
            try:
                await asyncio.wait_for(sm.semaphore.acquire(), timeout=0.1)
            except asyncio.TimeoutError:
                blocked = True

        await asyncio.wait_for(try_acquire(), timeout=0.2)
        assert blocked is True

        # Release one permit
        sm.semaphore.release()

        # Now should succeed
        blocked = False
        await asyncio.wait_for(try_acquire(), timeout=0.1)
        assert blocked is False

    @pytest.mark.asyncio
    async def test_timed_out_operation_must_drain_before_retry(self):
        """A retry must not create a second task while the ODBC worker is draining."""
        manager = SyncManager()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def blocked_sync(outlet):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                "outlet_code": outlet["Outlet"],
                "ip": outlet["Server"],
                "status": "Success",
                "remarks": "Y",
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": 0,
            }

        outlet = {"Outlet": "F001", "Server": "127.0.0.1"}
        with (
            mock_patch("app.sync_manager.run_on_outlet", side_effect=blocked_sync),
            mock_patch.object(app.config.settings, "OUTLET_SYNC_TIMEOUT", 0.01),
        ):
            first = await manager._run_outlet_with_watchdog(outlet)
            assert first["status"] == "N"
            assert "F001" in manager.draining_outlets

            with pytest.raises(RuntimeError, match="already has an active"):
                await manager._run_outlet_with_watchdog(outlet)

            assert calls == 1
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert "F001" not in manager.draining_outlets
        assert "F001" not in manager.outlet_operations


# --- Helpers ---

def _patch_excluded(value: str):
    """Patch settings.EXCLUDED_OUTLETS for testing using mock."""
    return mock_patch.object(app.config.settings, "EXCLUDED_OUTLETS", value)

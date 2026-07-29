"""
Unit tests for the retry queue module.

Tests cover:
- RetryEntry creation and backoff calculation
- RetryQueue add/get_due/remove/clear/get_all operations
- Permanent failure at max attempts
- Deduplication by outlet code
- Attempt tracking and state management
"""

import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.retry_queue import RetryEntry, RetryQueue


def make_outlet(outlet_code: str = "B001", server: str = "192.168.1.1") -> dict:
    """Create a standard outlet dict for testing."""
    return {
        "Outlet": outlet_code,
        "Server": server,
        "Database": "TestDB",
        "User": "sa",
        "Password": "pass",
    }


class TestRetryEntry:
    """Tests for the RetryEntry class."""

    def test_creation_defaults(self):
        """A RetryEntry should have sensible defaults."""
        outlet = make_outlet("B001", "192.168.1.1")
        entry = RetryEntry(outlet, "Connection error", attempt=1)

        assert entry.outlet_code == "B001"
        assert entry.server == "192.168.1.1"
        assert entry.attempt == 1
        assert entry.max_attempts == 10  # from default settings
        assert "Connection error" in entry.last_error
        assert entry.next_retry_at > datetime.now()

    def test_creation_unknown_outlet(self):
        """Missing outlet code should default to 'Unknown'."""
        entry = RetryEntry({}, "Some error")
        assert entry.outlet_code == "Unknown"
        assert entry.server == "N/A"

    def test_exponential_backoff_increases(self):
        """Each subsequent attempt should have a longer delay."""
        outlet = make_outlet()
        entry1 = RetryEntry(outlet, "err", attempt=1)
        entry2 = RetryEntry(outlet, "err", attempt=2)

        delay1 = (entry1.next_retry_at - entry1.added_at).total_seconds()
        delay2 = (entry2.next_retry_at - entry2.added_at).total_seconds()

        # Attempt 2 should have a larger base delay than attempt 1
        assert delay2 > delay1, (
            f"Expected attempt 2 delay ({delay2:.1f}s) "
            f"> attempt 1 delay ({delay1:.1f}s)"
        )

    def test_to_dict_contains_required_fields(self):
        """to_dict() should return all expected fields."""
        outlet = make_outlet("B005", "10.0.0.5")
        entry = RetryEntry(outlet, "Login failed", attempt=2)
        d = entry.to_dict()

        assert d["outlet_code"] == "B005"
        assert d["server"] == "10.0.0.5"
        assert d["attempt"] == 2
        assert d["max_attempts"] == 10
        assert "next_retry_at" in d
        assert d["last_error"] == "Login failed"
        assert "added_at" in d
        assert "permanently_failed" not in d
        assert d["next_retry_at"] >= d["added_at"]

    def test_from_persisted_restores_timestamps_and_outlet(self):
        now = datetime.now()
        entry = RetryEntry.from_persisted({
            "outlet_code": "B005", "server": "10.0.0.5", "attempt": 2,
            "max_attempts": 3, "last_error": "timeout",
            "added_at": now.isoformat(), "next_retry_at": (now + timedelta(minutes=1)).isoformat(),
        })
        assert entry.outlet_code == "B005"
        assert entry.attempt == 2
        assert entry.max_attempts == 10
        assert entry.added_at == now


class TestRetryQueue:
    """Tests for the RetryQueue collection class."""

    def test_empty_queue(self):
        """A fresh queue should be empty."""
        q = RetryQueue()
        assert q.size == 0
        assert q.pending_count == 0
        assert q.get_due() == []
        assert q.get_all() == []

    def test_add_single_entry(self):
        """Adding a failed outlet should increase queue size."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Connection timeout")
        assert q.size == 1
        assert q.pending_count == 1

    def test_deduplication(self):
        """Adding the same outlet twice should not duplicate the entry."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "First error")
        q.add(make_outlet("B001"), "Second error")
        assert q.size == 1
        assert q.pending_count == 1

    def test_remove_entry(self):
        """Removing an entry should decrease queue size."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Error")
        assert q.size == 1

        q.remove("B001")
        assert q.size == 0
        assert q.pending_count == 0

    def test_remove_nonexistent(self):
        """Removing an outlet not in the queue should not raise."""
        q = RetryQueue()
        q.remove("NONEXISTENT")  # should not raise
        assert q.size == 0

    def test_clear_queue(self):
        """Clearing the queue should remove all entries."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Error 1")
        q.add(make_outlet("B002"), "Error 2")
        q.add(make_outlet("B003"), "Error 3")
        assert q.size == 3

        q.clear()
        assert q.size == 0
        assert q.pending_count == 0
        assert q.get_all() == []

    def test_get_due_returns_only_due_entries(self):
        """get_due() should only return entries whose retry time has passed."""
        q = RetryQueue()
        outlet = make_outlet("B001")
        q.add(outlet, "Error")

        # Immediately after adding, the entry should not be due yet
        # (it has a backoff delay, so next_retry_at is in the future)
        due = q.get_due()
        assert len(due) == 0, "New entry should not be due immediately"

    def test_get_all_returns_all_entries(self):
        """get_all() should return all queued entries as dicts."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Error 1")
        q.add(make_outlet("B002"), "Error 2")

        entries = q.get_all()
        assert len(entries) == 2
        codes = {e["outlet_code"] for e in entries}
        assert codes == {"B001", "B002"}

    def test_get_all_marks_permanently_failed(self):
        """Permanently failed entries should have the flag in get_all()."""
        q = RetryQueue()

        # Add with attempt already at max -> permanently failed
        outlet = make_outlet("B001")
        q.add(outlet, "Fatal error", attempt=10)

        entries = q.get_all()
        assert len(entries) == 1
        assert entries[0]["permanently_failed"] is True
        assert entries[0]["attempt"] >= entries[0]["max_attempts"]

    def test_multiple_outlets_independent(self):
        """Multiple outlets should not interfere with each other."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Error 1")
        q.add(make_outlet("B002"), "Error 2")
        q.add(make_outlet("B003"), "Error 3")

        assert q.size == 3

        q.remove("B002")
        assert q.size == 2

        remaining_codes = {e["outlet_code"] for e in q.get_all()}
        assert remaining_codes == {"B001", "B003"}

    def test_permanent_failure_on_max_attempts(self):
        """An entry should move to permanently failed when attempt >= max."""
        q = RetryQueue()
        outlet = make_outlet("B045")

        # Add at max attempts -> should go directly to permanently failed
        q.add(outlet, "Persistent error", attempt=10)

        assert q.pending_count == 0
        assert q.size == 1

        entries = q.get_all()
        assert entries[0]["permanently_failed"] is True

    def test_remove_cleans_permanently_failed(self):
        """Removing a permanently failed outlet should also clear it."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Error", attempt=10)
        assert q.size == 1

        q.remove("B001")
        assert q.size == 0

    def test_clear_cleans_both_queues(self):
        """Clearing should empty both pending and permanently failed."""
        q = RetryQueue()
        q.add(make_outlet("B001"), "Regular", attempt=1)
        q.add(make_outlet("B002"), "Permanent", attempt=10)
        assert q.size == 2

        q.clear()
        assert q.size == 0
        assert q.get_all() == []

    def test_attempt_tracking(self):
        """Entry should track attempt numbers correctly."""
        q = RetryQueue()

        # First failure
        q.add(make_outlet("B001"), "Attempt 1 fail", attempt=1)
        entries = q.get_all()
        assert entries[0]["attempt"] == 1
        assert entries[0]["max_attempts"] == 10

        # Remove and re-add at higher attempt
        q.remove("B001")
        q.add(make_outlet("B001"), "Attempt 2 fail", attempt=2)
        entries = q.get_all()
        assert entries[0]["attempt"] == 2

    def test_large_number_of_outlets(self):
        """Queue should handle many outlets efficiently."""
        q = RetryQueue()
        for i in range(100):
            code = f"B{i:03d}"
            q.add(make_outlet(code), f"Error {i}")

        assert q.size == 100
        assert q.pending_count == 100

        entries = q.get_all()
        assert len(entries) == 100

        q.clear()
        assert q.size == 0

    def test_restore_replaces_pending_and_permanent_entries(self):
        now = datetime.now()
        rows = [{
            "outlet_code": "B001", "server": "10.0.0.1", "attempt": 1,
            "max_attempts": 3, "last_error": "error", "added_at": now.isoformat(),
            "next_retry_at": now.isoformat(), "permanently_failed": False,
        }, {
            # This entry was exhausted under the old 3-attempt policy.
            "outlet_code": "B002", "server": "10.0.0.2", "attempt": 3,
            "max_attempts": 3, "last_error": "error", "added_at": now.isoformat(),
            "next_retry_at": now.isoformat(), "permanently_failed": True,
        }, {
            # Classification is always based on the current policy.
            "outlet_code": "B003", "server": "10.0.0.3", "attempt": 10,
            "max_attempts": 20, "last_error": "error", "added_at": now.isoformat(),
            "next_retry_at": now.isoformat(), "permanently_failed": False,
        }]
        q = RetryQueue()
        q.restore(rows)
        assert q.size == 3
        assert q.pending_count == 2
        restored = {entry["outlet_code"]: entry for entry in q.get_all()}
        assert restored["B002"]["max_attempts"] == 10
        assert "permanently_failed" not in restored["B002"]
        assert restored["B003"]["permanently_failed"] is True

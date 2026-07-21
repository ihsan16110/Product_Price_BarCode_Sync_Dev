"""
Unit tests for the sync_sql module.

Tests cover:
- SQL generation with correct parameters substituted
- Linked server, central DB, local DB substitution
- Outlet code parameter in depot filter
- Query structure (SET NOCOUNT ON, USE, OPENQUERY, temp tables, INSERT/UPDATE, marker SELECT, DROP)
- LINKED_SERVER_CHECK_SQL and LINKED_SERVER_CREATE_TEMPLATE constants
"""

import pytest

from app.sync_sql import (
    LINKED_SERVER_CHECK_SQL,
    LINKED_SERVER_CREATE_TEMPLATE,
    get_sync_sql,
)


class TestGetSyncSql:
    """Tests for the main get_sync_sql function."""

    def test_returns_string(self):
        """get_sync_sql() should return a non-empty string."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert isinstance(sql, str)
        assert len(sql) > 100

    def test_contains_set_nocount(self):
        """SQL should start with SET NOCOUNT ON (may have leading newline from f-string)."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert sql.strip().startswith("SET NOCOUNT ON;")

    def test_contains_use_database(self):
        """SQL should contain USE with the local_db parameter."""
        sql = get_sync_sql("MyLocalDB", "192.168.11.200", "EPSMirror", "B004")
        assert "USE [MyLocalDB]" in sql

    def test_uses_correct_local_db(self):
        """local_db parameter should appear in USE statement."""
        sql = get_sync_sql("OutletDB", "192.168.11.200", "EPSMirror", "B004")
        assert "USE [OutletDB]" in sql

    def test_contains_linked_server(self):
        """SQL should contain the linked server name in OPENQUERY."""
        sql = get_sync_sql("EPS", "MY_LINKED_SRV", "EPSMirror", "B004")
        assert "OPENQUERY([MY_LINKED_SRV]" in sql

    def test_contains_central_db(self):
        """SQL should reference the central database."""
        sql = get_sync_sql("EPS", "192.168.11.200", "CentralDB", "B004")
        assert "CentralDB.DBO.Product D" in sql
        assert "CentralDB.DBO.ProductPrice D" in sql
        assert "CentralDB.DBO.ProductBarcode D" in sql

    def test_contains_outlet_code_filter(self):
        """SQL should filter ProductPrice by the given outlet_code."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "DepotCode = ''B004''" in sql

    def test_different_outlet_code(self):
        """SQL should use a different outlet_code when provided."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "F786")
        assert "DepotCode = ''F786''" in sql

    def test_contains_temp_tables(self):
        """SQL should create all required temp tables."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "#HoProduct" in sql
        assert "#HoProductPrice" in sql
        assert "#HoBarcode" in sql
        assert "#ChangedPrices" in sql

    def test_contains_all_inserts(self):
        """SQL should have INSERT statements for all three data types."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "INSERT INTO Product (" in sql
        assert "INSERT INTO ProductPrice (" in sql
        assert "INSERT INTO ProductBarcode (" in sql

    def test_contains_all_updates(self):
        """SQL should have UPDATE statements for Product and ProductPrice."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "UPDATE P" in sql or "UPDATE Product" in sql

    def test_contains_price_changes_marker(self):
        """SQL should contain the PRICE_CHANGES marker SELECT."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "PRICE_CHANGES" in sql
        assert "OldUnitPrice" in sql
        assert "NewUnitPrice" in sql

    def test_contains_price_changes_output(self):
        """SQL should contain OUTPUT INTO @PriceChanges."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "OUTPUT" in sql
        assert "@PriceChanges" in sql
        assert "DELETED.UnitPrice" in sql
        assert "INSERTED.UnitPrice" in sql
        assert "OUTPUT 'INSERT'" in sql
        assert "OUTPUT 'UPDATE'" in sql
        assert "PRICE_CHANGE_SUMMARY" in sql

    def test_audit_instrumentation_does_not_change_insert_selection(self):
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "SELECT D.* FROM #HoProductPrice D" in sql
        assert "LEFT JOIN ProductPrice P ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode" in sql
        assert "WHERE P.ProductCode IS NULL OR P.DepotCode IS NULL" in sql

    def test_audit_instrumentation_preserves_update_selection(self):
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "INNER JOIN #ChangedPrices C ON D.ProductCode = C.ProductCode AND D.DepotCode = C.DepotCode" in sql
        assert "WHERE SQ.ProductCode IS NULL" in sql

    def test_contains_price_change_table(self):
        """SQL should declare @PriceChanges table variable."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "DECLARE @PriceChanges TABLE" in sql

    def test_contains_vfmg_exclusion(self):
        """SQL should include ProductVfmg exclusion in the price update."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "ProductVfmg" in sql

    def test_contains_drop_statements(self):
        """SQL should drop all temp tables at the end."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "DROP TABLE #HoProduct" in sql
        assert "DROP TABLE #HoProductPrice" in sql
        assert "DROP TABLE #HoBarcode" in sql
        assert "DROP TABLE #ChangedPrices" in sql

    def test_does_not_contain_raw_placeholders(self):
        """SQL should not contain raw Python f-string placeholders."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "{local_db}" not in sql
        assert "{central_linked_server}" not in sql
        assert "{central_db}" not in sql
        assert "{outlet_code}" not in sql

    def test_contains_null_safe_comparisons(self):
        """SQL should have NULL-safe comparisons in the update filter."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "IS NULL AND" in sql
        assert "IS NOT NULL" in sql

    def test_contains_price_field_comparisons(self):
        """SQL should compare UnitPrice and ModifiedDate for changes."""
        sql = get_sync_sql("EPS", "192.168.11.200", "EPSMirror", "B004")
        assert "UnitPrice" in sql
        assert "ModifiedDate" in sql


class TestLinkedServerSQL:
    """Tests for the LINKED_SERVER_* SQL constants."""

    def test_check_sql_is_select(self):
        """LINKED_SERVER_CHECK_SQL should be a SELECT COUNT statement."""
        assert "SELECT COUNT" in LINKED_SERVER_CHECK_SQL
        assert "sys.servers" in LINKED_SERVER_CHECK_SQL
        assert "?" in LINKED_SERVER_CHECK_SQL  # parameterized

    def test_create_template_contains_format_placeholder(self):
        """LINKED_SERVER_CREATE_TEMPLATE should have a format placeholder."""
        assert "{linked_server_name}" in LINKED_SERVER_CREATE_TEMPLATE

    def test_create_template_format_works(self):
        """LINKED_SERVER_CREATE_TEMPLATE should format correctly."""
        sql = LINKED_SERVER_CREATE_TEMPLATE.format(linked_server_name="192.168.11.200")
        assert "192.168.11.200" in sql
        assert "{linked_server_name}" not in sql

    def test_create_template_contains_sp_addlinkedserver(self):
        """LINKED_SERVER_CREATE_TEMPLATE should contain sp_addlinkedserver."""
        assert "sp_addlinkedserver" in LINKED_SERVER_CREATE_TEMPLATE

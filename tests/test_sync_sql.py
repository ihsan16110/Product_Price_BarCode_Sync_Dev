from app.sync_sql import (
    LINKED_SERVER_CHECK_SQL,
    LINKED_SERVER_CREATE_TEMPLATE,
    get_sync_sql,
)


class TestGetSyncSql:
    def test_substitutes_and_escapes_identifiers(self):
        sql = get_sync_sql("Outlet]DB", "HO]LINK", "Mirror]DB", "B004")
        assert "USE [Outlet]]DB]" in sql
        assert "OPENQUERY([HO]]LINK]" in sql
        assert "FROM [Mirror]]DB].dbo.RepProduct" in sql

    def test_escapes_outlet_code_literal(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B'004")
        assert "DepotCode = ''B''''004''" in sql

    def test_reads_replication_tables(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "dbo.RepProduct'" in sql
        assert "dbo.RepProductPrice" in sql
        assert "dbo.RepProductBarcode'" in sql
        assert "SyncStatus = ''N''" in sql

    def test_separates_delete_markers_from_upserts(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "ISNULL(SyncType, '''') <> ''D''" in sql
        assert "SyncType = ''D''" in sql
        assert "#HoProductPriceDelete" in sql
        assert "DELETE P" in sql
        assert "INNER JOIN #HoProductPriceDelete D" in sql

    def test_price_flag_only_controls_unit_price(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "CASE WHEN D.Price = 'Y' THEN D.UnitPrice ELSE ISNULL(P.UnitPrice, 0) END" in sql
        assert "P.UnitPrice = CASE WHEN D.Price = 'Y' THEN D.UnitPrice ELSE P.UnitPrice END" in sql
        assert "P.VATPerc = D.VATPerc" in sql
        assert "P.ModifiedDate = D.ModifiedDate" in sql

    def test_synchronizes_all_three_outlet_tables(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "INSERT INTO Product (" in sql
        assert "INSERT INTO ProductPrice (" in sql
        assert "INSERT INTO ProductBarcode (" in sql
        assert "UPDATE P" in sql

    def test_returns_upsert_and_delete_keys_for_post_commit_ack(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "#HoAcknowledgements" in sql
        assert "SELECT ProductCode, DepotCode FROM #HoProductPrice" in sql
        assert "SELECT ProductCode, DepotCode FROM #HoProductPriceDelete" in sql
        assert "'HO_ACK_SUMMARY' AS Marker" in sql
        assert "'HO_ACKNOWLEDGEMENTS' AS Marker" in sql

    def test_does_not_update_head_office_inside_outlet_batch(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        assert "UPDATE dbo.RepProductPrice" not in sql
        assert "SentTime" not in sql

    def test_drops_all_temp_tables(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        for table in (
            "#HoAcknowledgements",
            "#HoProductPriceDelete",
            "#HoProduct",
            "#HoProductPrice",
            "#HoBarcode",
        ):
            assert f"DROP TABLE {table}" in sql

    def test_does_not_contain_raw_placeholders(self):
        sql = get_sync_sql("EPS", "HO", "EPSMirror", "B004")
        for placeholder in (
            "{local_db}",
            "{central_linked_server}",
            "{central_db}",
            "{outlet_code}",
        ):
            assert placeholder not in sql


class TestLinkedServerSQL:
    def test_check_sql_is_parameterized_select(self):
        assert "SELECT COUNT" in LINKED_SERVER_CHECK_SQL
        assert "sys.servers" in LINKED_SERVER_CHECK_SQL
        assert "?" in LINKED_SERVER_CHECK_SQL

    def test_create_template_formats_sp_addlinkedserver(self):
        assert "{linked_server_name}" in LINKED_SERVER_CREATE_TEMPLATE
        sql = LINKED_SERVER_CREATE_TEMPLATE.format(linked_server_name="192.168.11.200")
        assert "sp_addlinkedserver" in sql
        assert "192.168.11.200" in sql
        assert "{linked_server_name}" not in sql

import asyncio
import threading
from datetime import datetime
from uuid import uuid4

from app.config import settings
from app.database import make_connection
from app.logger import get_logger

logger = get_logger(__name__)

_audit_schema_ready = False
_audit_schema_lock = threading.Lock()


def _ensure_audit_schema_cached(cursor) -> None:
    """Run additive audit DDL once per service process, with lazy retry on failure."""
    global _audit_schema_ready
    if _audit_schema_ready:
        return

    with _audit_schema_lock:
        if _audit_schema_ready:
            return
        _ensure_sync_log_table(cursor)
        if settings.ENABLE_PRODUCT_PRICE_CHANGE_LOG:
            _ensure_price_change_table(cursor)
        if settings.ENABLE_PRODUCT_SYNC_LOG_HISTORY:
            _ensure_sync_history_table(cursor)
        _ensure_service_state_tables(cursor)
        _audit_schema_ready = True


def _reset_audit_schema_cache() -> None:
    """Reset process-local schema state for tests or an explicit recheck."""
    global _audit_schema_ready
    with _audit_schema_lock:
        _audit_schema_ready = False


def _make_log_connection():
    """Create a connection to the dedicated log database server."""
    return make_connection(
        server=settings.LOG_SERVER,
        database=settings.LOG_DATABASE,
        user=settings.LOG_USER,
        password=settings.LOG_PASSWORD,
        autocommit=True,
    )


def _ensure_sync_log_table(cursor) -> None:
    """Create the latest-status table during cached schema initialization."""
    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sysobjects
            WHERE name = 'ProductSyncLog' AND xtype = 'U'
        )
        CREATE TABLE ProductSyncLog (
            DepotCode          VARCHAR(10) PRIMARY KEY,
            LastSyncStatus     VARCHAR(50),
            LastSuccessfulSync DATETIME NULL,
            LastAttempt        DATETIME NULL,
            Remarks            VARCHAR(500) NULL
        )
    """)


def _update_sync_log_blocking(outlet_code: str, status: str, remarks: str) -> bool:
    """
    Write/update status into ProductSyncLog in the log database.
    Blocking version - run via asyncio.to_thread().
    """
    outlet_code = outlet_code or "UNKNOWN"
    remarks = remarks or ""
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()

        # Avoid repeating DDL for every outlet; lazily retry if startup failed.
        _ensure_audit_schema_cached(cursor)

        # Normalize remarks text
        norm = remarks.lower()
        data_succeeded = status in {"Success", "Partial"}
        if status == "Success":
            formatted_remarks = "Y"
        elif status == "Partial":
            formatted_remarks = remarks[:490]
        elif "network" in norm or "timeout" in norm or "connection" in norm:
            formatted_remarks = "Network Connectivity Issue"
        elif "query" in norm or "sql" in norm or "syntax" in norm:
            formatted_remarks = "Query Error"
        else:
            formatted_remarks = remarks[:490]

        now = datetime.now()

        # Check existing record
        cursor.execute(
            "SELECT COUNT(*) FROM ProductSyncLog WHERE DepotCode = ?",
            outlet_code,
        )
        exists = cursor.fetchone()[0] > 0

        if exists:
            if data_succeeded:
                cursor.execute(
                    """
                    UPDATE ProductSyncLog
                    SET
                        LastSyncStatus     = ?,
                        LastSuccessfulSync = ?,
                        LastAttempt        = ?,
                        Remarks            = ?
                    WHERE DepotCode = ?
                    """,
                    status, now, now, formatted_remarks, outlet_code,
                )
            else:
                cursor.execute(
                    """
                    UPDATE ProductSyncLog
                    SET
                        LastSyncStatus = ?,
                        LastAttempt    = ?,
                        Remarks        = ?
                    WHERE DepotCode = ?
                    """,
                    status, now, formatted_remarks, outlet_code,
                )
        else:
            if data_succeeded:
                cursor.execute(
                    """
                    INSERT INTO ProductSyncLog (
                        DepotCode, LastSyncStatus, LastSuccessfulSync,
                        LastAttempt, Remarks
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    outlet_code, status, now, now, formatted_remarks,
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO ProductSyncLog (
                        DepotCode, LastSyncStatus, LastSuccessfulSync,
                        LastAttempt, Remarks
                    )
                    VALUES (?, ?, NULL, ?, ?)
                    """,
                    outlet_code, status, now, formatted_remarks,
                )

        return True

    except Exception as e:
        logger.error(f"Error updating sync log for {outlet_code}: {e}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_error:
                logger.warning(
                    f"Error closing log database connection for {outlet_code}: {close_error}"
                )


async def update_product_sync_log(outlet_code: str, status: str, remarks: str) -> bool:
    """Async wrapper for DB sync log update."""
    return await asyncio.to_thread(_update_sync_log_blocking, outlet_code, status, remarks)


# =============================================================================
# ProductPriceChangeLog — Per-row audit trail of price changes
# =============================================================================


def _ensure_price_change_table(cursor) -> None:
    """Create ProductPriceChangeLog table and indexes if they don't exist."""
    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sysobjects
            WHERE name = 'ProductPriceChangeLog' AND xtype = 'U'
        )
        CREATE TABLE ProductPriceChangeLog (
            LogID                   BIGINT IDENTITY(1,1) PRIMARY KEY,
            EventID                 UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RunID                   UNIQUEIDENTIFIER NULL,
            ChangeType              VARCHAR(10) NOT NULL DEFAULT 'UPDATE',
            ProductCode             VARCHAR(20)     NOT NULL,
            DepotCode               VARCHAR(10)     NOT NULL,
            PreviousUnitPrice       DECIMAL(18,4)   NULL,
            PreviousModifiedDate    DATETIME        NULL,
            CurrentUnitPrice        DECIMAL(18,4)   NOT NULL,
            CurrentModifiedDate     DATETIME        NULL,
            ChangeOccurrenceTime    DATETIME        NOT NULL DEFAULT GETDATE(),
            OutletCode              VARCHAR(10)     NULL,
            ChangedBy               VARCHAR(50)     NULL
        )
    """)

    cursor.execute("""
        IF COL_LENGTH('ProductPriceChangeLog', 'EventID') IS NULL
            ALTER TABLE ProductPriceChangeLog ADD EventID UNIQUEIDENTIFIER NULL;
        IF COL_LENGTH('ProductPriceChangeLog', 'RunID') IS NULL
            ALTER TABLE ProductPriceChangeLog ADD RunID UNIQUEIDENTIFIER NULL;
        IF COL_LENGTH('ProductPriceChangeLog', 'ChangeType') IS NULL
            ALTER TABLE ProductPriceChangeLog ADD ChangeType VARCHAR(10) NULL;
        UPDATE ProductPriceChangeLog SET EventID = NEWID() WHERE EventID IS NULL;
        UPDATE ProductPriceChangeLog SET ChangeType = 'UPDATE' WHERE ChangeType IS NULL;
        ALTER TABLE ProductPriceChangeLog ALTER COLUMN EventID UNIQUEIDENTIFIER NOT NULL;
        ALTER TABLE ProductPriceChangeLog ALTER COLUMN ChangeType VARCHAR(10) NOT NULL;
        ALTER TABLE ProductPriceChangeLog ALTER COLUMN CurrentModifiedDate DATETIME NULL;
        IF COL_LENGTH('ProductPriceChangeLog', 'PriceDeltaPercent') IS NOT NULL
            ALTER TABLE ProductPriceChangeLog DROP COLUMN PriceDeltaPercent;
    """)

    # Create composite index for lookup queries
    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'IX_ProductPriceChangeLog_ProductDepot'
            AND object_id = OBJECT_ID('ProductPriceChangeLog')
        )
        CREATE INDEX IX_ProductPriceChangeLog_ProductDepot
            ON ProductPriceChangeLog (ProductCode, DepotCode, ChangeOccurrenceTime DESC)
    """)

    # Create dedicated time-range index for cleanup queries
    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'IX_ProductPriceChangeLog_ChangeOccurrenceTime'
            AND object_id = OBJECT_ID('ProductPriceChangeLog')
        )
        CREATE INDEX IX_ProductPriceChangeLog_ChangeOccurrenceTime
            ON ProductPriceChangeLog (ChangeOccurrenceTime DESC)
    """)

    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'UX_ProductPriceChangeLog_EventID'
            AND object_id = OBJECT_ID('ProductPriceChangeLog')
        )
        CREATE UNIQUE INDEX UX_ProductPriceChangeLog_EventID
            ON ProductPriceChangeLog (EventID)
    """)


def _ensure_sync_history_table(cursor) -> None:
    """Create append-only per-outlet synchronization history."""
    cursor.execute("""
        IF OBJECT_ID('ProductSyncLogHistory', 'U') IS NULL
        CREATE TABLE ProductSyncLogHistory (
            HistoryID       BIGINT IDENTITY(1,1) PRIMARY KEY,
            RunID           UNIQUEIDENTIFIER NOT NULL,
            TriggerType     VARCHAR(30) NULL,
            DepotCode       VARCHAR(10) NOT NULL,
            SyncStatus      VARCHAR(30) NOT NULL,
            AuditStatus     VARCHAR(30) NOT NULL,
            CapturedCount   INT NOT NULL DEFAULT 0,
            LoggedCount     INT NOT NULL DEFAULT 0,
            AttemptTime     DATETIME NOT NULL DEFAULT GETDATE(),
            DurationSeconds DECIMAL(18,2) NULL,
            Remarks         VARCHAR(1000) NULL
        );
        IF NOT EXISTS (
            SELECT * FROM sys.indexes
            WHERE name = 'IX_ProductSyncLogHistory_RunID'
            AND object_id = OBJECT_ID('ProductSyncLogHistory')
        )
        CREATE INDEX IX_ProductSyncLogHistory_RunID
            ON ProductSyncLogHistory (RunID, DepotCode);
    """)


def _ensure_service_state_tables(cursor) -> None:
    """Create persistent scheduler, cycle-summary, and retry-queue tables."""
    cursor.execute("""
        IF OBJECT_ID('dbo.ProductSyncServiceState', 'U') IS NULL
        CREATE TABLE dbo.ProductSyncServiceState (
            StateID             TINYINT NOT NULL PRIMARY KEY,
            ScheduleEnabled     BIT NOT NULL DEFAULT 1,
            ScheduleMode        VARCHAR(10) NULL,
            IntervalMinutes     INT NULL,
            ActiveHoursStart    TINYINT NOT NULL DEFAULT 0,
            ActiveMinutesStart  TINYINT NOT NULL DEFAULT 0,
            ActiveHoursEnd      TINYINT NOT NULL DEFAULT 23,
            ActiveMinutesEnd    TINYINT NOT NULL DEFAULT 0,
            ActiveDays          VARCHAR(50) NOT NULL DEFAULT 'mon,tue,wed,thu,fri,sat,sun',
            CronExpression      VARCHAR(100) NULL,
            ScheduleRulesJson   NVARCHAR(MAX) NULL,
            IsConfigured        BIT NOT NULL DEFAULT 0,
            UpdatedAt           DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
            CONSTRAINT CK_ProductSyncServiceState_Singleton CHECK (StateID = 1)
        );
        IF NOT EXISTS (SELECT 1 FROM dbo.ProductSyncServiceState WHERE StateID = 1)
        INSERT INTO dbo.ProductSyncServiceState (StateID) VALUES (1);
        IF COL_LENGTH('dbo.ProductSyncServiceState', 'ScheduleRulesJson') IS NULL
        ALTER TABLE dbo.ProductSyncServiceState ADD ScheduleRulesJson NVARCHAR(MAX) NULL;

        IF OBJECT_ID('dbo.ProductSyncCycle', 'U') IS NULL
        CREATE TABLE dbo.ProductSyncCycle (
            RunID           UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
            TriggerType     VARCHAR(30) NULL,
            StartedAt       DATETIME2 NOT NULL,
            FinishedAt      DATETIME2 NOT NULL,
            Outcome         VARCHAR(30) NOT NULL,
            TotalOutlets    INT NOT NULL DEFAULT 0,
            Successful      INT NOT NULL DEFAULT 0,
            Failed          INT NOT NULL DEFAULT 0,
            Excluded        INT NOT NULL DEFAULT 0,
            Cancelled       INT NOT NULL DEFAULT 0,
            AuditFailed     INT NOT NULL DEFAULT 0,
            RetryQueueSize  INT NOT NULL DEFAULT 0,
            DurationSeconds DECIMAL(18,2) NULL
        );
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes WHERE name = 'IX_ProductSyncCycle_FinishedAt'
            AND object_id = OBJECT_ID('dbo.ProductSyncCycle')
        )
        CREATE INDEX IX_ProductSyncCycle_FinishedAt
            ON dbo.ProductSyncCycle (FinishedAt DESC);

        IF OBJECT_ID('dbo.ProductSyncRetryQueue', 'U') IS NULL
        CREATE TABLE dbo.ProductSyncRetryQueue (
            OutletCode        VARCHAR(10) NOT NULL PRIMARY KEY,
            ServerAddress     VARCHAR(255) NOT NULL,
            Attempt           INT NOT NULL,
            MaxAttempts       INT NOT NULL,
            LastError         NVARCHAR(2000) NULL,
            AddedAt           DATETIME2 NOT NULL,
            NextRetryAt       DATETIME2 NOT NULL,
            PermanentlyFailed BIT NOT NULL DEFAULT 0,
            UpdatedAt         DATETIME2 NOT NULL DEFAULT SYSDATETIME()
        );
    """)


def _insert_price_changes_blocking(
    changes: list[dict], outlet_code: str, run_id: str | None = None
) -> int:
    """
    Insert price change records into ProductPriceChangeLog in the central database.
    Blocking version - run via asyncio.to_thread().
    Returns the number of changes inserted.
    """
    if not changes:
        return 0

    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()

        # Startup normally prepares the schema. This lazy, cached guard covers
        # startup failures without repeating DDL for every outlet completion.
        _ensure_audit_schema_cached(cursor)

        now = datetime.now()
        rows = []
        event_ids = []
        for ch in changes:
            event_id = ch.get("event_id") or str(uuid4())
            change_type = (ch.get("change_type") or "UPDATE").upper()
            if change_type not in {"INSERT", "UPDATE"}:
                raise ValueError(f"Unsupported ProductPrice change type: {change_type}")
            event_ids.append(event_id)
            rows.append(
                (
                    event_id,
                    run_id,
                    change_type,
                    ch["product_code"],
                    ch["depot_code"],
                    ch["old_unit_price"],
                    ch["old_modified_date"],
                    ch["new_unit_price"],
                    ch["new_modified_date"],
                    now,
                    outlet_code,
                    ch.get("modified_by"),
                    event_id,
                )
            )

        if len(set(event_ids)) != len(event_ids):
            raise ValueError("Duplicate ProductPrice audit EventID in one batch")

        insert_sql = """
            INSERT INTO ProductPriceChangeLog (
                EventID, RunID, ChangeType,
                ProductCode, DepotCode,
                PreviousUnitPrice, PreviousModifiedDate,
                CurrentUnitPrice, CurrentModifiedDate,
                ChangeOccurrenceTime, OutletCode, ChangedBy
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM ProductPriceChangeLog WHERE EventID = ?
            )
        """
        cursor.fast_executemany = True
        batch_size = settings.PRICE_CHANGE_INSERT_BATCH_SIZE
        persisted_total = 0

        for start in range(0, len(rows), batch_size):
            row_batch = rows[start:start + batch_size]
            id_batch = event_ids[start:start + batch_size]
            cursor.executemany(insert_sql, row_batch)

            # Verify a whole idempotent batch with one round trip. The batch
            # size cap stays below SQL Server's 2,100-parameter limit.
            placeholders = ",".join("?" for _ in id_batch)
            cursor.execute(
                f"SELECT COUNT(*) FROM ProductPriceChangeLog "
                f"WHERE EventID IN ({placeholders})",
                *id_batch,
            )
            persisted = int(cursor.fetchone()[0])
            if persisted != len(id_batch):
                raise RuntimeError(
                    "ProductPrice audit batch was not persisted exactly once: "
                    f"expected={len(id_batch)}, persisted={persisted}"
                )
            persisted_total += persisted

        logger.info(
            f"Logged {persisted_total} price changes to ProductPriceChangeLog "
            f"for outlet {outlet_code} in "
            f"{(len(rows) + batch_size - 1) // batch_size} batch(es)"
        )
        return persisted_total

    except Exception as e:
        logger.error(
            f"Error inserting price changes for {outlet_code}: {e}"
        )
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_error:
                logger.warning(
                    f"Error closing log connection for price changes: {close_error}"
                )


async def log_product_price_changes(
    changes: list[dict], outlet_code: str, run_id: str | None = None
) -> int:
    """Async wrapper for inserting price change records."""
    if not settings.ENABLE_PRODUCT_PRICE_CHANGE_LOG:
        return 0
    return await asyncio.to_thread(
        _insert_price_changes_blocking, changes, outlet_code, run_id
    )


def _insert_sync_history_blocking(result: dict) -> bool:
    """Append one immutable outlet attempt with audit counts and status."""
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        attempt_time = result.get("timestamp") or datetime.now()
        if isinstance(attempt_time, str):
            attempt_time = datetime.fromisoformat(attempt_time)
        cursor.execute(
            """
            INSERT INTO ProductSyncLogHistory (
                RunID, TriggerType, DepotCode, SyncStatus, AuditStatus,
                CapturedCount, LoggedCount, AttemptTime, DurationSeconds, Remarks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            result["run_id"], result.get("trigger"), result["outlet_code"],
            result["status"], result.get("audit_status", "NotApplicable"),
            result.get("captured_count", 0), result.get("logged_count", 0),
            attempt_time, result.get("duration_seconds"),
            (result.get("remarks") or "")[:1000],
        )
        return True
    finally:
        if conn is not None:
            conn.close()


async def log_sync_history(result: dict) -> bool:
    if not settings.ENABLE_PRODUCT_SYNC_LOG_HISTORY:
        return False
    return await asyncio.to_thread(_insert_sync_history_blocking, result)


def _initialize_audit_schema_blocking() -> None:
    """Apply additive audit schema migrations before dashboard polling starts."""
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
    finally:
        if conn is not None:
            conn.close()


async def initialize_audit_schema() -> None:
    await asyncio.to_thread(_initialize_audit_schema_blocking)


def _save_cycle_summary_blocking(summary: dict) -> None:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        cursor.execute("""
            MERGE dbo.ProductSyncCycle AS T
            USING (SELECT ? AS RunID) AS S ON T.RunID = S.RunID
            WHEN MATCHED THEN UPDATE SET
                TriggerType=?, StartedAt=?, FinishedAt=?, Outcome=?, TotalOutlets=?,
                Successful=?, Failed=?, Excluded=?, Cancelled=?, AuditFailed=?,
                RetryQueueSize=?, DurationSeconds=?
            WHEN NOT MATCHED THEN INSERT (
                RunID, TriggerType, StartedAt, FinishedAt, Outcome, TotalOutlets,
                Successful, Failed, Excluded, Cancelled, AuditFailed,
                RetryQueueSize, DurationSeconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
            summary["run_id"], summary.get("trigger"), summary["started_at"],
            summary["finished_at"], summary["outcome"], summary.get("total_outlets", 0),
            summary.get("successful", 0), summary.get("failed", 0),
            summary.get("excluded", 0), summary.get("cancelled", 0),
            summary.get("audit_failed", 0), summary.get("retry_queue_size", 0),
            summary.get("duration_seconds"),
            summary["run_id"], summary.get("trigger"), summary["started_at"],
            summary["finished_at"], summary["outcome"], summary.get("total_outlets", 0),
            summary.get("successful", 0), summary.get("failed", 0),
            summary.get("excluded", 0), summary.get("cancelled", 0),
            summary.get("audit_failed", 0), summary.get("retry_queue_size", 0),
            summary.get("duration_seconds"),
        )
    finally:
        if conn is not None:
            conn.close()


async def save_cycle_summary(summary: dict) -> None:
    await asyncio.to_thread(_save_cycle_summary_blocking, summary)


def _load_cycle_dashboard_state_blocking() -> dict:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        cursor.execute("""
            SELECT COUNT(*),
                SUM(CASE WHEN Outcome='completed' THEN 1 ELSE 0 END),
                SUM(CASE WHEN Outcome='partial_failure' THEN 1 ELSE 0 END),
                SUM(CASE WHEN Outcome='cancelled' THEN 1 ELSE 0 END),
                SUM(CASE WHEN Outcome='timed_out' THEN 1 ELSE 0 END),
                SUM(CASE WHEN Outcome NOT IN ('completed','partial_failure','cancelled','timed_out') THEN 1 ELSE 0 END)
            FROM dbo.ProductSyncCycle
        """)
        totals = cursor.fetchone()
        cursor.execute("""
            SELECT TOP 1 RunID, TriggerType, StartedAt, FinishedAt, Outcome,
                TotalOutlets, Successful, Failed, Excluded, Cancelled,
                AuditFailed, RetryQueueSize, DurationSeconds
            FROM dbo.ProductSyncCycle ORDER BY FinishedAt DESC, StartedAt DESC
        """)
        last = cursor.fetchone()
        result = {
            "attempted": int(totals[0] or 0), "completed": int(totals[1] or 0),
            "partial_failure": int(totals[2] or 0), "cancelled_total": int(totals[3] or 0),
            "timed_out": int(totals[4] or 0), "failed_total": int(totals[5] or 0),
            "last": None,
        }
        if last:
            result["last"] = {
                "run_id": str(last[0]), "trigger": last[1], "started_at": last[2],
                "finished_at": last[3], "outcome": last[4], "total_outlets": int(last[5]),
                "successful": int(last[6]), "failed": int(last[7]), "excluded": int(last[8]),
                "cancelled": int(last[9]), "audit_failed": int(last[10]),
                "retry_queue_size": int(last[11]), "duration_seconds": float(last[12] or 0),
            }
        return result
    finally:
        if conn is not None:
            conn.close()


async def load_cycle_dashboard_state() -> dict:
    return await asyncio.to_thread(_load_cycle_dashboard_state_blocking)


def _replace_retry_queue_blocking(entries: list[dict]) -> None:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        conn.autocommit = False
        cursor.execute("DELETE FROM dbo.ProductSyncRetryQueue")
        if entries:
            cursor.fast_executemany = True
            cursor.executemany("""
                INSERT INTO dbo.ProductSyncRetryQueue (
                    OutletCode, ServerAddress, Attempt, MaxAttempts, LastError,
                    AddedAt, NextRetryAt, PermanentlyFailed, UpdatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIME())
            """, [(
                entry["outlet_code"], entry["server"], entry["attempt"],
                entry["max_attempts"], entry.get("last_error"),
                datetime.fromisoformat(entry["added_at"]),
                datetime.fromisoformat(entry["next_retry_at"]),
                bool(entry.get("permanently_failed", False)),
            ) for entry in entries])
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


async def replace_retry_queue(entries: list[dict]) -> None:
    await asyncio.to_thread(_replace_retry_queue_blocking, entries)


def _load_retry_queue_blocking() -> list[dict]:
    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        _ensure_audit_schema_cached(cursor)
        cursor.execute("""
            SELECT OutletCode, ServerAddress, Attempt, MaxAttempts, LastError,
                AddedAt, NextRetryAt, PermanentlyFailed
            FROM dbo.ProductSyncRetryQueue ORDER BY AddedAt
        """)
        return [{
            "outlet_code": str(row[0]), "server": str(row[1]), "attempt": int(row[2]),
            "max_attempts": int(row[3]), "last_error": row[4] or "",
            "added_at": row[5].isoformat(), "next_retry_at": row[6].isoformat(),
            "permanently_failed": bool(row[7]),
        } for row in cursor.fetchall()]
    finally:
        if conn is not None:
            conn.close()


async def load_retry_queue() -> list[dict]:
    return await asyncio.to_thread(_load_retry_queue_blocking)


# =============================================================================
# Price Change Log Cleanup — Periodic purge of old records
# =============================================================================

# Number of rows to delete per batch during cleanup to keep transactions small
CLEANUP_BATCH_SIZE = 5000
CLEANUP_MAX_BATCHES = 1000


def _cleanup_price_changes_blocking(
    retention_days: int | None = None,
    batch_size: int = CLEANUP_BATCH_SIZE,
    max_batches: int = CLEANUP_MAX_BATCHES,
) -> int:
    """
    Purge old price change records in **batches** to keep transaction log growth minimal
    and avoid long-running locks on the ProductPriceChangeLog table.

    Uses a WHILE loop with DELETE TOP so each batch commits independently (autocommit mode).
    The dedicated ``IX_ProductPriceChangeLog_ChangeOccurrenceTime`` index makes the
    WHERE clause seekable instead of scanning the clustered index.

    Args:
        retention_days: Age threshold (default from ``settings.PRICE_CHANGE_RETENTION_DAYS``).
        batch_size: Rows to delete per batch (default 5000).

    Returns:
        Total number of rows deleted across all batches.

    Blocking version — run via ``asyncio.to_thread()``.
    """
    if retention_days is None:
        retention_days = settings.PRICE_CHANGE_RETENTION_DAYS
    if not 1 <= retention_days <= 3650:
        raise ValueError("retention_days must be between 1 and 3650")
    if not 1 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 1 and 100000")
    if not 1 <= max_batches <= 10_000:
        raise ValueError("max_batches must be between 1 and 10000")

    conn = None
    try:
        conn = _make_log_connection()
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()

        # Check if table exists before attempting cleanup
        cursor.execute("""
            SELECT COUNT(*) FROM sysobjects
            WHERE name = 'ProductPriceChangeLog' AND xtype = 'U'
        """)
        if cursor.fetchone()[0] == 0:
            logger.info("ProductPriceChangeLog table does not exist yet — skipping cleanup")
            return 0

        total_deleted = 0
        cutoff_days = retention_days

        logger.info(
            f"Starting batched cleanup of ProductPriceChangeLog "
            f"(retention: {cutoff_days}d, batch: {batch_size})"
        )

        batches_run = 0
        while batches_run < max_batches:
            cursor.execute(
                """
                DELETE TOP (?)
                FROM ProductPriceChangeLog
                WHERE ChangeOccurrenceTime < DATEADD(DAY, -?, GETDATE())
                """,
                batch_size,
                cutoff_days,
            )
            deleted_in_batch = cursor.rowcount
            batches_run += 1
            total_deleted += deleted_in_batch

            if deleted_in_batch < batch_size:
                break
        else:
            raise RuntimeError(
                f"Cleanup safety limit reached after {max_batches} batches; "
                f"{total_deleted} rows were deleted before the stop"
            )

        logger.info(
            f"Cleaned up {total_deleted} price change records older than {cutoff_days} days "
            f"(batched, {batch_size}/batch)"
        )
        return total_deleted

    except Exception as e:
        logger.error(f"Error cleaning up ProductPriceChangeLog: {e}")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_error:
                logger.warning(
                    f"Error closing log connection during price change cleanup: {close_error}"
                )


async def cleanup_price_changes(
    retention_days: int | None = None,
    batch_size: int = CLEANUP_BATCH_SIZE,
    max_batches: int = CLEANUP_MAX_BATCHES,
) -> int:
    """Async wrapper for purging old price change records."""
    return await asyncio.to_thread(
        _cleanup_price_changes_blocking, retention_days, batch_size, max_batches
    )

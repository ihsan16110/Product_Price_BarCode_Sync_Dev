import asyncio
import time
from datetime import datetime
from uuid import uuid4

from app.config import settings
from app.database import make_connection
from app.logger import get_logger
from app.db_logger import (
    log_product_price_changes,
    log_sync_history,
    update_product_sync_log,
)
from app.sync_sql import (
    LINKED_SERVER_CHECK_SQL,
    LINKED_SERVER_CREATE_TEMPLATE,
    get_sync_sql,
)

logger = get_logger(__name__)

PRICE_CHANGES_MARKER = "PRICE_CHANGES"
PRICE_CHANGE_SUMMARY_MARKER = "PRICE_CHANGE_SUMMARY"


def _extract_price_changes(cursor) -> tuple[list[dict], int]:
    """Read and validate the audit summary/details from all ODBC result sets."""
    price_changes: list[dict] = []
    captured_count: int | None = None

    while True:
        if cursor.description:
            rows = cursor.fetchall()
            if rows:
                marker = str(rows[0][0])
                if marker == PRICE_CHANGE_SUMMARY_MARKER:
                    captured_count = int(rows[0][1])
                elif marker == PRICE_CHANGES_MARKER:
                    for row in rows:
                        price_changes.append({
                            "event_id": str(uuid4()),
                            "change_type": str(row[1]),
                            "product_code": str(row[2]),
                            "depot_code": str(row[3]) if row[3] else "",
                            "old_unit_price": float(row[4]) if row[4] is not None else None,
                            "new_unit_price": float(row[5]) if row[5] is not None else None,
                            # Keep database timestamps as native datetime objects so
                            # pyodbc can bind them directly to SQL Server date columns.
                            "old_modified_date": row[6] if row[6] else None,
                            "new_modified_date": row[7] if row[7] else None,
                            "modified_by": str(row[8]) if row[8] else None,
                        })
        if not cursor.nextset():
            break

    if captured_count is None:
        raise RuntimeError("ProductPrice audit summary result set was not returned")
    if captured_count != len(price_changes):
        raise RuntimeError(
            f"ProductPrice audit count mismatch: captured={captured_count}, "
            f"returned={len(price_changes)}"
        )
    return price_changes, captured_count


def _run_on_outlet_blocking(outlet: dict) -> dict:
    """
    Execute the sync SQL batch on a single outlet server.
    Blocking version - run via asyncio.to_thread().
    Captures price change data from the marker result set for audit logging.
    Returns a result dict with outlet_code, ip, status, remarks, timestamp, duration,
    and optionally price_changes (list of dicts).
    """
    name = outlet.get("Outlet", "Unknown")
    run_id = str(outlet.get("_run_id") or uuid4())
    trigger = str(outlet.get("_trigger") or "unknown")
    server = outlet.get("Server")
    db_name = outlet.get("Database", settings.LOCAL_DB)
    user = outlet.get("User", settings.OUTLET_DB_USER)
    password = outlet.get("Password", settings.OUTLET_DB_PASSWORD)

    start_time = time.time()

    if not server:
        msg = "Missing server IP/hostname for outlet"
        logger.error(f"{msg} [{name}]")
        return {
            "outlet_code": name,
            "ip": "N/A",
            "status": "N",
            "remarks": msg,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": 0.0,
            "price_changes": [],
            "run_id": run_id,
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
        }

    conn = None
    try:
        logger.info(f"Starting sync for outlet {name} at {server}")

        conn = make_connection(
            server=server,
            database=db_name,
            user=user,
            password=password,
            autocommit=False,
        )
        # pyodbc applies the connection's query timeout to cursors when they
        # are created. The timeout passed to connect() only covers login.
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()

        # Ensure linked server exists on outlet
        cursor.execute(LINKED_SERVER_CHECK_SQL, settings.CENTRAL_LINKED_SERVER_NAME)
        if cursor.fetchone()[0] == 0:
            create_ls_sql = LINKED_SERVER_CREATE_TEMPLATE.format(
                linked_server_name=settings.CENTRAL_LINKED_SERVER_NAME
            )
            cursor.execute(create_ls_sql)

        # Keep linked-server administration outside the outlet data transaction.
        conn.commit()

        # Execute the complete sync SQL as one batch
        sync_sql = get_sync_sql(
            local_db=settings.LOCAL_DB,
            central_linked_server=settings.CENTRAL_LINKED_SERVER_NAME,
            central_db=settings.CENTRAL_DB,
            outlet_code=name,
        )
        cursor.execute(sync_sql)

        price_changes, captured_count = _extract_price_changes(cursor)
        logger.info(
            f"RunId={run_id} Outlet={name} CapturedCount={captured_count} "
            f"INSERT={sum(c['change_type'] == 'INSERT' for c in price_changes)} "
            f"UPDATE={sum(c['change_type'] == 'UPDATE' for c in price_changes)}"
        )

        # Product, ProductPrice, and ProductBarcode changes succeed or roll back
        # as one unit. The SQL batch itself remains unchanged.
        conn.commit()

        duration = time.time() - start_time
        logger.info(f"Sync successful for {name} ({duration:.1f}s)")
        return {
            "outlet_code": name,
            "ip": server,
            "status": "Success",
            "remarks": "Y",
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": round(duration, 2),
            "price_changes": price_changes,
            "run_id": run_id,
            "trigger": trigger,
            "captured_count": captured_count,
            "logged_count": 0,
            "audit_status": "Pending" if captured_count else "NoChanges",
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.error(f"Rollback failed for {name}: {rollback_error}")
                error_msg = f"{error_msg}; rollback failed: {rollback_error}"
        logger.error(f"Sync failed for {name} at {server}: {error_msg}")
        return {
            "outlet_code": name,
            "ip": server,
            "status": "N",
            "remarks": error_msg,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": round(duration, 2),
            "price_changes": [],
            "run_id": run_id,
            "trigger": trigger,
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_error:
                logger.warning(f"Error closing connection for {name}: {close_error}")


async def run_on_outlet(outlet: dict) -> dict:
    """Async wrapper for the blocking outlet sync operation."""
    result = await asyncio.to_thread(_run_on_outlet_blocking, outlet)

    if result["status"] == "Success":
        price_changes = result.get("price_changes", [])
        try:
            logged_count = 0
            if price_changes:
                logged_count = await log_product_price_changes(
                    changes=price_changes,
                    outlet_code=result["outlet_code"],
                    run_id=result["run_id"],
                )
            result["logged_count"] = logged_count
            if logged_count != result["captured_count"]:
                raise RuntimeError(
                    f"audit count mismatch: captured={result['captured_count']}, "
                    f"logged={logged_count}"
                )
            result["audit_status"] = "Logged" if logged_count else "NoChanges"
        except Exception as audit_error:
            result["status"] = "Partial"
            result["audit_status"] = "AuditFailed"
            result["remarks"] = f"Outlet sync succeeded; audit logging failed: {audit_error}"

    summary_written = await update_product_sync_log(
        outlet_code=result["outlet_code"],
        status=result["status"],
        remarks=result["remarks"],
    )
    if not summary_written:
        logger.error(
            f"RunId={result['run_id']} Outlet={result['outlet_code']} "
            "ProductSyncLog update failed"
        )
    try:
        await log_sync_history(result)
    except Exception as history_error:
        logger.error(
            f"RunId={result['run_id']} Outlet={result['outlet_code']} "
            f"ProductSyncLogHistory write failed: {history_error}"
        )

    logger.info(
        f"RunId={result['run_id']} Outlet={result['outlet_code']} "
        f"SyncStatus={result['status']} AuditStatus={result['audit_status']} "
        f"CapturedCount={result['captured_count']} LoggedCount={result['logged_count']}"
    )

    return result

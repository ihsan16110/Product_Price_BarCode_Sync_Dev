import asyncio
import time
from datetime import datetime
from uuid import uuid4

from app.config import settings
from app.database import make_connection
from app.logger import get_logger
from app.db_logger import (
    log_sync_history,
    update_product_sync_log,
)
from app.sync_sql import (
    LINKED_SERVER_CHECK_SQL,
    LINKED_SERVER_CREATE_TEMPLATE,
    get_sync_sql,
)

logger = get_logger(__name__)

HO_ACKNOWLEDGEMENTS_MARKER = "HO_ACKNOWLEDGEMENTS"
HO_ACK_SUMMARY_MARKER = "HO_ACK_SUMMARY"


def _extract_ho_acknowledgements(cursor) -> tuple[list[tuple[str, str]], int]:
    """Read and validate HO acknowledgement keys from all ODBC result sets."""
    acknowledgements: list[tuple[str, str]] = []
    expected_count: int | None = None

    while True:
        if cursor.description:
            rows = cursor.fetchall()
            if rows:
                marker = str(rows[0][0])
                if marker == HO_ACK_SUMMARY_MARKER:
                    expected_count = int(rows[0][1])
                elif marker == HO_ACKNOWLEDGEMENTS_MARKER:
                    for row in rows:
                        product_code = str(row[1]).strip() if row[1] is not None else ""
                        depot_code = str(row[2]).strip() if row[2] is not None else ""
                        if not product_code or not depot_code:
                            raise RuntimeError("HO acknowledgement contains an empty key")
                        acknowledgements.append((product_code, depot_code))
        if not cursor.nextset():
            break

    if expected_count is None:
        raise RuntimeError("HO acknowledgement summary result set was not returned")
    if expected_count != len(acknowledgements):
        raise RuntimeError(
            f"HO acknowledgement count mismatch: expected={expected_count}, "
            f"returned={len(acknowledgements)}"
        )
    return acknowledgements, expected_count


def _acknowledge_ho_blocking(acknowledgements: list[tuple[str, str]]) -> int:
    """Mark exact RepProductPrice keys sent after the outlet commit succeeds."""
    if not acknowledgements:
        return 0

    conn = None
    unique_keys = list(dict.fromkeys(acknowledgements))
    try:
        conn = make_connection(
            server=settings.HO_SERVER,
            database=settings.HO_DATABASE,
            user=settings.HO_DB_USERNAME,
            password=settings.HO_DB_PASSWORD,
            autocommit=False,
        )
        conn.timeout = settings.QUERY_TIMEOUT
        cursor = conn.cursor()
        cursor.fast_executemany = True
        cursor.executemany(
            """
            UPDATE dbo.RepProductPrice
            SET SyncStatus = 'Y', SentTime = GETDATE()
            WHERE SyncStatus = 'N'
              AND ProductCode = ?
              AND DepotCode = ?
            """,
            unique_keys,
        )
        conn.commit()
        return len(unique_keys)
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.error(f"Head Office acknowledgement rollback failed: {rollback_error}")
        raise
    finally:
        if conn is not None:
            conn.close()


def _run_on_outlet_blocking(outlet: dict) -> dict:
    """
    Execute the sync SQL batch on a single outlet server.
    Blocking version - run via asyncio.to_thread().
    Captures Head Office acknowledgement keys from the marker result set.
    Returns a result dict with outlet_code, ip, status, remarks, timestamp, duration,
    plus the acknowledgement keys consumed by the async wrapper.
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
            "run_id": run_id,
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
            "ho_ack_status": "NotAttempted",
            "ho_ack_count": 0,
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

        acknowledgements, acknowledgement_count = _extract_ho_acknowledgements(cursor)
        logger.info(
            f"RunId={run_id} Outlet={name} "
            f"HOAcknowledgementCount={acknowledgement_count}"
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
            "ho_acknowledgements": acknowledgements,
            "run_id": run_id,
            "trigger": trigger,
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "Disabled",
            "ho_ack_status": "Pending" if acknowledgement_count else "NoData",
            "ho_ack_count": 0,
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
            "run_id": run_id,
            "trigger": trigger,
            "captured_count": 0,
            "logged_count": 0,
            "audit_status": "NotApplicable",
            "ho_ack_status": "NotAttempted",
            "ho_ack_count": 0,
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
        acknowledgements = result.pop("ho_acknowledgements", [])
        try:
            acknowledged_count = 0
            if acknowledgements:
                acknowledged_count = await asyncio.to_thread(
                    _acknowledge_ho_blocking, acknowledgements
                )
            result["ho_ack_count"] = acknowledged_count
            result["ho_ack_status"] = "Acknowledged" if acknowledged_count else "NoData"
        except Exception as acknowledgement_error:
            result["status"] = "Partial"
            result["ho_ack_status"] = "Failed"
            result["remarks"] = (
                "Outlet sync succeeded; Head Office acknowledgement failed: "
                f"{acknowledgement_error}"
            )

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
    if settings.ENABLE_PRODUCT_SYNC_LOG_HISTORY:
        try:
            await log_sync_history(result)
        except Exception as history_error:
            logger.error(
                f"RunId={result['run_id']} Outlet={result['outlet_code']} "
                f"ProductSyncLogHistory write failed: {history_error}"
            )

    logger.info(
        f"RunId={result['run_id']} Outlet={result['outlet_code']} "
        f"SyncStatus={result['status']} HOAckStatus={result['ho_ack_status']} "
        f"HOAckCount={result['ho_ack_count']}"
    )

    return result

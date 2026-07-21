import asyncio

import pandas as pd
import pyodbc

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


def make_connection(
    server: str,
    database: str,
    user: str,
    password: str,
    autocommit: bool = True,
    timeout: int | None = None,
) -> pyodbc.Connection:
    """
    Create a pyodbc connection with a standard SQL Server connection string.
    Uses TCP prefix to avoid Named Pipes issues where possible.
    """
    if timeout is None:
        timeout = settings.CONNECT_TIMEOUT
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER=tcp:{server};"
        f"DATABASE={database};"
        f"UID={user};PWD={password};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, autocommit=autocommit, timeout=timeout)


def _load_outlet_data_sync() -> pd.DataFrame:
    """Fetch active depot list and IPs from central database (blocking)."""
    logger.info("Connecting to central DB to load outlets...")
    conn = None
    try:
        conn = make_connection(
            server=settings.SOURCE_SERVER,
            database=settings.SOURCE_DATABASE,
            user=settings.SOURCE_USER,
            password=settings.SOURCE_PASSWORD,
            autocommit=False,
        )
        conn.timeout = settings.QUERY_TIMEOUT
        query = """
            SELECT
                D.DepotCode AS OutletID,
                DI.IPAddress AS Server
            FROM Depot D
            INNER JOIN DepotIP DI ON D.DepotCode = DI.DepotCode
            WHERE D.ActiveDepot = 'Y'
        """
        df = pd.read_sql(query, conn)
    finally:
        if conn is not None:
            conn.close()

    df.columns = [c.strip().lower() for c in df.columns]
    logger.info(f"Loaded {len(df)} outlets from central DB")
    return df


async def load_outlet_data() -> pd.DataFrame:
    """Async wrapper around the blocking outlet data load."""
    return await asyncio.to_thread(_load_outlet_data_sync)


def build_outlet_list(df: pd.DataFrame) -> list[dict]:
    """
    Convert outlet DataFrame into a list of standardized dicts.
    """
    outlets = []
    for _, row in df.iterrows():
        outlet_code = row.get("outletid") or row.get("outlet") or ""
        server_ip = row.get("server") or row.get("ipaddress") or ""

        if not outlet_code or not server_ip:
            continue

        outlets.append(
            {
                "Outlet": str(outlet_code).strip(),
                "Server": str(server_ip).strip(),
                "Database": settings.LOCAL_DB,
                "User": settings.OUTLET_DB_USER,
                "Password": settings.OUTLET_DB_PASSWORD,
            }
        )

    return outlets

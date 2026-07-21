from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.logger import get_logger
from app.config import settings
from app.db_logger import cleanup_price_changes, CLEANUP_BATCH_SIZE, CLEANUP_MAX_BATCHES
from app.security import limit_expensive_operation, require_operator

router = APIRouter(prefix="/ProductSync/api/cleanup", tags=["cleanup"])
logger = get_logger(__name__)


class CleanupRequest(BaseModel):
    retention_days: int = Field(default_factory=lambda: settings.PRICE_CHANGE_RETENTION_DAYS, ge=1, le=3650)
    batch_size: int = Field(default=CLEANUP_BATCH_SIZE, ge=1, le=100_000)
    max_batches: int = Field(default=CLEANUP_MAX_BATCHES, ge=1, le=10_000)


@router.post("/price-changes", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def trigger_price_change_cleanup(request: CleanupRequest | None = None):
    """
    Manually trigger a cleanup of the ProductPriceChangeLog table.

    Deletes records older than ``retention_days`` in batches of ``batch_size``
    to keep transaction log growth minimal.

    **Query / Body Parameters** (all optional):
    - ``retention_days`` — age threshold; defaults to service setting (90 days)
    - ``batch_size`` — rows per batch; defaults to 5000

    **Response:**
    - ``status`` — ``"ok"`` or ``"error"``
    - ``deleted`` — total rows removed
    - ``retention_days`` — the threshold that was applied
    - ``batch_size`` — the batch size that was used
    """
    try:
        request = request or CleanupRequest()
        retention = request.retention_days
        batch = request.batch_size

        deleted = await cleanup_price_changes(
            retention_days=retention,
            batch_size=batch,
            max_batches=request.max_batches,
        )

        return {
            "status": "ok",
            "deleted": deleted,
            "retention_days": retention,
            "batch_size": batch,
            "max_batches": request.max_batches,
        }
    except Exception as e:
        logger.error(f"Manual cleanup triggered error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

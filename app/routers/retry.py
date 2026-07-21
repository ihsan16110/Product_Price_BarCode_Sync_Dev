import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.logger import get_logger
from app.security import limit_expensive_operation, require_operator

router = APIRouter(prefix="/ProductSync/api/retries", tags=["retries"])
logger = get_logger(__name__)

# Injected at startup
sync_manager = None


@router.get("")
async def get_retry_queue():
    """Get current retry queue contents."""
    if sync_manager is None:
        return {"entries": [], "size": 0}

    entries = sync_manager.retry_queue.get_all()
    return {
        "entries": entries,
        "size": sync_manager.retry_queue.size,
        "pending": sync_manager.retry_queue.pending_count,
    }


@router.post("/process-now", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def process_retries_now():
    """Force process all due retries immediately."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    due = sync_manager.retry_queue.get_due()
    if not due:
        return {"message": "No retries due", "processed": 0}

    count = len(due)
    logger.info(f"Force processing {count} due retries")

    for entry in due:
        sync_manager.retry_queue._queue.pop(entry.outlet_code, None)
        asyncio.create_task(sync_manager.retry_single_outlet(entry))

    return {"message": f"Processing {count} retries", "processed": count}


@router.delete("", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def clear_retry_queue():
    """Clear the entire retry queue."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    size = await sync_manager.clear_retry_queue()
    return {"message": f"Retry queue cleared", "removed": size}

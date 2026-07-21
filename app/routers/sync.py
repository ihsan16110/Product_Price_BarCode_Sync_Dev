import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.logger import get_logger
from app.security import limit_expensive_operation, require_operator

router = APIRouter(prefix="/ProductSync/api/sync", tags=["sync"])
logger = get_logger(__name__)

# sync_manager is injected at startup from main.py
sync_manager = None


@router.post("/start", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def start_full_sync():
    """Trigger a full sync cycle across all outlets."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    if sync_manager.current_cycle_task and not sync_manager.current_cycle_task.done():
        active = ", ".join(sorted(sync_manager.active_outlets)) or "unknown"
        logger.warning(
            f"Rejected manual sync trigger: a cycle is already running; "
            f"active outlets: {active}"
        )
        raise HTTPException(status_code=409, detail="Sync cycle already in progress")

    # Run in background so the API responds immediately. SyncManager retains
    # the task so an operator can request graceful cancellation later.
    task = sync_manager.start_full_sync(trigger="manual")
    if task is None:
        raise HTTPException(status_code=409, detail="Sync cycle already in progress")

    return {
        "message": "Sync cycle started",
        "trigger": "manual",
    }


@router.post("/stop", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def stop_full_sync():
    """Request graceful cancellation of the current full synchronization cycle."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return sync_manager.request_cancellation()


@router.post("/outlet/{outlet_code}", dependencies=[Depends(require_operator), Depends(limit_expensive_operation)])
async def sync_single_outlet(outlet_code: str):
    """Trigger sync for a single outlet by its code."""
    if sync_manager is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    result = await sync_manager.sync_single_outlet(outlet_code)

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result["error"])
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result["error"])

    return result

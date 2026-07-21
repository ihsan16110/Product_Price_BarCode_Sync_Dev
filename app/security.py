"""API-key authorization and lightweight protection for expensive operations."""

import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_request_times: dict[str, deque[float]] = defaultdict(deque)


def _matches(candidate: str | None, configured: str) -> bool:
    return bool(candidate and configured and secrets.compare_digest(candidate, configured))


async def require_operator(api_key: str | None = Security(_api_key_header)) -> str:
    """Require the configured administrator/operator API key."""
    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative API is disabled until ADMIN_API_KEY is configured",
        )
    if not _matches(api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return "operator"


async def require_viewer(api_key: str | None = Security(_api_key_header)) -> str:
    """Allow either the viewer key or the administrator key."""
    if not settings.VIEWER_API_KEY and not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Log access is disabled until an API key is configured",
        )
    if _matches(api_key, settings.VIEWER_API_KEY) or _matches(api_key, settings.ADMIN_API_KEY):
        return "viewer"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def limit_expensive_operation(request: Request) -> None:
    """In-process per-client limit; the reverse proxy should enforce the outer limit."""
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window_start = now - 60
    entries = _request_times[client]
    while entries and entries[0] < window_start:
        entries.popleft()
    if len(entries) >= settings.ADMIN_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Administrative request rate limit exceeded")
    entries.append(now)

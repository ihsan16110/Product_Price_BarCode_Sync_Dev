from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


client = TestClient(app)


def test_health_endpoint_is_public():
    response = client.get("/ProductSync/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_archive_log_route_is_unambiguous(tmp_path):
    log_file = tmp_path / "ProductSyncLog 2026-07-17.log"
    log_file.write_text("archive line\n", encoding="utf-8")

    with (
        patch.object(settings, "LOG_DIR", str(tmp_path)),
        patch.object(settings, "VIEWER_API_KEY", "viewer-test-key"),
    ):
        response = client.get(
            "/ProductSync/api/logs/archive/2026-07-17",
            headers={"X-API-Key": "viewer-test-key"},
        )

    assert response.status_code == 200
    assert response.text == "archive line\n"


def test_price_change_route_is_not_interpreted_as_a_date():
    with (
        patch.object(settings, "VIEWER_API_KEY", "viewer-test-key"),
        patch(
            "app.routers.status._fetch_price_changes",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.get(
            "/ProductSync/api/logs/price-changes",
            headers={"X-API-Key": "viewer-test-key"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "changes": [],
        "count": 0,
        "filters": {"outlet_code": None, "days": 7, "limit": 100},
    }

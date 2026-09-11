from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.mark.parametrize("origin", ["http://localhost:4173", "http://127.0.0.1:4173"])
def test_demo_frontend_origin_is_allowed_by_cors(origin: str):
    response = TestClient(app).options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin

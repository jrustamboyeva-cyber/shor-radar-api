from pathlib import Path
import sys

from fastapi.testclient import TestClient

# Ensure the repository root (where main.py lives) is importable under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


client = TestClient(main.app)


def test_health_endpoint_is_ready():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_salinity_window_is_bounded():
    too_short = client.get("/salinity?lat=41.3&lon=69.2&days=0")
    too_long = client.get("/salinity?lat=41.3&lon=69.2&days=366")
    assert too_short.status_code == 422
    assert too_long.status_code == 422


def test_integration_probe_is_post_only_and_rate_limited():
    main._verify_attempts.clear()
    headers = {"x-forwarded-for": "198.51.100.10"}

    assert client.get("/health/verify").status_code == 405
    first = client.post("/health/verify", headers=headers)
    second = client.post("/health/verify", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    main._verify_attempts.clear()


def test_integration_health_does_not_expose_credentials():
    response = client.get("/health/integrations")
    assert response.status_code == 200
    payload = response.json()
    assert "GEMINI_API_KEY" not in str(payload)
    assert "CDSE_CLIENT_SECRET" not in str(payload)

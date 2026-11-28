"""Tests for rate limiting, recommendations, analysis and chat. Upstream services are faked;
these tests never contact Gemini or Copernicus."""
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat  # noqa: E402
import history  # noqa: E402
import main  # noqa: E402
import sentinel  # noqa: E402

client = TestClient(main.app)


def stats_entry(si=0.21, ndvi=0.12, ndmi=-0.05, samples=154, no_data=4, month="2026-10-01"):
    def band(mean):
        return {"bands": {"B0": {"stats": {"mean": mean, "sampleCount": samples, "noDataCount": no_data}}}}
    return {"interval": {"from": month + "T00:00:00Z", "to": month[:8] + "28T00:00:00Z"},
            "outputs": {"si": band(si), "ndvi": band(ndvi), "ndmi": band(ndmi)}}


@pytest.fixture(autouse=True)
def reset_state():
    chat._cnt.clear()
    main._cache.clear()
    history._cache.clear()
    main._verify_attempts.clear()
    yield


# ---------- client IP / rate limiting ----------

def test_client_ip_ignores_forged_left_entries():
    from security import client_ip

    class Req:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff}
            self.client = None
    assert client_ip(Req("1.1.1.1, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(Req("203.0.113.9")) == "203.0.113.9"


class FakeGemini:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        FakeGemini.calls += 1
        FakeGemini.last = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Javob"}]}]})


def test_chat_limit_cannot_be_bypassed_with_forged_header(monkeypatch):
    monkeypatch.setattr(chat, "KEY", "test-key")
    monkeypatch.setattr(chat, "LIMIT", 2)
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeGemini)
    codes = []
    for forged in ("9.9.9.1", "9.9.9.2", "9.9.9.3"):
        r = client.post("/chat", json={"q": "salom"}, headers={"x-forwarded-for": forged + ", 198.51.100.7"})
        codes.append(r.status_code)
    assert codes == [200, 200, 429]


def test_chat_without_key_reports_in_requested_language(monkeypatch):
    monkeypatch.setattr(chat, "KEY", "")
    r = client.post("/chat", json={"q": "?", "language": "ru"})
    assert r.status_code == 503
    assert "GEMINI_API_KEY" in r.json()["detail"] and "ИИ" in r.json()["detail"]


def test_chat_drops_client_error_messages_and_sends_field_context(monkeypatch):
    monkeypatch.setattr(chat, "KEY", "test-key")
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeGemini)
    r = client.post("/chat", json={
        "q": "Qancha suv kerak?", "language": "kaa",
        "field": {"n": "1-dala", "ndmi": -0.04, "ec": 9.5},
        "history": [{"role": "user", "content": "oldingi"},
                    {"role": "bot", "content": "Xatolik: server", "error": True}]})
    assert r.status_code == 200
    body = FakeGemini.last
    assert "Xatolik" not in body["input"]
    assert "NDMI=-0.04" in body["system_instruction"]
    assert "Qaraqalpaq" in body["system_instruction"]
    assert r.json()["answer"] == "Javob"


# ---------- recommendations ----------

def test_recommendations_without_ec_asks_for_measurement():
    r = client.post("/recommendations", json={"crop": "Paxta", "language": "uz"})
    data = r.json()
    assert r.status_code == 200
    assert data["ec"] is None and data["leaching"] is None
    assert "EC" in data["tips"][0]


def test_recommendations_cotton_ec10_matches_fao():
    data = client.post("/recommendations", json={"crop": "cotton", "ec": 10, "area_ha": 2, "soil_texture": "loam"}).json()
    assert data["salinity_class"] == "strong"
    assert data["yield_loss_percent"] == pytest.approx(12.0, abs=0.1)  # (10-7.7)*5.2
    lw = data["leaching"]
    assert lw["needed"] and lw["target_ec"] == 7.7
    # 0.6 m * 0.3 * 10/7.7 = 0.2338 m -> 2338 m³/ha -> rounded to 50
    assert lw["m3_per_ha"] == 2350 and lw["total_m3"] == 4700


def test_recommendations_low_ec_no_leaching_russian():
    data = client.post("/recommendations", json={"crop": "wheat", "ec": 3, "language": "ru"}).json()
    assert data["leaching"] == {"needed": False, "target_ec": 6.0}
    assert "Промывка не требуется" in data["tips"][0]


def test_maintenance_leaching_requirement_and_weekly_water():
    data = client.post("/recommendations", json={"crop": "cotton", "water_ec": 1.5, "et0_week_mm": 35}).json()
    m = data["maintenance"]
    assert m["leaching_fraction"] == pytest.approx(1.5 / (5 * 7.7 - 1.5), abs=1e-3)
    assert m["week_mm"] == pytest.approx(35 * 1.15 / (1 - m["leaching_fraction"]), abs=0.2)


def test_unusable_irrigation_water_is_flagged():
    data = client.post("/recommendations", json={"crop": "onion", "water_ec": 7}).json()
    assert data["maintenance"]["usable"] is False


def test_recommendations_reject_bad_numbers_and_flag_drainage():
    assert client.post("/recommendations", json={"ec": -1}).status_code == 422
    assert client.post("/recommendations", json={"ec": "abc"}).status_code == 422
    data = client.post("/recommendations", json={"ec": 12, "drainage": "none", "groundwater_m": 1.2}).json()
    assert any("Drenaj yo‘q" in tip for tip in data["tips"])
    assert any("Sizot suvi 1.2" in tip for tip in data["tips"])


def test_crops_endpoint_is_localized():
    names = {c["id"]: c["name"] for c in client.get("/crops?language=kaa").json()}
    assert names["wheat"] == "Biyday"


# ---------- analysis ----------

def test_analyze_polygon_returns_indices_and_caches(monkeypatch):
    calls = []

    async def fake_query(body, timeout=60):
        calls.append(body)
        assert body["input"]["bounds"]["geometry"]["type"] == "Polygon"
        return [stats_entry()]
    monkeypatch.setattr(sentinel, "query", fake_query)
    polygon = [[41.30, 60.90], [41.30, 60.91], [41.31, 60.91], [41.31, 60.90]]
    first = client.post("/analyze", json={"polygon": polygon})
    second = client.post("/analyze", json={"polygon": polygon})
    assert first.status_code == 200
    data = first.json()
    assert data["mode"] == "polygon" and data["ndmi"] == -0.05 and data["valid_pixels"] == 150
    assert second.json() == data and len(calls) == 1


def test_analyze_widens_window_when_no_data(monkeypatch):
    windows = []

    async def fake_query(body, timeout=60):
        windows.append(body["aggregation"]["aggregationInterval"]["of"])
        return [] if len(windows) < 3 else [stats_entry()]
    monkeypatch.setattr(sentinel, "query", fake_query)
    r = client.get("/salinity?lat=41.3&lon=60.9&days=60")
    assert r.status_code == 200 and r.json()["period_days"] == 180
    assert windows == ["P60D", "P120D", "P180D"]


def test_polygon_validation():
    assert client.post("/analyze", json={"polygon": [[41.3, 60.9], [41.31, 60.9]]}).status_code == 422
    huge = [[41.0, 60.0], [41.0, 61.0], [42.0, 61.0]]
    assert client.post("/analyze", json={"polygon": huge}).status_code == 422
    assert client.post("/analyze", json={"lat": 95, "lon": 60}).status_code == 422


def test_history_returns_monthly_points(monkeypatch):
    async def fake_query(body, timeout=60):
        return [stats_entry(month="2026-09-01"), {"interval": {"from": "2026-10-01T00:00:00Z"}, "outputs": {}}]
    monkeypatch.setattr(sentinel, "query", fake_query)
    data = client.get("/history?lat=41.3&lon=60.9&months=3").json()
    assert data["points"][0]["month"] == "2026-09" and data["points"][0]["ndmi"] == -0.05
    assert data["points"][1]["si"] is None


def test_missing_copernicus_credentials_is_503(monkeypatch):
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    sentinel._token.update(value=None, expires=0)
    r = client.get("/salinity?lat=41.3&lon=60.9")
    assert r.status_code == 503


def test_first_verify_is_allowed_right_after_boot(monkeypatch):
    # Regression: on a freshly booted machine time.monotonic() can be below the cooldown.
    monkeypatch.setattr(main.time, "monotonic", lambda: 5.0)
    first = client.post("/health/verify", headers={"x-forwarded-for": "203.0.113.50"})
    second = client.post("/health/verify", headers={"x-forwarded-for": "203.0.113.50"})
    assert first.status_code == 200 and second.status_code == 429

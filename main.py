import os, time, datetime as dt
import base64, io
import httpx

from history import router as history_router
from chat import router as chat_router
from recommendations import router as recommendations_router
import sentinel
from security import BoundedCache, client_ip

from PIL import Image
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

TOKEN_URL = sentinel.TOKEN_URL
STAT_URL = sentinel.STAT_URL
EVALSCRIPT = sentinel.EVALSCRIPT

# Browsers only send cookies/credentials to allowed origins; this API uses none,
# so a wildcard is safe. Set CORS_ORIGINS="https://a,https://b" to restrict it.
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app = FastAPI(title="Shor Radar API")
app.add_middleware(CORSMiddleware, allow_origins=_origins or ["*"], allow_methods=["GET", "POST", "HEAD", "OPTIONS"], allow_headers=["*"])
app.include_router(history_router)
app.include_router(chat_router)
app.include_router(recommendations_router)
_cache = BoundedCache(max_items=1024, ttl_seconds=12 * 3600)
_verify_attempts = {}
VERIFY_COOLDOWN_SECONDS = 30

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"ok": True, "service": "Shor Radar API"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True, "service": "Shor Radar API"}

@app.get("/health/integrations")
def integration_health():
    # Never return credentials or connection strings from this public endpoint.
    gemini_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    copernicus_credentials = sentinel.credentials_configured()
    database_url = os.environ.get("DATABASE_URL", "").strip()

    return {
        "api": "ready",
        "gemini_configured": gemini_key,
        "gemini_model": gemini_model if gemini_key else None,
        "copernicus_configured": copernicus_credentials,
        "database_configured": bool(database_url),
        "integrations": {
            "gemini": {
                "status": "configured" if gemini_key else "missing",
                "model": gemini_model if gemini_key else None,
                "verified": False
            },
            "copernicus": {
                "status": "configured" if copernicus_credentials else "missing",
                "verified": False
            },
            "database": {
                "status": "configured" if database_url else "missing",
                "kind": "postgresql" if database_url.lower().startswith(("postgres://", "postgresql://")) else ("other" if database_url else None),
                "persistent_storage_verified": False
            }
        },
        "note": "configured faqat sozlama mavjudligini bildiradi. Tashqi xizmatning ishlashi ushbu endpointda jonli tekshirilmaydi. Ma'lumotlar bazasi mavjudligi ham ma'lumotlar saqlanib qolishini kafolatlamaydi."
    }

@app.post("/health/verify")
async def verify_integrations(request: Request):
    """Rate-limited on-demand upstream checks. Secrets are never returned or logged."""
    now_ts = time.monotonic()
    address = client_ip(request)
    previous = _verify_attempts.get(address)
    # monotonic() counts from boot, so on a fresh machine it can be < 30 s:
    # only compare when this client really made an earlier attempt.
    if previous is not None and now_ts - previous < VERIFY_COOLDOWN_SECONDS:
        raise HTTPException(429, "Integratsiyalarni qayta tekshirishdan oldin biroz kuting.")
    _verify_attempts[address] = now_ts
    if len(_verify_attempts) > 2048:
        cutoff = now_ts - VERIFY_COOLDOWN_SECONDS * 4
        for address, timestamp in list(_verify_attempts.items()):
            if timestamp < cutoff:
                _verify_attempts.pop(address, None)
    results = {
        "gemini": {"status": "missing", "verified": False},
        "copernicus": {"status": "missing", "verified": False},
        "database": {
            "status": "configured" if os.environ.get("DATABASE_URL", "").strip() else "missing",
            "verified": False
        }
    }

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if gemini_key:
        prompt = "Reply with OK."
        system_text = "You are a short integration health check. Reply only OK."
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0)) as client:
                response = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={"x-goog-api-key": gemini_key, "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "system_instruction": system_text,
                        "input": prompt,
                        "generation_config": {"max_output_tokens": 8, "temperature": 0}
                    },
                )
            verified = False
            if response.status_code == 200:
                try:
                    verified = any(
                        step.get("type") == "model_output"
                        and any(part.get("text") for part in step.get("content", []) if isinstance(part, dict))
                        for step in response.json().get("steps", [])
                    )
                except (ValueError, AttributeError, TypeError):
                    verified = False

            method = "interactions"
            http_status = response.status_code
            if not verified and response.status_code not in (401, 403, 429):
                async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0)) as client:
                    fallback = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": gemini_key, "Content-Type": "application/json"},
                        json={
                            "systemInstruction": {"parts": [{"text": system_text}]},
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"maxOutputTokens": 8, "temperature": 0}
                        },
                    )
                http_status = fallback.status_code
                if fallback.status_code == 200:
                    try:
                        verified = any(
                            part.get("text")
                            for candidate in fallback.json().get("candidates", [])
                            for part in candidate.get("content", {}).get("parts", [])
                            if isinstance(part, dict)
                        )
                    except (ValueError, AttributeError, TypeError):
                        verified = False
                    method = "generateContent"

            results["gemini"] = {
                "status": "verified" if verified else "error",
                "verified": verified,
                "http_status": http_status,
                "method": method if verified else None
            }
        except httpx.TimeoutException:
            results["gemini"] = {"status": "timeout", "verified": False}
        except httpx.HTTPError:
            results["gemini"] = {"status": "unreachable", "verified": False}

    if sentinel.credentials_configured():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
                token_response = await client.post(
                    TOKEN_URL,
                    data={"grant_type": "client_credentials",
                          "client_id": os.environ.get("CDSE_CLIENT_ID", "").strip(),
                          "client_secret": os.environ.get("CDSE_CLIENT_SECRET", "").strip()},
                )
                if token_response.status_code != 200:
                    results["copernicus"] = {
                        "status": "auth_error",
                        "verified": False,
                        "http_status": token_response.status_code
                    }
                else:
                    access_token = token_response.json().get("access_token")
                    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                    start = now - dt.timedelta(days=30)
                    probe = {
                        "input": {
                            "bounds": {
                                "bbox": [69.1993, 41.2995, 69.2007, 41.3005],
                                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                            },
                            "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 40}}]
                        },
                        "aggregation": {
                            "timeRange": {"from": start.isoformat().replace("+00:00", "Z"),
                                          "to": now.isoformat().replace("+00:00", "Z")},
                            "aggregationInterval": {"of": "P30D"},
                            "evalscript": EVALSCRIPT,
                            "resx": 0.0001,
                            "resy": 0.0001
                        }
                    }
                    stats_response = await client.post(
                        STAT_URL,
                        json=probe,
                        headers={"Authorization": "Bearer " + access_token}
                    )
                    has_data = False
                    if stats_response.status_code == 200:
                        try:
                            has_data = bool(stats_response.json().get("data"))
                        except (ValueError, AttributeError):
                            has_data = False
                    results["copernicus"] = {
                        "status": "verified" if stats_response.status_code == 200 else "error",
                        "verified": stats_response.status_code == 200,
                        "http_status": stats_response.status_code,
                        "probe_has_data": has_data
                    }
        except httpx.TimeoutException:
            results["copernicus"] = {"status": "timeout", "verified": False}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            results["copernicus"] = {"status": "unreachable", "verified": False}

    return {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "integrations": results}


@app.post("/photo")
async def photo(payload: dict = Body(...)):
    encoded = str(payload.get("image", ""))
    if len(encoded) > 14_000_000:
        raise HTTPException(413, "Surat 10 MB dan kichik bo‘lishi kerak")
    try:
        raw = base64.b64decode(encoded.split(",")[-1], validate=True)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(400, "Surat oqilmadi yoki formati noto‘g‘ri")
    im.thumbnail((320, 320))
    pixels = list(im.getdata())
    neutral_bright = 0
    for red, green, blue in pixels:
        maximum, minimum = max(red, green, blue), min(red, green, blue)
        if maximum > 0 and minimum > 165 and (maximum - minimum) / maximum < 0.16:
            neutral_bright += 1
    percent = round(neutral_bright / max(len(pixels), 1) * 100, 1)
    return {
        "bright_neutral_pixel_percent": percent,
        "method": "RGB rang statistikasi",
        "note": "Bu faqat suratdagi och va neytral rangli piksellar ulushi. Tuz kristali yoki tuproq sho‘rlanishini aniqlamaydi; EC laboratoriya tahlili yoki kalibrlangan EC-metr talab qilinadi."
    }

async def _analyse(bounds: dict, cache_key: tuple, days: int, extra: dict):
    """Try the requested window, then wider ones (sparse/cloudy coverage). Report the real window."""
    windows = list(dict.fromkeys([days, max(days, 120), max(days, 180), 365]))
    for window_days in windows:
        key = cache_key + (window_days, dt.date.today().isoformat())
        cached = _cache.get(key)
        if cached is not None:
            return cached
        end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        start = end - dt.timedelta(days=window_days)
        body = sentinel.statistics_body(bounds, start, end, "P%dD" % window_days, max_cloud=80)
        data = await sentinel.query(body)
        parsed = sentinel.parse_interval(data[0]) if data else None
        if not parsed:
            continue
        result = {
            **extra, "period_days": window_days, **parsed,
            "source": "Copernicus Sentinel-2 L2A (Sentinel Hub Statistical API)",
            "note": "Sentinel-2 dan olingan SI, NDVI va NDMI ko‘rsatkichlari. Ular tuproq EC qiymati yoki sho‘rlanish darajasini bevosita o‘lchamaydi.",
        }
        _cache.set(key, result)
        return result
    raise HTTPException(404, "Tanlangan joyda 365 kun ichida ham yaroqli Sentinel-2 statistikasi topilmadi. Maydon koordinatasini tekshiring yoki boshqa nuqtani tanlang.")


@app.get("/salinity")
async def salinity(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), days: int = Query(60, ge=1, le=365)):
    return await _analyse(sentinel.point_bounds(lat, lon), ("pt", round(lat, 4), round(lon, 4)), days,
                          {"lat": lat, "lon": lon, "mode": "point"})


@app.post("/analyze")
async def analyze_area(payload: dict = Body(...)):
    """Field-level analysis. Body: {"polygon": [[lat, lon], ...]} or {"lat":..,"lon":..}, optional "days"."""
    try:
        days = int(payload.get("days", 60))
    except (TypeError, ValueError):
        raise HTTPException(422, "days son bo‘lishi kerak")
    if not 1 <= days <= 365:
        raise HTTPException(422, "days 1 dan 365 gacha bo‘lishi kerak")
    polygon = payload.get("polygon")
    if polygon:
        bounds = sentinel.polygon_bounds(polygon)
        ring = bounds["geometry"]["coordinates"][0]
        key = ("poly",) + tuple((round(p[1], 5), round(p[0], 5)) for p in ring)
        lat = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
        lon = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
        return await _analyse(bounds, key, days, {"lat": round(lat, 6), "lon": round(lon, 6), "mode": "polygon"})
    try:
        lat, lon = float(payload["lat"]), float(payload["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "lat/lon yoki polygon kerak")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(422, "Koordinata noto‘g‘ri")
    return await _analyse(sentinel.point_bounds(lat, lon), ("pt", round(lat, 4), round(lon, 4)), days,
                          {"lat": lat, "lon": lon, "mode": "point"})

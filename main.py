import os, time, datetime as dt, math
import base64, io
import httpx

from history import router as history_router
from chat import router as chat_router

from PIL import Image
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

CID = os.environ.get("CDSE_CLIENT_ID", "")
SEC = os.environ.get("CDSE_CLIENT_SECRET", "")
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAT_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

app = FastAPI(title="Shor Radar API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(history_router)
app.include_router(chat_router)
_tok = {"v": None, "exp": 0}
_cache = {}
_verify_attempts = {}


def _stat_number(value):
    """Normalize Sentinel Hub statistics, which may arrive as strings."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
VERIFY_COOLDOWN_SECONDS = 30

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B04", "B08", "SCL", "dataMask"] }],
    output: [
      { id: "si", bands: 1, sampleType: "FLOAT32" },
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(s) {
  var valid = s.dataMask === 1 && [3, 8, 9, 10, 11].indexOf(s.SCL) < 0;
  var si = Math.sqrt(Math.max(0, s.B02 * s.B04));
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  return { si: [si], ndvi: [ndvi], dataMask: [valid ? 1 : 0] };
}"""

async def token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    if not CID or not SEC:
        raise HTTPException(500, "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET sozlanmagan")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                          "client_id": CID, "client_secret": SEC})
    if r.status_code != 200:
        raise HTTPException(502, "Token olinmadi: " + r.text[:200])
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + j.get("expires_in", 300))
    return _tok["v"]


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
    copernicus_credentials = bool(CID.strip() and SEC.strip())
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
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    previous = _verify_attempts.get(client_ip, 0.0)
    if now_ts - previous < VERIFY_COOLDOWN_SECONDS:
        raise HTTPException(429, "Integratsiyalarni qayta tekshirishdan oldin biroz kuting.")
    _verify_attempts[client_ip] = now_ts
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

    if CID.strip() and SEC.strip():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
                token_response = await client.post(
                    TOKEN_URL,
                    data={"grant_type": "client_credentials", "client_id": CID, "client_secret": SEC},
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

@app.get("/salinity")
async def salinity(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), days: int = Query(60, ge=1, le=365)):
    key = (round(lat, 4), round(lon, 4), days, dt.date.today().isoformat())
    if key in _cache:
        return _cache[key]
    end = dt.datetime.utcnow().replace(microsecond=0)
    start = end - dt.timedelta(days=days)
    dx, dy = 0.0007, 0.0005
    body = {
      "input": {"bounds": {"bbox": [lon-dx, lat-dy, lon+dx, lat+dy],
                           "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 40}}]},
      "aggregation": {"timeRange": {"from": start.isoformat()+"Z", "to": end.isoformat()+"Z"},
                      "aggregationInterval": {"of": "P"+str(days)+"D"},
                      "evalscript": EVALSCRIPT, "resx": 0.0001, "resy": 0.0001}}
    t = await token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(STAT_URL, json=body, headers={"Authorization": "Bearer "+t})
    if r.status_code != 200:
        raise HTTPException(502, "Sentinel Hub xatosi: " + r.text[:300])
    try:
        outputs = r.json()["data"][0]["outputs"]
        si = _stat_number(outputs["si"]["bands"]["B0"]["stats"].get("mean"))
        ndvi = _stat_number(outputs["ndvi"]["bands"]["B0"]["stats"].get("mean"))
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(404, "Bu nuqta uchun bulutsiz Sentinel-2 tasviri yoki statistikasi topilmadi")
    if si is None or ndvi is None:
        raise HTTPException(404, "Sentinel-2 statistikasi to‘liq emas. Boshqa sana yoki nuqtani sinab ko‘ring.")
    res = {"lat": lat, "lon": lon, "period_days": days, "si": round(si, 3),
           "ndvi": round(ndvi, 3),
           "note": "Sentinel-2 dan olingan SI va NDVI ko‘rsatkichlari. Ular tuproq EC qiymati yoki sho‘rlanish darajasini bevosita o‘lchamaydi."}
    _cache[key] = res
    return res

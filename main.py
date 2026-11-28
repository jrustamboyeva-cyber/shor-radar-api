import os, time, datetime as dt
import base64, io
import httpx

from history import router as history_router
from chat import router as chat_router

from PIL import Image
from fastapi import Body, FastAPI, HTTPException, Query
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

EVALSCRIPT = """//VERSION=3
function setup(){return{input:[{bands:["B03","B04","B08","SCL","dataMask"]}],
output:[{id:"si",bands:1},{id:"ndvi",bands:1},{id:"dataMask",bands:1}]}}
function evaluatePixel(s){
 var ok=s.dataMask==1&&[3,8,9,10,11].indexOf(s.SCL)<0?1:0;
 return{si:[Math.sqrt(s.B03*s.B04)],ndvi:[(s.B08-s.B04)/(s.B08+s.B04+1e-6)],dataMask:[ok]}}"""

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


@app.get("/")
def root():
    return {"ok": True, "service": "Shor Radar API"}

@app.get("/health")
def health():
    return {"ok": True, "service": "Shor Radar API"}

@app.get("/health/integrations")
def integration_health():
    return {
        "api": "ready",
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "copernicus_configured": bool(CID and SEC),
        "database_configured": bool(os.environ.get("DATABASE_URL", "").strip()),
        "note": "Configured means credentials exist; it does not guarantee upstream availability."
    }

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
async def salinity(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), days: int = 60):
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
        o = r.json()["data"][0]["outputs"]
        si = o["si"]["bands"]["B0"]["stats"]["mean"]
        ndvi = o["ndvi"]["bands"]["B0"]["stats"]["mean"]
    except Exception:
        raise HTTPException(404, "Bu nuqta uchun bulutsiz suratlar topilmadi")
    res = {"lat": lat, "lon": lon, "period_days": days, "si": round(si, 3),
           "ndvi": round(ndvi, 3),
           "note": "Sentinel-2 dan olingan SI va NDVI ko‘rsatkichlari. Ular tuproq EC qiymati yoki sho‘rlanish darajasini bevosita o‘lchamaydi."}
    _cache[key] = res
    return res

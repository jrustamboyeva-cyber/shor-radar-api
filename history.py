import os, time, datetime as dt
import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
CID = os.environ.get("CDSE_CLIENT_ID", "")
SEC = os.environ.get("CDSE_CLIENT_SECRET", "")
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAT_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
_tok = {"v": None, "exp": 0}
_cache = {}

EVAL = """//VERSION=3
function setup(){return{input:[{bands:["B03","B04","B08","SCL","dataMask"]}],
output:[{id:"si",bands:1},{id:"ndvi",bands:1},{id:"dataMask",bands:1}]}}
function evaluatePixel(s){
 var ok=s.dataMask==1&&[3,8,9,10,11].indexOf(s.SCL)<0?1:0;
 return{si:[Math.sqrt(s.B03*s.B04)],ndvi:[(s.B08-s.B04)/(s.B08+s.B04+1e-6)],dataMask:[ok]}}"""

async def token():
    if not CID or not SEC:
        raise HTTPException(503, "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET sozlanmagan")
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": CID, "client_secret": SEC})
    if r.status_code != 200:
        raise HTTPException(502, "Token olinmadi")
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + j.get("expires_in", 300))
    return _tok["v"]

def num(x):
    return x if isinstance(x, (int, float)) and x == x else None


@router.get("/history")
async def history(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), months: int = Query(12, ge=3, le=24)):
    key = (round(lat, 4), round(lon, 4), months, dt.date.today().isoformat())
    if key in _cache:
        return _cache[key]
    end = dt.datetime.utcnow().replace(microsecond=0)
    start = (end.replace(day=1) - dt.timedelta(days=30 * (months - 1))).replace(day=1, hour=0, minute=0, second=0)
    dx, dy = 0.0007, 0.0005
    body = {
        "input": {"bounds": {"bbox": [lon - dx, lat - dy, lon + dx, lat + dy],
                             "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                  "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 40}}]},
        "aggregation": {"timeRange": {"from": start.isoformat() + "Z", "to": end.isoformat() + "Z"},
                        "aggregationInterval": {"of": "P1M", "lastIntervalBehavior": "SHORTEN"},
                        "evalscript": EVAL, "resx": 0.0001, "resy": 0.0001}}
    t = await token()
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(STAT_URL, json=body, headers={"Authorization": "Bearer " + t})
    if r.status_code != 200:
        raise HTTPException(502, "Sentinel Hub xatosi: " + r.text[:200])
    out = []
    for d in r.json().get("data", []):
        try:
            o = d["outputs"]
            si = num(o["si"]["bands"]["B0"]["stats"].get("mean"))
            nd = num(o["ndvi"]["bands"]["B0"]["stats"].get("mean"))
        except Exception:
            si = nd = None
        m = d["interval"]["from"][:7]
        if si is None or nd is None:
            out.append({"month": m, "si": None, "ndvi": None})
        else:
            out.append({"month": m, "si": round(si, 3), "ndvi": round(nd, 3)})
    res = {"lat": lat, "lon": lon, "points": out, "note": "Taxminiy baho; bulutli oylarda ma'lumot bo'lmasligi mumkin."}
    _cache[key] = res
    return res


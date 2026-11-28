"""Copernicus Data Space (Sentinel Hub) access shared by /salinity, /analyze and /history.

Returned indices are remote-sensing indicators (SI, NDVI, NDMI). They are NOT soil
EC measurements; callers must present them as indicators only.
"""
import datetime as dt
import math
import os
import time

import httpx
from fastapi import HTTPException

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAT_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
WGS84 = "http://www.opengis.net/def/crs/EPSG/0/4326"

# Point mode samples a ~120 x 110 m box around the coordinate.
POINT_DX, POINT_DY = 0.0007, 0.0005
MAX_POLYGON_VERTICES = 200
MAX_POLYGON_SPAN_DEG = 0.05  # ~5 km; larger areas belong to a district-level job, not one request

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B04", "B08", "B11", "SCL", "dataMask"] }],
    output: [
      { id: "si", bands: 1, sampleType: "FLOAT32" },
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndmi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(s) {
  // Exclude no-data, cloud shadow (3), clouds (8, 9), cirrus (10) and snow (11).
  var valid = s.dataMask === 1 && [3, 8, 9, 10, 11].indexOf(s.SCL) < 0;
  var si = Math.sqrt(Math.max(0, s.B02 * s.B04));
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  var ndmi = (s.B08 - s.B11) / (s.B08 + s.B11 + 1e-6);
  return { si: [si], ndvi: [ndvi], ndmi: [ndmi], dataMask: [valid ? 1 : 0] };
}"""

_token = {"value": None, "expires": 0.0}


def credentials_configured() -> bool:
    return bool(os.environ.get("CDSE_CLIENT_ID", "").strip() and os.environ.get("CDSE_CLIENT_SECRET", "").strip())


async def token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    client_id = os.environ.get("CDSE_CLIENT_ID", "").strip()
    secret = os.environ.get("CDSE_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise HTTPException(503, "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET sozlanmagan")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN_URL, data={
            "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
    if response.status_code != 200:
        # Never echo the upstream body: it can contain request details.
        raise HTTPException(502, f"Copernicus token olinmadi (HTTP {response.status_code})")
    payload = response.json()
    _token.update(value=payload["access_token"], expires=time.time() + payload.get("expires_in", 300))
    return _token["value"]


def number(value):
    """Normalize Sentinel Hub statistics, which may arrive as strings or NaN."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def point_bounds(lat: float, lon: float) -> dict:
    return {"bbox": [lon - POINT_DX, lat - POINT_DY, lon + POINT_DX, lat + POINT_DY],
            "properties": {"crs": WGS84}}


def polygon_bounds(polygon) -> dict:
    """polygon: [[lat, lon], ...] as stored by the apps. Validated and closed here."""
    if not isinstance(polygon, list) or not 3 <= len(polygon) <= MAX_POLYGON_VERTICES:
        raise HTTPException(422, "Chegara 3 tadan 200 tagacha nuqtadan iborat bo‘lishi kerak")
    ring = []
    for point in polygon:
        try:
            lat, lon = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(422, "Chegara nuqtasi noto‘g‘ri")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(422, "Chegara nuqtasi koordinata oralig‘idan tashqarida")
        ring.append([lon, lat])
    lats = [p[1] for p in ring]
    lons = [p[0] for p in ring]
    if max(lats) - min(lats) > MAX_POLYGON_SPAN_DEG or max(lons) - min(lons) > MAX_POLYGON_SPAN_DEG:
        raise HTTPException(422, "Maydon juda katta: bitta dala chegarasini chizing (taxminan 5 km gacha)")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"crs": WGS84}}


def _iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def statistics_body(bounds: dict, start: dt.datetime, end: dt.datetime, interval: str, max_cloud: int) -> dict:
    return {
        "input": {"bounds": bounds,
                  "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": max_cloud}}]},
        "aggregation": {
            "timeRange": {"from": _iso(start), "to": _iso(end)},
            "aggregationInterval": {"of": interval, "lastIntervalBehavior": "SHORTEN"},
            "evalscript": EVALSCRIPT, "resx": 0.0001, "resy": 0.0001,
        },
    }


def parse_interval(entry: dict):
    """Return dict with si/ndvi/ndmi means and valid pixel count, or None if unusable."""
    try:
        outputs = entry["outputs"]
        def stat(name):
            return outputs[name]["bands"]["B0"]["stats"]
        si_stats = stat("si")
        si, ndvi = number(si_stats.get("mean")), number(stat("ndvi").get("mean"))
        ndmi = number(stat("ndmi").get("mean")) if "ndmi" in outputs else None
        samples = number(si_stats.get("sampleCount")) or 0
        no_data = number(si_stats.get("noDataCount")) or 0
    except (KeyError, TypeError, AttributeError):
        return None
    if si is None or ndvi is None:
        return None
    return {
        "si": round(si, 3), "ndvi": round(ndvi, 3),
        "ndmi": round(ndmi, 3) if ndmi is not None else None,
        "valid_pixels": int(max(0, samples - no_data)),
        "interval_from": entry.get("interval", {}).get("from"),
        "interval_to": entry.get("interval", {}).get("to"),
    }


async def query(body: dict, timeout: float = 60) -> list:
    access = await token()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(STAT_URL, json=body, headers={"Authorization": "Bearer " + access})
    if response.status_code == 401:
        _token.update(value=None, expires=0)
    if response.status_code != 200:
        print("Sentinel Hub error:", response.status_code, response.text[:300], flush=True)
        raise HTTPException(502, f"Sentinel Hub xatosi (HTTP {response.status_code})")
    return response.json().get("data", [])

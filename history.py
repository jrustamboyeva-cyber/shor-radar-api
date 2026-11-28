"""Monthly Sentinel-2 indicator history for a field (point or polygon)."""
import datetime as dt

from fastapi import APIRouter, Body, HTTPException, Query

import sentinel
from security import BoundedCache

router = APIRouter()
_cache = BoundedCache(max_items=512, ttl_seconds=12 * 3600)
NOTE = "Taxminiy ko‘rsatkich; bulutli oylarda ma’lumot bo‘lmasligi mumkin. SI/NDVI/NDMI tuproq EC qiymatini bevosita o‘lchamaydi."


def _month_start(months: int) -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    year, month = end.year, end.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    return dt.datetime(year, month, 1, tzinfo=dt.timezone.utc), end


async def _history(bounds: dict, key: tuple, months: int, extra: dict):
    cache_key = key + (months, dt.date.today().isoformat())
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    start, end = _month_start(months)
    data = await sentinel.query(sentinel.statistics_body(bounds, start, end, "P1M", max_cloud=40), timeout=90)
    points = []
    for entry in data:
        month = str(entry.get("interval", {}).get("from", ""))[:7]
        parsed = sentinel.parse_interval(entry)
        if parsed:
            points.append({"month": month, "si": parsed["si"], "ndvi": parsed["ndvi"], "ndmi": parsed["ndmi"],
                           "valid_pixels": parsed["valid_pixels"]})
        else:
            points.append({"month": month, "si": None, "ndvi": None, "ndmi": None, "valid_pixels": 0})
    result = {**extra, "points": points, "note": NOTE}
    _cache.set(cache_key, result)
    return result


@router.get("/history")
async def history(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                  months: int = Query(12, ge=3, le=24)):
    return await _history(sentinel.point_bounds(lat, lon), ("pt", round(lat, 4), round(lon, 4)), months,
                          {"lat": lat, "lon": lon})


@router.post("/history")
async def history_area(payload: dict = Body(...)):
    try:
        months = int(payload.get("months", 12))
    except (TypeError, ValueError):
        raise HTTPException(422, "months son bo‘lishi kerak")
    if not 3 <= months <= 24:
        raise HTTPException(422, "months 3 dan 24 gacha bo‘lishi kerak")
    bounds = sentinel.polygon_bounds(payload.get("polygon"))
    ring = bounds["geometry"]["coordinates"][0]
    key = ("poly",) + tuple((round(p[1], 5), round(p[0], 5)) for p in ring)
    return await _history(bounds, key, months, {"mode": "polygon"})

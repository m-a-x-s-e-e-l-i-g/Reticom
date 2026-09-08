"""Bounded nearest-landmark lookup using public OpenStreetMap data."""
import json
import math
import re
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query

from .intel_sources import OVERPASS_URL, IntelSourceError, fetch_bytes


FILTERS = {
    "church": ['["building"="church"]', '["amenity"="place_of_worship"]["religion"="christian"]'],
    "school": ['["amenity"="school"]'],
    "hospital": ['["amenity"="hospital"]'],
    "bridge": ['["bridge"="yes"]'],
    "station": ['["railway"="station"]'],
    "police station": ['["amenity"="police"]'],
    "fire station": ['["amenity"="fire_station"]'],
}


def nearest_landmark(query: str, lat: float, lon: float, *, fetch=fetch_bytes):
    query = query.strip().lower()
    if not query or len(query) > 64 or not all(c.isalnum() or c in " -'" for c in query):
        raise ValueError("Invalid landmark name")
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid reference position")
    filters = FILTERS.get(query, [f'["name"~{json.dumps("^" + re.escape(query) + "$")},i]'])
    statements = "".join(f"nwr{selector}(around:5000,{lat},{lon});" for selector in filters)
    payload = json.loads(fetch(OVERPASS_URL,
        data=urlencode({"data": f"[out:json][timeout:15];({statements});out center tags;"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, max_bytes=2_000_000))
    if payload.get("remark"):
        raise IntelSourceError("Landmark lookup was incomplete")
    candidates = []
    for item in payload.get("elements", []):
        center = item.get("center", item)
        try:
            y, x = float(center["lat"]), float(center["lon"])
            if not math.isfinite(y) or not math.isfinite(x) or not -90 <= y <= 90 or not -180 <= x <= 180:
                continue
            a = math.sin(math.radians(y-lat)/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(y))*math.sin(math.radians(x-lon)/2)**2
            distance = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
        except (KeyError, TypeError, ValueError):
            continue
        if distance <= 5000:
            candidates.append({"coordinates": [x, y], "name": item.get("tags", {}).get("name", query.title()),
                "distance_m": round(distance), "source": "OpenStreetMap", "osm_id": f'{item.get("type")}/{item.get("id")}', "_distance": distance})
    if not candidates:
        return None
    result = min(candidates, key=lambda item: (item["_distance"], item["osm_id"]))
    result.pop("_distance")
    return result


def landmark_router():
    router = APIRouter()

    @router.get("/api/map/landmark")
    def lookup(q: str = Query(min_length=1, max_length=64), lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
        try:
            return {"landmark": nearest_landmark(q, lat, lon)}
        except IntelSourceError as exc:
            raise HTTPException(503, "Landmark lookup unavailable") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router

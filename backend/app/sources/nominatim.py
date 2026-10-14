"""State / province boundaries from OpenStreetMap's Nominatim.

Nominatim's usage policy: at most 1 request/second, an identifying
User-Agent, cache results, and no search-as-you-type. The frontend only
searches on submit and everything we get back is cached in Mongo.
"""

import asyncio
import math
import time

import httpx

from .http import get_json

BASE_URL = "https://nominatim.openstreetmap.org"
MAX_VERTICES = 20_000
OSM_TYPES = {"relation": "R", "way": "W", "node": "N"}


class Nominatim:
    def __init__(self, http: httpx.AsyncClient, base_url: str = BASE_URL):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self._lock = asyncio.Lock()
        self._next_ok = 0.0

    async def _get(self, path: str, params: dict):
        async with self._lock:
            wait = self._next_ok - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await get_json(
                    self.http, f"{self.base_url}/{path}", params,
                    name="OpenStreetMap Nominatim", retries=1, timeout=15.0,
                )
            finally:
                self._next_ok = time.monotonic() + 1.1

    async def search_states(self, query: str, limit: int = 6) -> list[dict]:
        data = await self._get("search", {
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "featureType": "state",
            "polygon_geojson": 1,
            "polygon_threshold": 0.02,  # ~2 km simplification, plenty for sampling
            "accept-language": "en",
        })
        return [a for a in map(parse_area, data if isinstance(data, list) else []) if a]

    async def lookup(self, osm_type: str, osm_id: int) -> dict | None:
        data = await self._get("lookup", {
            "osm_ids": f"{osm_type}{osm_id}",
            "format": "jsonv2",
            "polygon_geojson": 1,
            "polygon_threshold": 0.02,
            "accept-language": "en",
        })
        areas = [a for a in map(parse_area, data if isinstance(data, list) else []) if a]
        return areas[0] if areas else None


def parse_area(r: dict) -> dict | None:
    """Validate one Nominatim result; anything odd is dropped rather than trusted."""
    try:
        osm_type = OSM_TYPES[r["osm_type"]]
        osm_id = int(r["osm_id"])
        south, north, west, east = (float(x) for x in r["boundingbox"])
        geometry = r["geojson"]
        name = str(r.get("name") or r["display_name"])[:120]
        label = str(r.get("display_name") or name)[:200]
    except (KeyError, TypeError, ValueError):
        return None
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    if not _valid_coords(geometry):
        return None
    return {
        "osm_type": osm_type,
        "osm_id": osm_id,
        "name": name,
        "label": label,
        "bbox": [south, west, north, east],
        "geometry": {"type": geometry["type"], "coordinates": geometry["coordinates"]},
    }


def _valid_coords(geometry: dict) -> bool:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    count = 0
    try:
        for rings in polys:
            for ring in rings:
                for lon, lat in ring:
                    count += 1
                    if count > MAX_VERTICES or not (math.isfinite(lon) and math.isfinite(lat)):
                        return False
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        return False
    except (TypeError, ValueError):
        return False
    return count >= 4

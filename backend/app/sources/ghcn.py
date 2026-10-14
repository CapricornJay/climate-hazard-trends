"""NOAA NCEI GHCN-Daily station observations.

This is the "real world" source: stations move, instruments change, days go
missing and NOAA's own QC flags bad values. ERA5 has none of those problems
but smooths everything over a ~25 km cell, so having both is useful.

Units are a trap here: the access service returns tenths of a unit when no
`units` parameter is given, and °F / inches with units=standard. We always
ask for units=metric and still run the result through normalize_units.
"""

import asyncio
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import httpx
import numpy as np

from ..cleaning import align_to_calendar, normalize_units
from .http import UpstreamError, get_json

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.ncei.noaa.gov/access/services/search/v1/data"
DATA_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
ELEMENTS = {"tmax": "TMAX", "tmin": "TMIN", "precip": "PRCP"}
STATION_ID = re.compile(r"^[A-Z0-9]{11}$")

# GHCN-Daily quality flags (readme.txt, section III)
QFLAGS = {
    "D": "duplicate check",
    "G": "gap check",
    "I": "internal consistency check",
    "K": "streak/frequent-value check",
    "L": "multiday period length check",
    "M": "megaconsistency check",
    "N": "naught check",
    "O": "climatological outlier check",
    "R": "lookup check",
    "S": "spatial consistency check",
    "T": "temporal consistency check",
    "W": "temperature too warm for snow",
    "X": "bounds check",
    "Z": "Datzilla investigation",
}


@dataclass
class StationSeries:
    station: dict
    start: date
    values: np.ndarray
    duplicates: int = 0
    flagged: int = 0
    flags: dict[str, int] = field(default_factory=dict)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


class Ghcn:
    def __init__(self, http: httpx.AsyncClient, search_url: str = SEARCH_URL, data_url: str = DATA_URL):
        self.http = http
        self.search_url = search_url
        self.data_url = data_url
        self._sem = asyncio.Semaphore(2)  # be polite, NCEI is a shared public service

    async def stations_near(self, lat: float, lon: float, variable: str, start: date, end: date, radius_deg: float = 0.75) -> list[dict]:
        element = ELEMENTS[variable]
        params = {
            "dataset": "daily-summaries",
            # north,west,south,east
            "bbox": f"{lat + radius_deg:.3f},{lon - radius_deg:.3f},{lat - radius_deg:.3f},{lon + radius_deg:.3f}",
            "dataTypes": element,
            "startDate": f"{start.isoformat()}T00:00:00",
            "endDate": f"{end.isoformat()}T23:59:59",
            "limit": 100,
        }
        data = await get_json(self.http, self.search_url, params, name="NOAA NCEI search", retries=2, timeout=30.0)
        span = (end - start).days + 1
        out = []
        for r in (data or {}).get("results") or []:
            try:
                st = r["stations"][0]
                s_lon, s_lat = (float(x) for x in r["boundingPoints"][0]["coordinates"])
                dt = next(d for d in st["dataTypes"] if d["id"] == element)
                first = date.fromisoformat(dt["dateRange"]["start"][:10])
                last = date.fromisoformat(dt["dateRange"]["end"][:10])
                coverage = float(dt.get("coverage") or 0) / 100
                sid = str(st["id"])
            except (KeyError, IndexError, StopIteration, TypeError, ValueError):
                continue
            if not STATION_ID.match(sid):
                continue
            overlap = max(0, (min(last, end) - max(first, start)).days + 1) / span
            dist = haversine_km(lat, lon, s_lat, s_lon)
            out.append({
                "id": sid,
                "name": str(st.get("name", sid))[:120],
                "lat": s_lat,
                "lon": s_lon,
                "distance_km": round(dist, 1),
                "first": first.isoformat(),
                "last": last.isoformat(),
                "coverage": round(coverage, 3),
                "overlap": round(overlap, 3),
                # Prefer long, complete records; distance matters but less.
                "score": round(overlap * coverage - dist / 250, 4),
            })
        out.sort(key=lambda s: s["score"], reverse=True)
        return out

    async def fetch(self, station: dict, variable: str, start: date, end: date) -> StationSeries:
        sid = station["id"]
        if not STATION_ID.match(sid):
            raise ValueError(f"bad station id {sid!r}")
        element = ELEMENTS[variable]
        params = {
            "dataset": "daily-summaries",
            "stations": sid,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dataTypes": element,
            "includeAttributes": "true",
            "units": "metric",
            "format": "json",
        }
        async with self._sem:
            rows = await get_json(self.http, self.data_url, params, name="NOAA NCEI", retries=2,
                                  timeout=httpx.Timeout(120.0, connect=10.0))
        if not isinstance(rows, list):
            raise UpstreamError("NOAA returned an unexpected response")

        dates, vals, flags = [], [], Counter()
        for row in rows:
            raw = row.get(element)
            if raw is None or not str(raw).strip():
                continue
            attrs = str(row.get(f"{element}_ATTRIBUTES") or "").split(",")
            qflag = attrs[1].strip() if len(attrs) > 1 else ""
            if qflag:
                flags[qflag] += 1
                continue
            try:
                vals.append(float(raw))
                dates.append(row["DATE"])
            except (KeyError, ValueError):
                flags["unparseable"] += 1

        unit = "mm" if variable == "precip" else "°C"  # what units=metric gives back
        values = normalize_units(vals, unit, variable)
        try:
            aligned, dupes = align_to_calendar(np.array(dates, dtype="datetime64[D]"), values, start, end)
        except ValueError as exc:
            raise UpstreamError("NOAA returned dates we couldn't parse") from exc
        log.info("ghcn %s %s: %d values, %d flagged", sid, element, len(vals), sum(flags.values()))
        return StationSeries(
            station=station,
            start=start,
            values=aligned,
            duplicates=dupes,
            flagged=sum(flags.values()),
            flags=dict(flags),
        )

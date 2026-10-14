"""Daily series with a read-through cache.

Each cached series is one contiguous run of days. When a request needs
years we don't have, only the missing prefix and/or suffix is fetched and
stitched on, so widening the time range later costs a few years of quota,
not the whole record again.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np

from .hazards import InsufficientData
from .regions import SamplePoint
from .sources.ghcn import ELEMENTS, Ghcn
from .sources.openmeteo import OpenMeteo, call_weight
from .store import pack, unpack

log = logging.getLogger(__name__)

STATION_MAX_KM = 75
STATION_MIN_OVERLAP = 0.75
STATION_TTL = timedelta(days=180)  # GHCN gets re-QC'd, so don't keep it forever
UNITS = {"tmax": "°C", "tmin": "°C", "precip": "mm"}


@dataclass
class Loaded:
    key: str
    values: np.ndarray  # exactly [start, end]
    meta: dict = field(default_factory=dict)
    duplicates: int = 0
    flagged: int = 0
    flags: dict = field(default_factory=dict)


def missing_spans(doc: dict | None, start: date, end: date) -> list[tuple[date, date]]:
    if not doc:
        return [(start, end)]
    have_start, have_end = date.fromisoformat(doc["start"]), date.fromisoformat(doc["end"])
    spans = []
    if start < have_start:
        spans.append((start, have_start - timedelta(days=1)))
    if end > have_end:
        spans.append((have_end + timedelta(days=1), end))
    return spans


def merge(doc: dict | None, key: str, start: date, values: np.ndarray, meta: dict, extra: dict | None = None) -> dict:
    """Stitch freshly fetched values onto a cached doc. Spans come from
    missing_spans(), so they always touch the cached range."""
    extra = extra or {}
    end = start + timedelta(days=values.size - 1)
    if doc:
        old = unpack(doc["data"])
        old_start = date.fromisoformat(doc["start"])
        if start < old_start:
            values, new_start = np.concatenate([values, old]), start
        else:
            values, new_start = np.concatenate([old, values]), old_start
        end = new_start + timedelta(days=values.size - 1)
        start = new_start
        for k in ("duplicates", "flagged"):
            extra[k] = extra.get(k, 0) + doc.get(k, 0)
        flags = dict(doc.get("flags") or {})
        for k, v in (extra.get("flags") or {}).items():
            flags[k] = flags.get(k, 0) + v
        extra["flags"] = flags
    return {
        "_id": key,
        **meta,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n": int(values.size),
        "data": pack(values),
        "fetched_at": datetime.now(timezone.utc),
        "duplicates": extra.get("duplicates", 0),
        "flagged": extra.get("flagged", 0),
        "flags": extra.get("flags", {}),
    }


def _slice(doc: dict, start: date, end: date) -> Loaded:
    values = unpack(doc["data"])
    i0 = (start - date.fromisoformat(doc["start"])).days
    n = (end - start).days + 1
    meta = {k: doc.get(k) for k in ("source", "variable", "lat", "lon", "cell_lat", "cell_lon", "elevation", "station")}
    return Loaded(doc["_id"], values[i0:i0 + n].copy(), meta, doc.get("duplicates", 0), doc.get("flagged", 0), doc.get("flags") or {})


class SeriesRepo:
    def __init__(self, store, openmeteo: OpenMeteo, ghcn: Ghcn, max_request_weight: float):
        self.store = store
        self.om = openmeteo
        self.ghcn = ghcn
        self.max_request_weight = max_request_weight

    async def grid(self, variable: str, points: list[SamplePoint], start: date, end: date, before_fetch) -> list[Loaded]:
        keys = [f"era5|{variable}|{p.lat:.2f}|{p.lon:.2f}" for p in points]
        docs = await self.store.get_series_many(keys)

        plan: dict[tuple[date, date], list[int]] = {}
        for i, key in enumerate(keys):
            for span in missing_spans(docs.get(key), start, end):
                plan.setdefault(span, []).append(i)

        if plan:
            await before_fetch()
        for (s, e), idxs in plan.items():
            per_point = call_weight(1, 1, (e - s).days + 1)
            batch_size = max(1, int(self.max_request_weight // per_point))
            for b in range(0, len(idxs), batch_size):
                batch = idxs[b:b + batch_size]
                fetched = await self.om.fetch_daily([(points[i].lat, points[i].lon) for i in batch], variable, s, e)
                for i, gs in zip(batch, fetched):
                    meta = {
                        "source": "era5", "variable": variable, "unit": UNITS[variable],
                        "lat": points[i].lat, "lon": points[i].lon,
                        "cell_lat": gs.cell_lat, "cell_lon": gs.cell_lon, "elevation": gs.elevation,
                    }
                    docs[keys[i]] = merge(docs.get(keys[i]), keys[i], gs.start, gs.values, meta, {"duplicates": gs.duplicates})
                    await self.store.put_series(docs[keys[i]])

        return [_slice(docs[k], start, end) for k in keys]

    async def station(self, variable: str, lat: float, lon: float, start: date, end: date, before_fetch) -> Loaded:
        lookup_key = f"ghcn-near|{variable}|{lat:.2f}|{lon:.2f}|{start.year}|{end.year}"
        stations = await self.store.get_lookup(lookup_key)
        if stations is None:
            await before_fetch()
            stations = await self.ghcn.stations_near(lat, lon, variable, start, end)
            await self.store.put_lookup(lookup_key, stations, 30 * 86400)

        eligible = [s for s in stations if s["overlap"] >= STATION_MIN_OVERLAP and s["distance_km"] <= STATION_MAX_KM]
        if not eligible:
            raise InsufficientData(
                f"No GHCN-Daily station within {STATION_MAX_KM} km has a {ELEMENTS[variable]} record covering "
                f"most of {start.year}-{end.year}. Station coverage is patchy outside North America, Europe "
                f"and Australia; the ERA5 source works everywhere."
            )
        st = eligible[0]
        key = f"ghcn|{variable}|{st['id']}"
        doc = (await self.store.get_series_many([key])).get(key)
        if doc and datetime.now(timezone.utc) - _aware(doc["fetched_at"]) > STATION_TTL:
            doc = None

        spans = missing_spans(doc, start, end)
        if spans:
            await before_fetch()
        for s, e in spans:
            ss = await self.ghcn.fetch(st, variable, s, e)
            meta = {"source": "ghcn", "variable": variable, "unit": UNITS[variable], "lat": st["lat"], "lon": st["lon"],
                    "station": {k: st[k] for k in ("id", "name", "lat", "lon", "distance_km", "first", "last")}}
            doc = merge(doc, key, ss.start, ss.values, meta,
                        {"duplicates": ss.duplicates, "flagged": ss.flagged, "flags": ss.flags})
            await self.store.put_series(doc)

        loaded = _slice(doc, start, end)
        loaded.meta["station"] = {**(loaded.meta.get("station") or {}), "distance_km": st["distance_km"]}
        return loaded


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

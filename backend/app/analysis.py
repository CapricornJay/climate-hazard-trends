import asyncio
import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone

import numpy as np

from . import insights
from .cleaning import QCReport, area_mean, clean_point, fill_short_gaps, incomplete_years
from .hazards import ANALYSERS, HAZARDS, InsufficientData, Params
from .models import AnalysisIn
from .regions import Region, admin_region, box_region, point_region, snap
from .series import SeriesRepo
from .sources.ghcn import QFLAGS
from .sources.nominatim import Nominatim
from .store import pack, unpack
from .timeseries import days, years_of
from .trends import summarize

log = logging.getLogger(__name__)

# Bump when the hazard/trend logic changes so old cached analyses aren't served.
ALGO_VERSION = 5
MAX_EVENTS = 3000

SOURCES = {
    "era5": {
        "id": "era5",
        "label": "ERA5 reanalysis (Open-Meteo)",
        "regions": ["point", "box", "admin"],
        "attribution": "ERA5: Hersbach et al. (2023), Copernicus C3S / ECMWF, served by Open-Meteo.com (CC BY 4.0)",
    },
    "ghcn": {
        "id": "ghcn",
        "label": "NOAA GHCN-Daily station",
        "regions": ["point"],
        "attribution": "GHCN-Daily: Menne et al. (2012), NOAA National Centers for Environmental Information",
    },
}


def region_key(r) -> str:
    if r.kind == "point":
        return f"point:{snap(r.lat):.2f},{snap(r.lon):.2f}"
    if r.kind == "box":
        return f"box:{r.south:.2f},{r.west:.2f},{r.north:.2f},{r.east:.2f}"
    return f"admin:{r.osm_type}{r.osm_id}"


def analysis_id(req: AnalysisIn) -> str:
    canonical = {
        "v": ALGO_VERSION,
        "region": region_key(req.region),
        "hazard": req.hazard,
        "source": req.source,
        "years": [req.start_year, req.end_year],
        "baseline": [req.baseline_start, req.baseline_end],
        "percentile": req.percentile,
        "min_duration": req.min_duration,
        "threshold": req.threshold_mode,
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:24]


def _clean_json(obj):
    """NaN/inf aren't valid JSON; numpy scalars aren't JSON at all."""
    if isinstance(obj, dict):
        return {k: _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def to_public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "expires_at", "daily")}
    out["id"] = doc["_id"]
    d = doc["daily"]
    values = unpack(d["data"])
    out["daily"] = {"start": d["start"], "end": d["end"], "values": [None if np.isnan(v) else round(float(v), 1) for v in values]}
    return _clean_json(out)


def _once(fn):
    done = False

    async def wrapper():
        nonlocal done
        if not done:
            done = True
            await fn()

    return wrapper


class AnalysisService:
    def __init__(self, store, series: SeriesRepo, nominatim: Nominatim, max_points: int):
        self.store = store
        self.series = series
        self.nominatim = nominatim
        self.max_points = max_points
        self._inflight: dict[str, asyncio.Task] = {}

    async def get(self, aid: str) -> dict | None:
        doc = await self.store.get_analysis(aid)
        return to_public(doc) if doc else None

    async def run(self, req: AnalysisIn, before_fetch) -> tuple[dict, bool]:
        """Returns (analysis, served_from_cache)."""
        aid = analysis_id(req)
        doc = await self.store.get_analysis(aid)
        if doc:
            return self._relabel(to_public(doc), req), True

        task = self._inflight.get(aid)
        fresh = task is None
        if fresh:
            # A task rather than a bare await, so the work finishes (and gets
            # cached) even if the browser that asked for it goes away, and so
            # identical requests arriving meanwhile share it instead of
            # fetching the same data twice.
            task = asyncio.ensure_future(self._compute(aid, req, before_fetch))
            self._inflight[aid] = task

            def _done(t: asyncio.Task):
                self._inflight.pop(aid, None)
                if not t.cancelled():
                    t.exception()  # mark as retrieved; awaiters re-raise it anyway

            task.add_done_callback(_done)
        result = await asyncio.shield(task)
        return self._relabel(result, req), not fresh

    @staticmethod
    def _relabel(doc: dict, req: AnalysisIn) -> dict:
        # Nearby points share a grid cell and therefore a cached analysis;
        # show the name the user actually picked.
        if req.region.kind == "point" and req.region.label:
            doc = {**doc, "region": {**doc["region"], "label": req.region.label}}
        return doc

    async def admin_area(self, osm_type: str, osm_id: int) -> dict | None:
        key = f"area|{osm_type}{osm_id}"
        area = await self.store.get_lookup(key)
        if area is None:
            area = await self.nominatim.lookup(osm_type, osm_id)
            if area:
                await self.store.put_lookup(key, area, 90 * 86400)
        return area

    async def _region(self, r) -> Region:
        if r.kind == "point":
            return point_region(r.lat, r.lon, r.label)
        if r.kind == "box":
            return box_region(r.south, r.west, r.north, r.east, r.label, self.max_points)
        area = await self.admin_area(r.osm_type, r.osm_id)
        if not area:
            raise InsufficientData("Couldn't load the boundary for that area from OpenStreetMap.")
        return admin_region(area, self.max_points)

    async def _compute(self, aid: str, req: AnalysisIn, before_fetch) -> dict:
        hz = HAZARDS[req.hazard]
        region = await self._region(req.region)
        start = date(min(req.start_year, req.baseline_start), 1, 1)
        end = date(max(req.end_year, req.baseline_end), 12, 31)
        fetch_guard = _once(before_fetch)
        qc = QCReport(expected_days=(end - start).days + 1)
        region_out = region.to_dict()

        if req.source == "era5":
            loaded = await self.series.grid(hz.variable, region.points, start, end, fetch_guard)
            cleaned = []
            for item, pt in zip(loaded, region_out["points"]):
                v, q = clean_point(item.values, hz.variable)
                q.duplicates = item.duplicates
                qc.add(q)
                cleaned.append(v)
                pt.update({k: item.meta.get(k) for k in ("cell_lat", "cell_lon", "elevation")})
            weights = np.array([p.weight for p in region.points])
            values = cleaned[0] if len(cleaned) == 1 else area_mean(np.vstack(cleaned), weights)
            source = {k: SOURCES["era5"][k] for k in ("id", "label", "attribution")}
            source["detail"] = f"ERA5 0.25° grid, {len(cleaned)} cell(s), models=era5"
        else:
            req_pt = region_out.get("requested") or {"lat": region.points[0].lat, "lon": region.points[0].lon}
            item = await self.series.station(hz.variable, req_pt["lat"], req_pt["lon"], start, end, fetch_guard)
            values, q = clean_point(item.values, hz.variable)
            q.duplicates = item.duplicates
            q.source_flagged = item.flagged
            q.source_flags = {QFLAGS.get(k, k): n for k, n in item.flags.items()}
            qc.add(q)
            station = item.meta.get("station") or {}
            region_out["station"] = station
            source = {k: SOURCES["ghcn"][k] for k in ("id", "label", "attribution")}
            source["station"] = station
            source["detail"] = f"{station.get('name')} ({station.get('id')}), {station.get('distance_km')} km away"

        qc.missing = int(np.isnan(values).sum())
        if hz.variable != "precip":
            values, qc.filled = fill_short_gaps(values)
        qc.incomplete_years = incomplete_years(values, start)

        params = Params(req.start_year, req.end_year, (req.baseline_start, req.baseline_end),
                        req.percentile, req.min_duration, req.threshold_mode)
        result = ANALYSERS[hz.id](values, start, params)

        years = years_of(days(start, end))
        for row in result["annual"]:
            in_year = values[years == row["year"]]
            row["coverage"] = round(float(np.mean(~np.isnan(in_year))), 3)
            row["complete"] = row["year"] not in qc.incomplete_years

        usable = [r for r in result["annual"] if r["complete"]]
        trends = {m.key: summarize([r["year"] for r in usable], [r[m.key] for r in usable], m.kind) for m in hz.metrics}

        quality = qc.to_dict()
        quality["points"] = len(region.points)
        text = insights.build(hz, params, result, trends, region_out, quality, source)

        w0 = (date(req.start_year, 1, 1) - start).days
        w1 = (date(req.end_year, 12, 31) - start).days
        doc = _clean_json({
            "_id": aid,
            "version": ALGO_VERSION,
            "request": req.model_dump(),
            "region": region_out,
            "hazard": hz.to_dict(),
            "source": source,
            "threshold": {**result["threshold"], "percentile": req.percentile,
                          "baseline": [req.baseline_start, req.baseline_end], "min_duration": req.min_duration},
            "definition": result["definition"],
            "annual": result["annual"],
            "events": result["events"][:MAX_EVENTS],
            "events_total": len(result["events"]),
            "trends": trends,
            "insights": text,
            "quality": quality,
        })
        doc["created_at"] = datetime.now(timezone.utc)
        doc["daily"] = {"start": f"{req.start_year}-01-01", "end": f"{req.end_year}-12-31", "data": pack(values[w0:w1 + 1])}
        await self.store.put_analysis(doc)
        log.info("analysis %s computed: %s %s %s-%s", aid, region.key, hz.id, req.start_year, req.end_year)
        return to_public(doc)

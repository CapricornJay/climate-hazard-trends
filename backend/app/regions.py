"""Turning "a city / a state / a box" into a handful of ERA5 grid points.

Every sample point sits on a global lattice that is a multiple of the ERA5
0.25 deg grid, so overlapping regions (and repeat visits) reuse cached cells
instead of costing more Open-Meteo quota.
"""

import math
from dataclasses import dataclass, field

import numpy as np

GRID = 0.25
SPACINGS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0)


def snap(value: float, step: float = GRID) -> float:
    return round(round(value / step) * step, 4)


@dataclass
class SamplePoint:
    lat: float
    lon: float
    weight: float


@dataclass
class Region:
    kind: str  # point | box | admin
    key: str  # stable id used in cache keys
    label: str
    bbox: tuple[float, float, float, float]  # south, west, north, east
    points: list[SamplePoint]
    geometry: dict | None = None
    spacing: float = GRID
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "bbox": list(self.bbox),
            "geometry": self.geometry,
            "spacing_deg": self.spacing,
            "points": [{"lat": p.lat, "lon": p.lon, "weight": round(p.weight, 4)} for p in self.points],
            **self.extra,
        }


def point_region(lat: float, lon: float, label: str | None) -> Region:
    slat, slon = snap(lat), snap(lon)
    return Region(
        kind="point",
        key=f"point:{slat:.2f},{slon:.2f}",
        label=label or f"{lat:.2f}, {lon:.2f}",
        bbox=(slat - GRID / 2, slon - GRID / 2, slat + GRID / 2, slon + GRID / 2),
        points=[SamplePoint(slat, slon, 1.0)],
        extra={"requested": {"lat": lat, "lon": lon}},
    )


def box_region(south: float, west: float, north: float, east: float, label: str | None, max_points: int) -> Region:
    s, w, n, e = (round(v, 2) for v in (south, west, north, east))
    geometry = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    points, spacing = sample_lattice(geometry, (s, w, n, e), max_points)
    return Region(
        kind="box",
        key=f"box:{s:.2f},{w:.2f},{n:.2f},{e:.2f}",
        label=label or f"Box {s:.2f}..{n:.2f}N, {w:.2f}..{e:.2f}E",
        bbox=(s, w, n, e),
        points=points,
        geometry=geometry,
        spacing=spacing,
    )


def admin_region(area: dict, max_points: int) -> Region:
    s, w, n, e = area["bbox"]
    points, spacing = sample_lattice(area["geometry"], (s, w, n, e), max_points)
    return Region(
        kind="admin",
        key=f"admin:{area['osm_type']}{area['osm_id']}",
        label=area["label"],
        bbox=(s, w, n, e),
        points=points,
        geometry=area["geometry"],
        spacing=spacing,
        extra={"osm": f"{area['osm_type']}{area['osm_id']}"},
    )


def _polygons(geometry: dict) -> list:
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


def contains(geometry: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Even-odd ray casting, vectorised over points. Holes handled because
    every ring of a polygon toggles the same parity."""
    inside_any = np.zeros(lats.size, dtype=bool)
    for rings in _polygons(geometry):
        parity = np.zeros(lats.size, dtype=bool)
        for ring in rings:
            r = np.asarray(ring, dtype=float)
            if r.shape[0] < 3:
                continue
            x1, y1 = r[:, 0], r[:, 1]
            x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
            py, px = lats[:, None], lons[:, None]
            crosses = (y1 > py) != (y2 > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                x_at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            hits = crosses & (px < x_at)
            parity ^= (hits.sum(axis=1) % 2).astype(bool)
        inside_any |= parity
    return inside_any


def sample_lattice(geometry: dict, bbox, max_points: int) -> tuple[list[SamplePoint], float]:
    """Pick the finest lattice spacing that puts at most `max_points` points
    inside the shape. Points are weighted by cos(latitude) so a box spanning
    20 degrees of latitude doesn't over-weight its poleward edge."""
    s, w, n, e = bbox
    previous: list[tuple[float, float]] = []
    prev_step = GRID
    for step in SPACINGS:
        lats = np.arange(math.ceil(s / step) * step, n + 1e-9, step)
        lons = np.arange(math.ceil(w / step) * step, e + 1e-9, step)
        if lats.size * lons.size > 40 * max_points and step != SPACINGS[-1]:
            continue  # clearly too fine, skip the point-in-polygon work
        glat, glon = np.meshgrid(lats, lons, indexing="ij")
        glat, glon = glat.ravel(), glon.ravel()
        mask = contains(geometry, glat, glon) if glat.size else np.zeros(0, dtype=bool)
        found = [(round(float(a), 4), round(float(b), 4)) for a, b in zip(glat[mask], glon[mask])]
        if 0 < len(found) <= max_points:
            return _weighted(found), step
        if not found:
            break
        previous, prev_step = found, step

    if previous:
        # Too many at the last spacing that still hit the shape; thin evenly.
        idx = np.linspace(0, len(previous) - 1, max_points).round().astype(int)
        return _weighted([previous[i] for i in sorted(set(idx))]), prev_step

    # Region smaller than a grid cell (or a sliver): use one representative point.
    lat, lon = _representative_point(geometry, bbox)
    return [SamplePoint(snap(lat), snap(lon), 1.0)], GRID


def _weighted(points: list[tuple[float, float]]) -> list[SamplePoint]:
    return [SamplePoint(lat, lon, max(math.cos(math.radians(lat)), 0.01)) for lat, lon in points]


def _representative_point(geometry: dict, bbox) -> tuple[float, float]:
    s, w, n, e = bbox
    clat, clon = (s + n) / 2, (w + e) / 2
    if contains(geometry, np.array([clat]), np.array([clon]))[0]:
        return clat, clon
    # Fall back to the vertex centroid of the largest outer ring.
    rings = [p[0] for p in _polygons(geometry)]
    ring = np.asarray(max(rings, key=len), dtype=float)
    return float(ring[:, 1].mean()), float(ring[:, 0].mean())

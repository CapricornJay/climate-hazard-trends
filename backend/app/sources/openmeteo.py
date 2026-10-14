"""Open-Meteo: ERA5 daily series (archive API) and place search (geocoding API)."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date

import httpx
import numpy as np

from ..cleaning import align_to_calendar, normalize_units, to_float_array
from .http import UpstreamBusy, UpstreamError, get_json

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

VARIABLES = {
    "tmax": "temperature_2m_max",
    "tmin": "temperature_2m_min",
    "precip": "precipitation_sum",
}


def call_weight(n_locations: int, n_variables: int, n_days: int) -> float:
    """What Open-Meteo charges for one request, in "API calls".

    Mirrors calculateQueryWeight() in the open-meteo server: per location,
    max(1, nvars/10, nvars/10 * days/14). So 65 years of a single daily
    variable at one grid cell costs ~170 calls, and the free tier's
    "10,000 calls a day" is really ~60 cell-records a day.
    """
    vf = n_variables / 10
    return n_locations * max(1.0, vf, vf * n_days / 14)


class CallBudget:
    """Keeps our Open-Meteo spend under the free-tier limits.

    Open-Meteo counts weighted calls per IP in fixed minute/hour/day windows
    and lets a request through as long as the counter is under the limit when
    it arrives, so one heavy multi-location request can overshoot a minute.
    We mirror that locally so we can wait for the next minute, or fail early
    with a useful message, instead of collecting 429s.

    In-process only. With several replicas this would need to live in Redis
    or Mongo; we also can't see other tenants sharing our egress IP, which is
    why 429s from upstream feed back into it via exhaust().
    """

    SPANS = {"minute": 60, "hour": 3600, "day": 86400}

    def __init__(self, per_minute: float, per_hour: float, per_day: float, clock=time.time, sleep=asyncio.sleep):
        self.limits = {"minute": per_minute, "hour": per_hour, "day": per_day}
        self.used = dict.fromkeys(self.SPANS, 0.0)
        self._window = dict.fromkeys(self.SPANS, -1)
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    def _roll(self, now: float) -> None:
        for name, span in self.SPANS.items():
            w = int(now // span)
            if w != self._window[name]:
                self._window[name] = w
                self.used[name] = 0.0

    def _until_reset(self, name: str, now: float) -> float:
        span = self.SPANS[name]
        return span - (now % span)

    async def acquire(self, weight: float, max_wait: float = 25.0) -> None:
        if weight > self.limits["hour"]:
            raise UpstreamBusy("That region needs more upstream data than we can fetch in one go; try a smaller region")
        async with self._lock:
            while True:
                now = self._clock()
                self._roll(now)
                for name in ("day", "hour"):
                    if self.used[name] + weight > self.limits[name]:
                        wait = int(self._until_reset(name, now)) + 1
                        raise UpstreamBusy(
                            f"We've used this {name}'s share of the free Open-Meteo quota. "
                            "Regions that were analysed before still work.",
                            retry_after=wait,
                        )
                if self.used["minute"] >= self.limits["minute"]:
                    wait = self._until_reset("minute", now) + 0.5
                    if wait > max_wait:
                        raise UpstreamBusy("Open-Meteo quota is busy, try again in a minute", retry_after=int(wait) + 1)
                    log.info("open-meteo minute budget spent, waiting %.1fs", wait)
                    await self._sleep(wait)
                    continue
                for name in self.used:
                    self.used[name] += weight
                return

    def exhaust(self, name: str) -> None:
        self._roll(self._clock())
        self.used[name] = max(self.used[name], self.limits[name])

    def snapshot(self) -> dict:
        self._roll(self._clock())
        return {k: {"used": round(self.used[k], 1), "limit": self.limits[k]} for k in self.SPANS}


def _limit_window(reason: str) -> str | None:
    reason = reason.lower()
    for word, name in (("minutely", "minute"), ("hourly", "hour"), ("daily", "day")):
        if word in reason:
            return name
    return None


@dataclass
class GridSeries:
    lat: float  # what we asked for (snapped to the 0.25 deg grid)
    lon: float
    cell_lat: float | None  # grid cell Open-Meteo actually used
    cell_lon: float | None
    elevation: float | None
    start: date
    values: np.ndarray  # canonical units, NaN = missing, one value per day
    duplicates: int


class OpenMeteo:
    def __init__(self, http: httpx.AsyncClient, budget: CallBudget, archive_url: str = ARCHIVE_URL, geocoding_url: str = GEOCODING_URL):
        self.http = http
        self.budget = budget
        self.archive_url = archive_url
        self.geocoding_url = geocoding_url

    async def fetch_daily(self, points: list[tuple[float, float]], variable: str, start: date, end: date) -> list[GridSeries]:
        """One request for all points. Weight is the same as N separate
        requests, but it's one round trip and one slot in the minute window."""
        name = VARIABLES[variable]
        n_days = (end - start).days + 1
        weight = call_weight(len(points), 1, n_days)
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": name,
            "timezone": "auto",
            # Pin ERA5. The default "best_match" switches to the 9 km IFS model
            # from 2017 on, which would put a resolution change in the middle of
            # most trend windows. Open-Meteo's own docs say to pin ERA5 or
            # ERA5-Land for multi-decade climate work.
            "models": "era5",
        }
        for attempt in range(2):
            await self.budget.acquire(weight)
            try:
                data = await get_json(
                    self.http, self.archive_url, params, name="Open-Meteo",
                    retries=2, timeout=httpx.Timeout(120.0, connect=5.0),
                )
                break
            except UpstreamBusy as exc:
                window = _limit_window(exc.detail) or "minute"
                self.budget.exhaust(window)
                log.warning("open-meteo 429 (%s window): %s", window, exc.detail[:200])
                if window != "minute" or attempt == 1:
                    raise
        log.info("open-meteo: %d point(s) %s %s..%s weight=%.0f", len(points), variable, start, end, weight)

        items = data if isinstance(data, list) else [data]
        if len(items) != len(points):
            raise UpstreamError("Open-Meteo returned an unexpected number of locations")
        return [self._parse(item, pt, name, variable, start, end) for item, pt in zip(items, points)]

    @staticmethod
    def _parse(item: dict, point: tuple[float, float], name: str, variable: str, start: date, end: date) -> GridSeries:
        daily = item.get("daily") if isinstance(item, dict) else None
        if not isinstance(daily, dict):
            raise UpstreamError("Open-Meteo response had no daily data")
        times, raw = daily.get("time"), daily.get(name)
        if not isinstance(times, list) or not isinstance(raw, list) or len(times) != len(raw):
            raise UpstreamError("Open-Meteo response had malformed daily data")
        unit = str((item.get("daily_units") or {}).get(name, ""))
        try:
            values = normalize_units(to_float_array(raw), unit, variable)
            dates = np.array(times, dtype="datetime64[D]")
        except ValueError as exc:
            log.error("open-meteo parse failure: %s", exc)
            raise UpstreamError("Open-Meteo returned data in an unexpected format") from exc
        aligned, dupes = align_to_calendar(dates, values, start, end)
        return GridSeries(
            lat=point[0],
            lon=point[1],
            cell_lat=_num(item.get("latitude")),
            cell_lon=_num(item.get("longitude")),
            elevation=_num(item.get("elevation")),
            start=start,
            values=aligned,
            duplicates=dupes,
        )

    async def search_places(self, query: str, count: int = 8) -> list[dict]:
        data = await get_json(
            self.http, self.geocoding_url,
            {"name": query, "count": count, "language": "en", "format": "json"},
            name="Open-Meteo geocoding", retries=1, timeout=10.0,
        )
        out = []
        for r in (data or {}).get("results") or []:
            lat, lon = _num(r.get("latitude")), _num(r.get("longitude"))
            if lat is None or lon is None or not r.get("name"):
                continue
            out.append({
                "id": r.get("id"),
                "name": str(r["name"]),
                "admin1": r.get("admin1"),
                "country": r.get("country"),
                "country_code": r.get("country_code"),
                "lat": lat,
                "lon": lon,
                "population": r.get("population"),
            })
        return out


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None

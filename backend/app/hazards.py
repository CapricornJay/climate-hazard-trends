"""Hazard definitions: thresholds, event detection and yearly indicators.

Heatwave (default)
    At least N consecutive days (default 3) with Tmax above the p-th
    percentile (default 95th) of a fixed baseline period. Two flavours of
    threshold:
      fixed - one number for the whole year, so heatwaves can only happen in
              the hot season. This is what the brief describes and what most
              people mean by "heatwave".
      doy   - a threshold per calendar day from a 15-day window across the
              baseline years (the ETCCDI / Perkins & Alexander approach).
              Catches spells that are unusually hot for the time of year,
              including in winter.

Heavy rain
    Days with precipitation above the p-th percentile of *wet* days (>= 1 mm)
    in the baseline. Using all days would put the threshold at ~0 mm in dry
    climates. This is the ETCCDI R95p idea.

Events are assigned to the year in which they start, including all their
days, so a heatwave from Dec 30 to Jan 2 belongs to the December year.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np

from .timeseries import day_of_year_365, days, find_runs, years_of

WET_DAY_MM = 1.0
DOY_HALF_WINDOW = 7  # 15-day window
MIN_BASELINE_COVERAGE = 0.7


class InsufficientData(ValueError):
    """Raised with a message that can go straight to the user."""


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    kind: str  # "count" or "continuous"; picks the trend model
    description: str

    def to_dict(self):
        return self.__dict__.copy()


HEATWAVE_METRICS = [
    Metric("days", "Heatwave days", "days/yr", "count", "Days per year that were part of a heatwave"),
    Metric("events", "Heatwave events", "events/yr", "count", "Separate heatwaves starting in the year"),
    Metric("longest", "Longest heatwave", "days", "continuous", "Length of the year's longest heatwave (0 if none)"),
    Metric("mean_excess", "Mean intensity", "°C over threshold", "continuous",
           "How far heatwave days exceeded the threshold, on average (years with no heatwave left out)"),
    Metric("peak", "Hottest heatwave day", "°C", "continuous", "Highest daily maximum reached during a heatwave"),
    Metric("cum_excess", "Cumulative heat", "°C·days", "continuous",
           "Sum of the exceedance over every heatwave day: a rough heat-load measure"),
]

HEAVY_RAIN_METRICS = [
    Metric("days", "Heavy-rain days", "days/yr", "count", "Days above the heavy-rain threshold"),
    Metric("events", "Heavy-rain events", "events/yr", "count", "Separate heavy-rain episodes starting in the year"),
    Metric("heavy_total", "Rain on heavy days", "mm", "continuous", "Precipitation that fell on heavy-rain days (ETCCDI R95pTOT)"),
    Metric("rx1day", "Wettest day", "mm", "continuous", "Largest one-day total of the year (ETCCDI Rx1day)"),
    Metric("heavy_share", "Share from heavy days", "%", "continuous",
           "Fraction of the year's wet-day precipitation that came from heavy-rain days"),
]


@dataclass(frozen=True)
class Hazard:
    id: str
    label: str
    variable: str
    unit: str
    metrics: list
    primary: str
    default_percentile: float
    default_min_duration: int
    min_duration_range: tuple[int, int]
    threshold_modes: tuple[str, ...]

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "variable": self.variable,
            "unit": self.unit,
            "metrics": [m.to_dict() for m in self.metrics],
            "primary_metric": self.primary,
            "defaults": {"percentile": self.default_percentile, "min_duration": self.default_min_duration},
            "min_duration_range": list(self.min_duration_range),
            "threshold_modes": list(self.threshold_modes),
        }


HAZARDS = {
    "heatwave": Hazard("heatwave", "Heatwave", "tmax", "°C", HEATWAVE_METRICS, "days", 95.0, 3, (2, 10), ("fixed", "doy")),
    "heavy_rain": Hazard("heavy_rain", "Heavy rainfall", "precip", "mm", HEAVY_RAIN_METRICS, "days", 95.0, 1, (1, 5), ("fixed",)),
}


@dataclass
class Params:
    start_year: int
    end_year: int
    baseline: tuple[int, int]
    percentile: float
    min_duration: int
    threshold_mode: str = "fixed"


def _baseline_values(values, years, baseline):
    b0, b1 = baseline
    in_base = (years >= b0) & (years <= b1)
    n_expected = int(in_base.sum())
    sel = values[in_base]
    sel_ok = ~np.isnan(sel)
    if n_expected == 0 or sel_ok.sum() < MIN_BASELINE_COVERAGE * n_expected:
        raise InsufficientData(
            f"Not enough data in the {b0}-{b1} baseline to set a threshold "
            f"({sel_ok.sum()} of {n_expected} days). Pick a different baseline or source."
        )
    return in_base


def fixed_threshold(values, years, baseline, percentile, wet_only=False) -> float:
    in_base = _baseline_values(values, years, baseline)
    sel = values[in_base]
    sel = sel[~np.isnan(sel)]
    if wet_only:
        sel = sel[sel >= WET_DAY_MM]
        if sel.size < 30:
            raise InsufficientData("Too few wet days in the baseline to define heavy rain here.")
    return float(np.percentile(sel, percentile))


def doy_threshold(values, years, doy, baseline, percentile) -> np.ndarray:
    in_base = _baseline_values(values, years, baseline) & ~np.isnan(values)
    v, d = values[in_base], doy[in_base]
    order = np.argsort(d, kind="stable")
    v, d = v[order], d[order]
    bounds = np.searchsorted(d, np.arange(366))
    out = np.empty(365)
    for k in range(365):
        parts = [v[bounds[w]:bounds[w + 1]] for w in ((k + o) % 365 for o in range(-DOY_HALF_WINDOW, DOY_HALF_WINDOW + 1))]
        pooled = np.concatenate(parts)
        out[k] = np.percentile(pooled, percentile) if pooled.size else np.nan
    return out


def _r(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _events(values, dates, thr_daily, min_duration, years_window):
    """Runs of values > threshold. NaN is never an exceedance, so a missing
    day breaks a run: we can't claim three hot days in a row if we didn't
    see the middle one."""
    with np.errstate(invalid="ignore"):
        over = values > thr_daily
    y0, y1 = years_window
    out = []
    for s, e in find_runs(over, min_duration):
        start_year = int(str(dates[s])[:4])
        if start_year < y0 or start_year > y1:
            continue
        out.append((s, e))
    return out


def analyse_heatwave(values: np.ndarray, start: date, p: Params) -> dict:
    dates = days(start, date.fromordinal(start.toordinal() + values.size - 1))
    years = years_of(dates)
    if p.threshold_mode == "doy":
        doy = day_of_year_365(dates)
        thr_doy = doy_threshold(values, years, doy, p.baseline, p.percentile)
        thr_daily = thr_doy[doy]
        lo, hi = np.nanmin(thr_doy), np.nanmax(thr_doy)
        threshold = {"mode": "doy", "by_doy": [_r(x) for x in thr_doy], "min": _r(lo), "max": _r(hi)}
        definition = (
            f"At least {p.min_duration} consecutive days with a daily maximum above the {p.percentile:g}th "
            f"percentile for that time of year ({p.baseline[0]}-{p.baseline[1]}, 15-day window). "
            f"The threshold runs from {lo:.1f} °C to {hi:.1f} °C over the year."
        )
    else:
        thr = fixed_threshold(values, years, p.baseline, p.percentile)
        thr_daily = np.full(values.size, thr)
        threshold = {"mode": "fixed", "value": _r(thr)}
        definition = (
            f"At least {p.min_duration} consecutive days with a daily maximum above {thr:.1f} °C, "
            f"the {p.percentile:g}th percentile of all days in {p.baseline[0]}-{p.baseline[1]}."
        )

    events = []
    for s, e in _events(values, dates, thr_daily, p.min_duration, (p.start_year, p.end_year)):
        seg = values[s:e + 1]
        exc = seg - thr_daily[s:e + 1]
        k = int(np.argmax(seg))
        events.append({
            "start": str(dates[s]),
            "end": str(dates[e]),
            "duration": e - s + 1,
            "peak": _r(seg[k]),
            "peak_date": str(dates[s + k]),
            "mean": _r(seg.mean()),
            "mean_excess": _r(exc.mean(), 2),
            "cum_excess": _r(exc.sum()),
        })

    annual = []
    for y in range(p.start_year, p.end_year + 1):
        evs = [ev for ev in events if int(ev["start"][:4]) == y]
        n_days = sum(ev["duration"] for ev in evs)
        cum = sum(ev["cum_excess"] for ev in evs)
        annual.append({
            "year": y,
            "events": len(evs),
            "days": n_days,
            "longest": max((ev["duration"] for ev in evs), default=0),
            "mean_excess": _r(cum / n_days, 2) if n_days else None,
            "peak": max((ev["peak"] for ev in evs), default=None),
            "cum_excess": _r(cum),
        })
    return {"threshold": threshold, "definition": definition, "events": events, "annual": annual}


def analyse_heavy_rain(values: np.ndarray, start: date, p: Params) -> dict:
    dates = days(start, date.fromordinal(start.toordinal() + values.size - 1))
    years = years_of(dates)
    thr = fixed_threshold(values, years, p.baseline, p.percentile, wet_only=True)
    thr_daily = np.full(values.size, thr)
    threshold = {"mode": "fixed", "value": _r(thr)}
    run = "day" if p.min_duration == 1 else f"run of at least {p.min_duration} days"
    definition = (
        f"A {run} with more than {thr:.1f} mm of precipitation, the {p.percentile:g}th percentile "
        f"of wet days (>= {WET_DAY_MM:g} mm) in {p.baseline[0]}-{p.baseline[1]}."
    )

    events = []
    for s, e in _events(values, dates, thr_daily, p.min_duration, (p.start_year, p.end_year)):
        seg = values[s:e + 1]
        k = int(np.argmax(seg))
        events.append({
            "start": str(dates[s]),
            "end": str(dates[e]),
            "duration": e - s + 1,
            "peak": _r(seg[k]),
            "peak_date": str(dates[s + k]),
            "total": _r(seg.sum()),
            "excess": _r((seg - thr).sum()),
        })

    annual = []
    for y in range(p.start_year, p.end_year + 1):
        evs = [ev for ev in events if int(ev["start"][:4]) == y]
        in_year = values[years == y]
        wet_total = np.nansum(np.where(in_year >= WET_DAY_MM, in_year, 0.0))
        heavy_total = sum(ev["total"] for ev in evs)
        annual.append({
            "year": y,
            "events": len(evs),
            "days": sum(ev["duration"] for ev in evs),
            "heavy_total": _r(heavy_total),
            "rx1day": _r(np.nanmax(in_year)) if np.isfinite(in_year).any() else None,
            "heavy_share": _r(100 * heavy_total / wet_total) if wet_total > 0 else None,
        })
    return {"threshold": threshold, "definition": definition, "events": events, "annual": annual}


ANALYSERS = {"heatwave": analyse_heatwave, "heavy_rain": analyse_heavy_rain}

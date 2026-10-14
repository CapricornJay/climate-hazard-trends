"""Turning raw daily values into something the hazard code can trust.

Order matters: convert units first, then put everything on a complete daily
calendar (so "consecutive rows" really means "consecutive days"), then throw
out values that can't be real, and only after that fill short gaps.
"""

from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

from .timeseries import days, find_runs, years_of

# Deliberately loose. These catch unit mix-ups and sentinels like -9999; they
# must not trim genuine extremes, because extremes are what we're measuring.
LIMITS = {
    "tmax": (-90.0, 60.0),  # records: -89.2 C (Vostok), 56.7 C (Death Valley)
    "tmin": (-95.0, 45.0),
    "precip": (0.0, 1900.0),  # wettest day on record ~1825 mm (La Reunion, 1966)
}

# ETCCDI rule for annual indices: a year counts if no more than 15 days are missing.
MAX_MISSING_DAYS_PER_YEAR = 15


def _f_to_c(v):
    return (v - 32.0) * 5.0 / 9.0


_UNITS = {
    "temperature": {
        "°c": lambda v: v,
        "c": lambda v: v,
        "celsius": lambda v: v,
        "°f": _f_to_c,
        "f": _f_to_c,
        "fahrenheit": _f_to_c,
        "0.1 °c": lambda v: v / 10.0,  # raw GHCN-Daily files
    },
    "precip": {
        "mm": lambda v: v,
        "inch": lambda v: v * 25.4,
        "in": lambda v: v * 25.4,
        "0.1 mm": lambda v: v / 10.0,  # raw GHCN-Daily files
    },
}


def normalize_units(values, unit: str, variable: str) -> np.ndarray:
    """Convert to °C / mm. Unknown units are an error, not a guess."""
    table = _UNITS["precip" if variable == "precip" else "temperature"]
    try:
        convert = table[unit.strip().lower()]
    except KeyError:
        raise ValueError(f"don't know how to convert {variable} from {unit!r}") from None
    return convert(np.asarray(values, dtype=float))


def to_float_array(raw: list) -> np.ndarray:
    """JSON list -> float array with NaN for null/garbage."""
    try:
        return np.array(raw, dtype=float)  # None becomes nan
    except (TypeError, ValueError):
        out = np.full(len(raw), np.nan)
        for i, v in enumerate(raw):
            try:
                out[i] = float(v)
            except (TypeError, ValueError):
                pass
        return out


def align_to_calendar(dates, values, start: date, end: date) -> tuple[np.ndarray, int]:
    """Place values on a gap-free daily calendar [start, end].

    Dates that never appear become NaN. If a date appears twice, the first
    non-missing value wins. Returns (series, number of duplicate rows dropped).
    """
    dates = np.asarray(dates, dtype="datetime64[D]")
    values = np.asarray(values, dtype=float)
    n = (end - start).days + 1
    idx = (dates - np.datetime64(start, "D")).astype(np.int64)
    ok = (idx >= 0) & (idx < n) & ~np.isnan(values)
    idx, values = idx[ok], values[ok]
    out = np.full(n, np.nan)
    uniq, first = np.unique(idx, return_index=True)
    out[uniq] = values[first]
    return out, int(idx.size - uniq.size)


@dataclass
class QCReport:
    expected_days: int = 0
    missing: int = 0  # days with no usable value, before gap filling
    filled: int = 0  # short gaps filled by interpolation
    out_of_range: int = 0
    spikes: int = 0
    duplicates: int = 0
    source_flagged: int = 0  # rejected by the provider's own QC flags
    source_flags: dict[str, int] = field(default_factory=dict)
    incomplete_years: list[int] = field(default_factory=list)

    def add(self, other: "QCReport") -> None:
        for name in ("missing", "filled", "out_of_range", "spikes", "duplicates", "source_flagged"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for k, v in other.source_flags.items():
            self.source_flags[k] = self.source_flags.get(k, 0) + v

    def to_dict(self) -> dict:
        return asdict(self)


def drop_out_of_range(values: np.ndarray, variable: str) -> tuple[np.ndarray, int]:
    lo, hi = LIMITS[variable]
    bad = (values < lo) | (values > hi)  # NaN compares False, fine
    if not bad.any():
        return values, 0
    v = values.copy()
    v[bad] = np.nan
    return v, int(bad.sum())


def drop_spikes(values: np.ndarray, jump: float = 10.0, z_limit: float = 8.0) -> tuple[np.ndarray, int]:
    """Remove isolated one-day spikes from a temperature series.

    A day is a spike when it differs from *both* neighbours by more than
    `jump` degrees in the same direction AND its second difference is an
    extreme outlier for the series as a whole. A real hot spell doesn't trip
    this because the days around it are hot too; a keying error usually does.
    """
    if values.size < 3:
        return values, 0
    prev, cur, nxt = values[:-2], values[1:-1], values[2:]
    up, down = cur - prev, cur - nxt
    second = cur - (prev + nxt) / 2
    med = np.nanmedian(second)
    mad = 1.4826 * np.nanmedian(np.abs(second - med))
    if not np.isfinite(mad) or mad == 0:
        return values, 0
    with np.errstate(invalid="ignore"):
        robust_z = np.abs(second - med) / mad
        spike = (np.abs(up) > jump) & (np.abs(down) > jump) & (np.sign(up) == np.sign(down)) & (robust_z > z_limit)
    idx = np.flatnonzero(spike) + 1
    if idx.size == 0:
        return values, 0
    v = values.copy()
    v[idx] = np.nan
    return v, int(idx.size)


def fill_short_gaps(values: np.ndarray, max_gap: int = 2) -> tuple[np.ndarray, int]:
    """Linearly interpolate NaN runs of at most `max_gap` days that have real
    values on both sides. Only for temperature: interpolating rainfall would
    invent drizzle on days that may have been dry."""
    missing = np.isnan(values)
    if not missing.any():
        return values, 0
    v = values.copy()
    filled = 0
    for s, e in find_runs(missing):
        length = e - s + 1
        if length > max_gap or s == 0 or e == v.size - 1:
            continue
        left, right = v[s - 1], v[e + 1]
        v[s : e + 1] = left + (right - left) * np.arange(1, length + 1) / (length + 1)
        filled += length
    return v, filled


def incomplete_years(values: np.ndarray, start: date) -> list[int]:
    end = date.fromordinal(start.toordinal() + values.size - 1)
    years = years_of(days(start, end))
    out = []
    for y in np.unique(years):
        if np.isnan(values[years == y]).sum() > MAX_MISSING_DAYS_PER_YEAR:
            out.append(int(y))
    return out


def clean_point(values: np.ndarray, variable: str) -> tuple[np.ndarray, QCReport]:
    """Range + spike checks for one raw series. Gap filling happens later, on
    the combined regional series, so we don't interpolate twice."""
    qc = QCReport(expected_days=int(values.size))
    v, qc.out_of_range = drop_out_of_range(values, variable)
    if variable != "precip":
        v, qc.spikes = drop_spikes(v)
    return v, qc


def area_mean(stack: np.ndarray, weights: np.ndarray, min_weight_share: float = 0.5) -> np.ndarray:
    """Weighted mean across points (rows) for each day (column).

    A day only gets a value if points carrying at least `min_weight_share` of
    the total weight have data; otherwise one surviving mountain-top cell could
    stand in for a whole state.
    """
    present = ~np.isnan(stack)
    wsum = (weights[:, None] * present).sum(axis=0)
    total = (np.where(present, stack, 0.0) * weights[:, None]).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / wsum
    mean[wsum < min_weight_share * weights.sum()] = np.nan
    return mean

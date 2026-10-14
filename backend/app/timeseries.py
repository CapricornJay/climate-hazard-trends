"""Small helpers for daily series that start on a known date and have no holes
in the calendar (missing values are NaN, never missing rows)."""

from datetime import date

import numpy as np


def days(start: date, end: date) -> np.ndarray:
    return np.arange(np.datetime64(start, "D"), np.datetime64(end, "D") + np.timedelta64(1, "D"))


def years_of(dates: np.ndarray) -> np.ndarray:
    return dates.astype("datetime64[Y]").astype(int) + 1970


def day_of_year_365(dates: np.ndarray) -> np.ndarray:
    """0-based day of year on a 365-day calendar.

    Feb 29 shares Feb 28's slot, which is how the ETCCDI indices treat leap
    days. Without this every date after February would be off by one in leap
    years when we build day-of-year thresholds.
    """
    year_start = dates.astype("datetime64[Y]")
    doy = (dates - year_start).astype(int)
    years = years_of(dates)
    leap = (years % 4 == 0) & ((years % 100 != 0) | (years % 400 == 0))
    return np.where(leap & (doy >= 59), doy - 1, doy)


def find_runs(mask: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """(first, last) index pairs, inclusive, of True runs at least `min_len` long."""
    if mask.size == 0:
        return []
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    keep = (ends - starts + 1) >= min_len
    return [(int(s), int(e)) for s, e in zip(starts[keep], ends[keep])]

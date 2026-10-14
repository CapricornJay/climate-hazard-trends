from datetime import date

import numpy as np
import pytest

from app.hazards import InsufficientData, Params, analyse_heatwave, analyse_heavy_rain, doy_threshold
from app.timeseries import day_of_year_365, days, find_runs, years_of


def flat_series(start, end, value=20.0):
    """Sawtooth 20.0..20.9: gives a well-defined percentile but can never
    produce three exceedances in a row by chance, unlike random noise."""
    n = (end - start).days + 1
    return value + (np.arange(n) % 10) / 10


def index_of(start: date, d: date) -> int:
    return (d - start).days


def test_find_runs():
    mask = np.array([0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1], dtype=bool)
    assert find_runs(mask, 3) == [(1, 3), (8, 11)]
    assert find_runs(mask, 1) == [(1, 3), (5, 6), (8, 11)]


def test_leap_day_shares_feb_28_slot():
    d = days(date(2020, 2, 27), date(2020, 3, 2))
    assert day_of_year_365(d).tolist() == [57, 58, 58, 59, 60]
    d = days(date(2021, 2, 27), date(2021, 3, 2))
    assert day_of_year_365(d).tolist() == [57, 58, 59, 60]


def test_heatwave_needs_three_consecutive_days():
    start = date(1981, 1, 1)
    v = flat_series(start, date(2010, 12, 31))
    i = index_of(start, date(2005, 7, 1))
    v[i:i + 3] = 40  # 3 days -> heatwave
    j = index_of(start, date(2006, 7, 1))
    v[j:j + 2] = 40  # only 2 days -> not a heatwave
    k = index_of(start, date(2007, 7, 1))
    v[k:k + 5] = 40
    v[k + 2] = np.nan  # a missing day splits it into 2 + 2 -> nothing

    res = analyse_heatwave(v, start, Params(2000, 2010, (1981, 2010), 95, 3))
    starts = [e["start"] for e in res["events"]]
    assert starts == ["2005-07-01"]
    ev = res["events"][0]
    assert ev["duration"] == 3 and ev["peak"] == 40
    row = {r["year"]: r for r in res["annual"]}
    assert row[2005]["events"] == 1 and row[2005]["days"] == 3
    assert row[2006]["events"] == 0 and row[2006]["mean_excess"] is None


def test_event_crossing_new_year_belongs_to_start_year():
    start = date(1981, 1, 1)
    v = flat_series(start, date(2010, 12, 31))
    i = index_of(start, date(2004, 12, 30))
    v[i:i + 4] = 38
    res = analyse_heatwave(v, start, Params(2000, 2010, (1981, 2010), 95, 3))
    row = {r["year"]: r for r in res["annual"]}
    assert row[2004]["days"] == 4 and row[2005]["days"] == 0


def test_fixed_threshold_is_the_baseline_percentile():
    start = date(1961, 1, 1)
    v = np.arange((date(1990, 12, 31) - start).days + 1, dtype=float) % 100  # uniform 0..99
    res = analyse_heatwave(v, start, Params(1961, 1990, (1961, 1990), 95, 3))
    assert res["threshold"]["value"] == pytest.approx(np.percentile(v, 95), abs=0.05)


def test_doy_threshold_follows_the_season():
    start = date(1981, 1, 1)
    d = days(start, date(2010, 12, 31))
    doy = day_of_year_365(d)
    seasonal = 15 + 10 * np.sin(2 * np.pi * (doy - 100) / 365)
    v = seasonal + np.random.default_rng(5).normal(0, 2, seasonal.size)
    thr = doy_threshold(v, years_of(d), doy, (1981, 2010), 95)
    assert thr.shape == (365,)
    assert thr[190] > thr[10] + 15  # July threshold far above January's


def test_baseline_without_data_is_rejected():
    start = date(1981, 1, 1)
    v = flat_series(start, date(2010, 12, 31))
    v[:5000] = np.nan
    with pytest.raises(InsufficientData):
        analyse_heatwave(v, start, Params(1995, 2010, (1981, 1995), 95, 3))


def test_heavy_rain_uses_wet_days_only():
    start = date(1981, 1, 1)
    n = (date(2010, 12, 31) - start).days + 1
    rng = np.random.default_rng(7)
    v = np.where(rng.random(n) < 0.8, 0.0, rng.gamma(2, 6, n))  # 80% dry days
    res = analyse_heavy_rain(v, start, Params(1981, 2010, (1981, 2010), 95, 1))
    wet = v[v >= 1]
    assert res["threshold"]["value"] == pytest.approx(np.percentile(wet, 95), abs=0.05)
    # over all days the 95th percentile would sit at a light shower
    assert res["threshold"]["value"] > np.percentile(v, 95) * 1.5
    total_heavy = sum(r["days"] for r in res["annual"])
    assert total_heavy == int((v > res["threshold"]["value"]).sum())

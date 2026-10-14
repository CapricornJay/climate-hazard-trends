from datetime import date

import numpy as np
import pytest

from app.cleaning import (
    align_to_calendar,
    area_mean,
    drop_out_of_range,
    drop_spikes,
    fill_short_gaps,
    incomplete_years,
    normalize_units,
    to_float_array,
)


def test_units_are_converted_explicitly():
    assert normalize_units([32, 212], "°F", "tmax") == pytest.approx([0, 100])
    assert normalize_units([1], "inch", "precip") == pytest.approx([25.4])
    # raw GHCN-Daily stores tenths
    assert normalize_units([253], "0.1 °C", "tmax") == pytest.approx([25.3])
    with pytest.raises(ValueError):
        normalize_units([1], "kelvin", "tmax")


def test_nulls_and_garbage_become_nan():
    v = to_float_array([1.5, None, "x", 3])
    assert v[0] == 1.5 and v[3] == 3
    assert np.isnan(v[1]) and np.isnan(v[2])


def test_missing_dates_become_gaps_not_shifts():
    # Jan 3 is missing from the feed entirely. If we kept rows as-is, Jan 4
    # would sit where Jan 3 should be and "consecutive" would be a lie.
    dates = ["2020-01-01", "2020-01-02", "2020-01-04"]
    out, dupes = align_to_calendar(dates, [1.0, 2.0, 4.0], date(2020, 1, 1), date(2020, 1, 4))
    assert dupes == 0
    assert out[:2].tolist() == [1.0, 2.0]
    assert np.isnan(out[2])
    assert out[3] == 4.0


def test_duplicate_dates_keep_first_real_value():
    dates = ["2020-01-01", "2020-01-01", "2020-01-02", "2020-01-02"]
    out, dupes = align_to_calendar(dates, [np.nan, 5.0, 6.0, 7.0], date(2020, 1, 1), date(2020, 1, 2))
    assert out.tolist() == [5.0, 6.0]
    assert dupes == 1


def test_range_check_catches_sentinels_only():
    v, n = drop_out_of_range(np.array([-9999.0, 25.0, 55.5, 999.9]), "tmax")
    assert n == 2
    assert v[2] == 55.5  # extreme but possible, must survive


def test_spike_check_ignores_real_heatwaves():
    rng = np.random.default_rng(1)
    v = 20 + rng.normal(0, 1.5, 400)
    v[100:104] += 14  # a real 4-day hot spell: neighbours agree with each other
    v[300] += 25  # an isolated keying error
    cleaned, n = drop_spikes(v)
    assert n == 1
    assert np.isnan(cleaned[300])
    assert not np.isnan(cleaned[100:104]).any()


def test_short_gaps_filled_long_gaps_left():
    v = np.array([10, np.nan, 12, 13, np.nan, np.nan, np.nan, 17, np.nan])
    out, n = fill_short_gaps(v, max_gap=2)
    assert n == 1
    assert out[1] == pytest.approx(11)
    assert np.isnan(out[4:7]).all()  # 3-day gap stays missing
    assert np.isnan(out[8])  # no right neighbour, nothing to interpolate towards


def test_incomplete_year_rule():
    start = date(2001, 1, 1)
    v = np.ones(365 * 2)
    v[10:26] = np.nan  # 16 missing days in 2001 -> out
    v[400:410] = np.nan  # 10 missing days in 2002 -> fine
    assert incomplete_years(v, start) == [2001]


def test_area_mean_needs_enough_weight():
    stack = np.array([[10.0, 10.0, np.nan], [20.0, np.nan, np.nan], [30.0, np.nan, 5.0]])
    out = area_mean(stack, np.array([1.0, 1.0, 1.0]))
    assert out[0] == pytest.approx(20)
    assert np.isnan(out[1])  # only a third of the weight present
    assert np.isnan(out[2])

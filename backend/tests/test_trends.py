import numpy as np
import pymannkendall as pmk
import pytest

from app.insights import headline
from app.hazards import HAZARDS, Params
from app.trends import mann_kendall, quasi_poisson, rolling_mean, sens_slope, summarize


@pytest.mark.parametrize("seed", range(8))
def test_mann_kendall_matches_reference(seed):
    rng = np.random.default_rng(seed)
    y = np.round(rng.normal(0, 1, 35) + 0.03 * np.arange(35), 1)  # rounding creates ties
    x = np.arange(1990, 2025, dtype=float)
    ours = mann_kendall(x, y)
    ref = pmk.original_test(y)
    assert ours["s"] == ref.s
    assert ours["tau"] == pytest.approx(ref.Tau)
    assert ours["p_uncorrected"] == pytest.approx(ref.p, rel=1e-6)

    hr = pmk.hamed_rao_modification_test(y)
    if ours["autocorrelation_correction"] >= 1:
        assert ours["p"] == pytest.approx(hr.p, rel=1e-6)
    else:
        # we refuse to let the correction make a result *more* significant
        assert ours["p"] == pytest.approx(ref.p, rel=1e-6)


def test_sens_slope_matches_reference():
    y = np.array([3, 5, 4, 6, 8, 7, 9, 12, 10, 11], dtype=float)
    slope, _, _ = sens_slope(np.arange(10, dtype=float), y)
    assert slope == pytest.approx(pmk.sens_slope(y).slope)


def test_quasi_poisson_recovers_a_known_rate():
    rng = np.random.default_rng(42)
    x = np.arange(1960, 2021, dtype=float)
    true_rate = 3 * np.exp(0.03 * (x - 1960))  # +35% per decade
    y = rng.poisson(true_rate).astype(float)
    fit = quasi_poisson(x, y)
    assert fit["pct_per_decade"] == pytest.approx(35, abs=12)
    assert fit["pct_per_decade_lo"] < fit["pct_per_decade"] < fit["pct_per_decade_hi"]
    assert fit["p"] < 0.001


def test_quasi_poisson_gives_up_on_all_zero_series():
    assert quasi_poisson(np.arange(30.0), np.zeros(30)) is None


def test_rolling_mean_is_centred():
    out = rolling_mean(np.arange(2000, 2007), np.array([0, 0, 5, 0, 0, 0, 0], dtype=float))
    assert dict(map(tuple, out))[2002] == pytest.approx(1.0)


def test_summarize_skips_missing_years():
    years = list(range(1990, 2020))
    values = [None if y % 7 == 0 else 1.0 for y in years]
    s = summarize(years, values, "count")
    assert s["n"] == len([v for v in values if v is not None])


def test_short_series_gets_no_trend():
    s = summarize(list(range(2015, 2023)), [1, 2, 3, 4, 5, 6, 7, 8], "count")
    assert s["enough_data"] is False


def _params(y0=1990, y1=2020):
    return Params(y0, y1, (1961, 1990), 95, 3)


def test_headline_does_not_claim_a_trend_without_evidence():
    rng = np.random.default_rng(11)
    years = list(range(1990, 2021))
    values = rng.poisson(4, len(years)).astype(float).tolist()
    text = headline(HAZARDS["heatwave"], _params(), summarize(years, values, "count"))
    assert "No clear trend" in text or "weak" in text


def test_headline_uses_absolute_numbers_from_a_low_base():
    years = list(range(1990, 2021))
    values = [0] * 15 + [0, 1, 0, 1, 1, 2, 1, 2, 3, 2, 3, 4, 3, 5, 4, 6]
    t = summarize(years, values, "count")
    text = headline(HAZARDS["heatwave"], _params(), t)
    assert t["mk"]["p"] < 0.05
    assert "%" not in text.split("(Mann")[0]  # no "+900%" from almost nothing
    assert "too low for a percentage" in text

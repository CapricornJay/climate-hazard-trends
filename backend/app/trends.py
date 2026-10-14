"""Trend statistics for yearly hazard indicators.

What we use and why:

* Mann-Kendall for "is there a monotonic trend at all". Non-parametric, so
  it doesn't care that heatwave counts are skewed and full of zeros. The
  variance gets the Hamed & Rao (1998) correction when the detrended series
  is autocorrelated (a run of hot years in a row would otherwise look more
  significant than it is).
* Sen's slope for the size of a trend in continuous indicators.
* Quasi-Poisson regression for counts (heatwave days, events). Sen's slope
  is often exactly 0 for counts with many zero years, which is useless, and
  OLS can predict negative counts. Poisson gives a rate ratio, i.e. "x% more
  per decade", which is the honest way to state a change in a count.
* OLS is reported too, because everyone recognises it.
"""

import math

import numpy as np
from scipy import stats

MIN_YEARS = 10


def _norm_sf2(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))  # two-sided p-value


def _acf(x: np.ndarray, nlags: int) -> np.ndarray:
    y = x - x.mean()
    n = len(x)
    acov = np.correlate(y, y, "full")[n - 1:] / n
    return acov[: nlags + 1] / acov[0] if acov[0] > 0 else np.zeros(nlags + 1)


def sens_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
    i, j = np.triu_indices(len(x), k=1)
    dx = x[j] - x[i]
    slopes = (y[j] - y[i])[dx != 0] / dx[dx != 0]
    slope = float(np.median(slopes))
    intercept = float(np.median(y) - slope * np.median(x))
    return slope, intercept, np.sort(slopes)


def mann_kendall(x: np.ndarray, y: np.ndarray, alpha: float = 0.05) -> dict:
    n = len(y)
    i, j = np.triu_indices(n, k=1)
    s = float(np.sign(y[j] - y[i]).sum())

    _, counts = np.unique(y, return_counts=True)
    t = counts[counts > 1]
    var_s = (n * (n - 1) * (2 * n + 5) - np.sum(t * (t - 1) * (2 * t + 5))) / 18.0

    # Hamed & Rao (1998): inflate the variance by the effective sample size
    # computed from significant autocorrelations of the ranks of the
    # detrended series.
    slope, _, _ = sens_slope(np.arange(1, n + 1, dtype=float), y)
    detrended = y - slope * np.arange(1, n + 1)
    ranks = stats.rankdata(detrended)
    acf = _acf(ranks, n - 1)
    bound = stats.norm.ppf(1 - alpha / 2) / math.sqrt(n)
    lags = np.arange(1, n)
    sig = np.abs(acf[1:]) > bound
    correction = 1 + 2 / (n * (n - 1) * (n - 2)) * np.sum(((n - lags) * (n - lags - 1) * (n - lags - 2) * acf[1:])[sig])
    # Only ever widen the variance. Negative autocorrelation at some lag can
    # push the factor below 1 and make the test *more* confident than the
    # plain one, which isn't a trade we want to make with 30 data points.
    var_corr = var_s * max(correction, 1.0)

    def z_of(v):
        if v <= 0 or s == 0:
            return 0.0
        return (s - math.copysign(1, s)) / math.sqrt(v)

    z_raw, z = z_of(var_s), z_of(var_corr)
    return {
        "s": s,
        "tau": s / (0.5 * n * (n - 1)),
        "z": z,
        "p": _norm_sf2(z),
        "p_uncorrected": _norm_sf2(z_raw),
        "autocorrelation_correction": round(float(correction), 3),
        "var_s": var_corr,
    }


def sen_with_ci(x: np.ndarray, y: np.ndarray, var_s: float, alpha: float = 0.05) -> dict:
    """Sen's slope with the Gilbert (1987) rank-based confidence interval."""
    slope, intercept, slopes = sens_slope(x, y)
    m = len(slopes)
    c = stats.norm.ppf(1 - alpha / 2) * math.sqrt(max(var_s, 0))
    lo_i, hi_i = int(math.floor((m - c) / 2)), int(math.ceil((m + c) / 2))
    lo = slopes[max(lo_i - 1, 0)] if m else float("nan")
    hi = slopes[min(hi_i, m - 1)] if m else float("nan")
    return {"slope": slope, "intercept": intercept, "lo": float(lo), "hi": float(hi)}


def ols(x: np.ndarray, y: np.ndarray) -> dict:
    res = stats.linregress(x, y)
    tcrit = stats.t.ppf(0.975, len(x) - 2)
    return {
        "slope": float(res.slope),
        "intercept": float(res.intercept),
        "r2": float(res.rvalue ** 2),
        "p": float(res.pvalue),
        "lo": float(res.slope - tcrit * res.stderr),
        "hi": float(res.slope + tcrit * res.stderr),
    }


def quasi_poisson(x: np.ndarray, y: np.ndarray) -> dict | None:
    """log(mu) = a + b*(year - mean), fitted by IRLS. Overdispersion (heatwave
    days come in clumps, so the variance is much bigger than the mean) is
    handled by scaling the standard error with the Pearson dispersion."""
    if y.sum() <= 0 or np.count_nonzero(y) < 3:
        return None
    xc = x - x.mean()
    X = np.column_stack([np.ones_like(xc), xc])
    beta = np.array([math.log(max(y.mean(), 1e-3)), 0.0])
    try:
        for _ in range(100):
            mu = np.exp(X @ beta)
            z = X @ beta + (y - mu) / mu
            XtW = X.T * mu
            new = np.linalg.solve(XtW @ X, XtW @ z)
            done = np.max(np.abs(new - beta)) < 1e-10
            beta = new
            if done:
                break
        mu = np.exp(X @ beta)
        n = len(y)
        phi = max(1.0, float(np.sum((y - mu) ** 2 / mu) / (n - 2)))
        cov = np.linalg.inv((X.T * mu) @ X) * phi
    except (np.linalg.LinAlgError, FloatingPointError, OverflowError):
        return None
    b = float(beta[1])
    if not np.all(np.isfinite(beta)) or abs(b) > 1.0 or not np.isfinite(cov[1, 1]):
        return None  # diverged; a few events at the very end of the window can do this
    se = math.sqrt(cov[1, 1])
    tcrit = stats.t.ppf(0.975, n - 2)
    p = float(2 * stats.t.sf(abs(b / se), n - 2)) if se > 0 else 1.0
    span = float(x.max() - x.min())
    return {
        "pct_per_decade": 100 * (math.exp(10 * b) - 1),
        "pct_per_decade_lo": 100 * (math.exp(10 * (b - tcrit * se)) - 1),
        "pct_per_decade_hi": 100 * (math.exp(10 * (b + tcrit * se)) - 1),
        "fit_start": float(mu[np.argmin(x)]),
        "fit_end": float(mu[np.argmax(x)]),
        "pct_over_period": 100 * (math.exp(b * span) - 1),
        "dispersion": phi,
        "p": p,
        "coef": [float(beta[0]), b, float(x.mean())],
    }


def rolling_mean(x: np.ndarray, y: np.ndarray, window: int = 5) -> list[list[float]]:
    """Centred rolling mean; edges use whatever part of the window exists,
    but need at least 3 values."""
    half = window // 2
    out = []
    for k in range(len(y)):
        seg = y[max(0, k - half): k + half + 1]
        if seg.size >= 3:
            out.append([int(x[k]), round(float(seg.mean()), 3)])
    return out


def compare_decades(x: np.ndarray, y: np.ndarray) -> dict | None:
    """First vs last decade of the window (or thirds of it if shorter than 30
    years). Plain means, easy to check by hand."""
    n = len(x)
    if n < MIN_YEARS:
        return None
    k = 10 if n >= 30 else max(3, n // 3)
    return {
        "first": {"from": int(x[0]), "to": int(x[k - 1]), "mean": float(y[:k].mean())},
        "last": {"from": int(x[-k]), "to": int(x[-1]), "mean": float(y[-k:].mean())},
    }


def summarize(years, values, kind: str) -> dict:
    """Everything the UI needs for one indicator. Missing values (incomplete
    years, or no events for intensity metrics) are dropped first."""
    x = np.asarray(years, dtype=float)
    y = np.array([np.nan if v is None else v for v in values], dtype=float)
    ok = ~np.isnan(y)
    x, y = x[ok], y[ok]
    base = {"n": int(len(x)), "kind": kind}
    if len(x) < MIN_YEARS:
        return {**base, "enough_data": False}

    mk = mann_kendall(x, y)
    sen = sen_with_ci(x, y, mk["var_s"])
    out = {
        **base,
        "enough_data": True,
        "mk": {k: v for k, v in mk.items() if k != "var_s"},
        "sen": sen,
        "ols": ols(x, y),
        "rolling": rolling_mean(x, y),
        "decades": compare_decades(x, y),
        "zero_years": int(np.sum(y == 0)),
        "mean": float(y.mean()),
    }
    if kind == "count":
        qp = quasi_poisson(x, y)
        out["poisson"] = qp
        if qp:
            a, b, xm = qp["coef"]
            out["line"] = [[int(v), round(math.exp(a + b * (v - xm)), 3)] for v in x]
    if "line" not in out:
        out["line"] = [[int(v), round(sen["intercept"] + sen["slope"] * v, 3)] for v in x]
        out["line_model"] = "sen"
    else:
        out["line_model"] = "poisson"
    return out

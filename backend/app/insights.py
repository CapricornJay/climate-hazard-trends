"""Plain-language summary of an analysis.

Rules this follows, because it's easy to oversell climate statistics:
- Only say "increased"/"decreased" when Mann-Kendall p < 0.05. Between
  0.05 and 0.10 say the evidence is weak. Above that, say there's no clear
  trend, even if the decade means differ.
- Percentages come from the fitted trend, not from two noisy endpoints.
  If the fitted starting level is tiny, give absolute numbers instead,
  because "+400%" from 0.3 days a year means very little.
- Always say what the counts are relative to (threshold + baseline), and
  list the caveats that apply to *this* analysis rather than a generic
  disclaimer.
"""

from datetime import date

from .hazards import Hazard, Params

NOUNS = {
    "heatwave": ("heatwave days", "heatwaves", "heatwave"),
    "heavy_rain": ("heavy-rain days", "heavy-rain events", "heavy-rain event"),
}


def _p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}" if p < 0.01 else f"p = {p:.2f}"


def _pct(v: float) -> str:
    return f"{v:+.0f}%"


def _fmt_date(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')} {x.year}"


def headline(hz: Hazard, p: Params, trend: dict) -> str:
    days_noun = NOUNS[hz.id][0]
    y0, y1 = p.start_year, p.end_year
    if not trend.get("enough_data"):
        return (f"Only {trend['n']} usable years between {y0} and {y1}, which is too few to say "
                f"anything reliable about a trend in {days_noun}.")

    mk_p = trend["mk"]["p"]
    qp = trend.get("poisson")
    up = trend["mk"]["s"] > 0
    word = "increased" if up else "decreased"

    if mk_p < 0.05:
        if qp and qp["fit_start"] >= 1.0:
            text = (f"{days_noun.capitalize()} have {word} by about {abs(qp['pct_over_period']):.0f}% since {y0}: "
                    f"the fitted trend goes from {qp['fit_start']:.1f} to {qp['fit_end']:.1f} days a year by {y1}.")
        elif qp:
            text = (f"{days_noun.capitalize()} have {word}, from about {qp['fit_start']:.1f} to "
                    f"{qp['fit_end']:.1f} days a year between {y0} and {y1} (fitted trend). The starting "
                    f"level is too low for a percentage change to mean much.")
        else:
            sen = trend["sen"]["slope"] * 10
            text = f"{days_noun.capitalize()} have {word} by roughly {abs(sen):.1f} days per decade since {y0}."
        return text + f" That trend is statistically significant (Mann-Kendall {_p(mk_p)})."

    if mk_p < 0.10:
        change = f" ({_pct(qp['pct_over_period'])} over {y0}-{y1} on the fitted trend)" if qp and qp["fit_start"] >= 1 else ""
        return (f"There are signs that {days_noun} have {word}{change}, but the evidence is weak "
                f"(Mann-Kendall {_p(mk_p)}) and it could still be chance.")

    return (f"No clear trend in {days_noun} between {y0} and {y1} (Mann-Kendall {_p(mk_p)}). "
            f"Year-to-year swings are bigger than any steady change.")


def build(hz: Hazard, p: Params, result: dict, trends: dict, region: dict, quality: dict, source: dict) -> list[dict]:
    days_noun, events_noun, event_noun = NOUNS[hz.id]
    primary = trends[hz.primary]
    out = [{"kind": "headline", "text": headline(hz, p, primary)}]
    find = lambda t: out.append({"kind": "finding", "text": t})  # noqa: E731
    caveat = lambda t: out.append({"kind": "caveat", "text": t})  # noqa: E731

    # --- findings -------------------------------------------------------
    dec = primary.get("decades")
    if dec:
        a, b = dec["first"]["mean"], dec["last"]["mean"]
        rel = ""
        if a >= 0.5 and b / a >= 1.5:
            rel = f", {b / a:.1f} times as many"
        elif a >= 0.5 and b / a <= 0.67:
            rel = f", {100 * (1 - b / a):.0f}% fewer"
        find(f"{dec['first']['from']}-{dec['first']['to']} averaged {a:.1f} {days_noun} a year; "
             f"{dec['last']['from']}-{dec['last']['to']} averaged {b:.1f}{rel}.")

    events = result["events"]
    annual = [r for r in result["annual"] if r["complete"]]
    with_events = sum(1 for r in annual if r["events"] > 0)
    if events:
        find(f"{len(events)} {events_noun} in total; {with_events} of {len(annual)} complete years had at least one.")
        longest = max(events, key=lambda e: (e["duration"], e["peak"] or 0))
        if hz.id == "heatwave":
            find(f"Longest {event_noun}: {longest['duration']} days, {_fmt_date(longest['start'])} to "
                 f"{_fmt_date(longest['end'])}, peaking at {longest['peak']:.1f} °C.")
            hottest = max(events, key=lambda e: e["peak"] or -999)
            if hottest is not longest:
                find(f"Hottest {event_noun} day: {hottest['peak']:.1f} °C on {_fmt_date(hottest['peak_date'])}.")
        else:
            wettest = max(events, key=lambda e: e["peak"] or 0)
            find(f"Wettest day: {wettest['peak']:.0f} mm on {_fmt_date(wettest['peak_date'])}.")
    else:
        find(f"No {events_noun} met the definition in {p.start_year}-{p.end_year}.")

    if hz.id == "heatwave":
        t = trends.get("mean_excess", {})
        if t.get("enough_data") and t["mk"]["p"] < 0.05:
            slope = t["sen"]["slope"] * 10
            find(f"Heatwaves have also become {'more' if slope > 0 else 'less'} intense: the average exceedance "
                 f"of the threshold changed by {slope:+.2f} °C per decade (Sen's slope, {_p(t['mk']['p'])}).")
    else:
        t = trends.get("rx1day", {})
        if t.get("enough_data") and t["mk"]["p"] < 0.05:
            slope = t["sen"]["slope"] * 10
            find(f"The wettest day of the year has changed by {slope:+.1f} mm per decade (Sen's slope, {_p(t['mk']['p'])}).")

    # --- caveats --------------------------------------------------------
    if primary.get("enough_data"):
        n = primary["n"]
        if n < 30:
            caveat(f"Only {n} years of data. Climate trends are normally judged over 30 years or more, "
                   f"so treat this as indicative.")
        zero = primary.get("zero_years", 0)
        if zero >= 0.4 * n:
            caveat(f"{zero} of {n} years had no {events_noun} at all, so a handful of extreme years "
                   f"carry a lot of weight in the trend.")
        corr = primary["mk"].get("autocorrelation_correction", 1)
        if corr > 1.3:
            caveat(f"Consecutive years are correlated, so the significance test was adjusted (Hamed & Rao). "
                   f"Without the adjustment it would read {_p(primary['mk']['p_uncorrected'])}.")

    b0, b1 = p.baseline
    overlap = max(0, min(b1, p.end_year) - max(b0, p.start_year) + 1)
    if 0 < overlap < (p.end_year - p.start_year + 1):
        caveat(f"The threshold comes from {b0}-{b1}, which overlaps part of the analysis window. Years inside "
               f"a baseline tend to show slightly fewer exceedances than years outside it for purely "
               f"statistical reasons (Zhang et al., 2005), which can exaggerate a trend a little.")
    elif overlap == 0 and p.start_year > b1:
        caveat(f"Everything is measured against the {b0}-{b1} climate, so even the first years of the window "
               f"can show more {events_noun} than the {100 - p.percentile:g}% you'd expect by chance.")

    incomplete = quality.get("incomplete_years") or []
    in_window = [y for y in incomplete if p.start_year <= y <= p.end_year]
    if in_window:
        yrs = ", ".join(map(str, in_window[:8])) + ("..." if len(in_window) > 8 else "")
        caveat(f"{len(in_window)} year(s) had more than 15 missing days and were left out of the trends: {yrs}.")

    if source["id"] == "era5":
        caveat("ERA5 is a ~25 km reanalysis. It is consistent through time, which is what matters for "
               "trends, but it smooths out local effects such as urban heat islands. Absolute values can "
               "differ from a local thermometer by a few degrees; percentile thresholds make the counts "
               "much less sensitive to that.")
        if hz.id == "heavy_rain":
            caveat("Reanalysis rainfall is a model product and tends to underestimate short, intense "
                   "convective downpours. The direction of the trend is more trustworthy than the amounts.")
    else:
        st = source.get("station") or {}
        caveat(f"Station data from {st.get('name', 'a GHCN-Daily station')} "
               f"({st.get('distance_km', '?')} km from the selected point). NOAA quality-controls GHCN-Daily "
               f"but does not homogenise it, so station moves or instrument changes can masquerade as trends.")

    if region.get("kind") != "point" and len(region.get("points", [])) > 1:
        caveat(f"This is an average over {len(region['points'])} grid points. A {event_noun} here means the "
               f"region as a whole was unusually {'hot' if hz.id == 'heatwave' else 'wet'}; local events in "
               f"one corner may not register.")
    return out

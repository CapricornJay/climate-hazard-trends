"""CSV, PNG and PDF versions of a stored analysis. All of these are rebuilt
from the analysis document, so they always match what the UI shows."""

import csv
import io
import os
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
from fontTools.ttLib import TTFont  # noqa: E402  (ships with matplotlib and fpdf2)
from fpdf import FPDF  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

FONT_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
_GLYPHS = set(TTFont(FONT_REGULAR)["cmap"].getBestCmap())

HEAT = "#c2410c"
RAIN = "#1d4ed8"


def _metric(doc: dict, key: str | None) -> dict:
    metrics = doc["hazard"]["metrics"]
    key = key or doc["hazard"]["primary_metric"]
    return next((m for m in metrics if m["key"] == key), metrics[0])


def annual_csv(doc: dict) -> str:
    keys = [m["key"] for m in doc["hazard"]["metrics"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["year", "complete", "coverage", *keys])
    for r in doc["annual"]:
        w.writerow([r["year"], int(r["complete"]), r["coverage"], *("" if r[k] is None else r[k] for k in keys)])
    return buf.getvalue()


def events_csv(doc: dict) -> str:
    events = doc["events"]
    buf = io.StringIO()
    if not events:
        buf.write("start,end,duration\n")
        return buf.getvalue()
    fields = list(events[0].keys())
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(events)
    return buf.getvalue()


def _trend_label(t: dict) -> str:
    if t.get("line_model") == "poisson":
        return "Trend (quasi-Poisson fit)"
    return "Trend (Sen's slope)"


def trend_png(doc: dict, metric_key: str | None = None, dpi: int = 150) -> bytes:
    metric = _metric(doc, metric_key)
    t = doc["trends"][metric["key"]]
    rows = doc["annual"]
    color = HEAT if doc["hazard"]["id"] == "heatwave" else RAIN

    fig = Figure(figsize=(9, 4.4), dpi=dpi)
    ax = fig.add_subplot()
    ax.bar(
        [r["year"] for r in rows],
        [r[metric["key"]] or 0 for r in rows],
        color=[color if r["complete"] else "#d4d4d8" for r in rows],
        width=0.8,
        label=metric["label"],
    )
    if t.get("rolling"):
        xs, ys = zip(*t["rolling"])
        ax.plot(xs, ys, color="#111827", lw=2, label="5-year mean")
    if t.get("line"):
        xs, ys = zip(*t["line"])
        ax.plot(xs, ys, color="#0f766e", lw=1.6, ls="--", label=_trend_label(t))
    ax.set_ylabel(metric["unit"])
    ax.set_title(f"{metric['label']} - {doc['region']['label']}", loc="left", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e5e7eb", lw=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", fontsize=8)

    note = doc["definition"]
    if t.get("enough_data"):
        note += f"  Mann-Kendall p = {t['mk']['p']:.3f}."
    fig.text(0.01, 0.015, note, fontsize=6.5, color="#4b5563", wrap=True)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def _safe(text: str) -> str:
    return "".join(ch if ord(ch) in _GLYPHS or ch == "\n" else "?" for ch in str(text))


class _Report(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("dejavu", "", 7)
        self.set_text_color(120)
        self.cell(0, 5, _safe(f"climate-hazard-trends  ·  page {self.page_no()}"), align="R")


def report_pdf(doc: dict) -> bytes:
    hz = doc["hazard"]
    req = doc["request"]
    pdf = _Report(format="A4")
    pdf.add_font("dejavu", "", FONT_REGULAR)
    pdf.add_font("dejavu", "B", FONT_BOLD)
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    width = pdf.epw

    def para(text, size=9.5, bold=False, color=(30, 30, 30), gap=1.5):
        pdf.set_font("dejavu", "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(width, size * 0.5, _safe(text))
        pdf.ln(gap)

    def heading(text):
        pdf.ln(2)
        para(text, size=11, bold=True, gap=0.5)

    para(f"{hz['label']} trends: {doc['region']['label']}", size=16, bold=True, gap=1)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    para(f"{req['start_year']}-{req['end_year']}  ·  {doc['source']['label']}  ·  generated {generated}",
         size=8.5, color=(100, 100, 100), gap=3)

    insights = doc["insights"]
    para(next(i["text"] for i in insights if i["kind"] == "headline"), size=11, bold=True, gap=3)

    png = trend_png(doc)
    pdf.image(io.BytesIO(png), w=width)
    pdf.ln(2)

    heading("Findings")
    for item in insights:
        if item["kind"] == "finding":
            para(f"•  {item['text']}")

    heading("Key numbers")
    t = doc["trends"][hz["primary_metric"]]
    rows = [("Definition", doc["definition"])]
    if t.get("enough_data"):
        adjusted = " (Hamed-Rao adjusted for autocorrelation)" if t["mk"]["autocorrelation_correction"] > 1 else ""
        rows += [
            ("Mann-Kendall", f"tau = {t['mk']['tau']:.2f}, p = {t['mk']['p']:.3f}{adjusted}"),
            ("Sen's slope", f"{t['sen']['slope'] * 10:+.2f} per decade (95% CI {t['sen']['lo'] * 10:+.2f} to {t['sen']['hi'] * 10:+.2f})"),
            ("OLS", f"{t['ols']['slope'] * 10:+.2f} per decade, R² = {t['ols']['r2']:.2f}, p = {t['ols']['p']:.3f}"),
        ]
        qp = t.get("poisson")
        if qp:
            rows.append(("Quasi-Poisson", f"{qp['pct_per_decade']:+.0f}% per decade (95% CI {qp['pct_per_decade_lo']:+.0f}% to {qp['pct_per_decade_hi']:+.0f}%)"))
    pdf.set_font("dejavu", "", 8.5)
    pdf.set_text_color(30, 30, 30)
    with pdf.table(col_widths=(28, 152), text_align="LEFT", line_height=4.6, first_row_as_headings=False,
                   borders_layout="HORIZONTAL_LINES") as table:
        for k, v in rows:
            r = table.row()
            r.cell(_safe(k))
            r.cell(_safe(v))

    events = doc["events"]
    if events:
        heading(f"Largest events (of {doc['events_total']})")
        if hz["id"] == "heatwave":
            cols = [("start", "Start"), ("end", "End"), ("duration", "Days"), ("peak", "Peak °C"), ("mean_excess", "Mean excess °C")]
            top = sorted(events, key=lambda e: (e["cum_excess"] or 0), reverse=True)[:15]
        else:
            cols = [("start", "Start"), ("end", "End"), ("duration", "Days"), ("peak", "Max mm/day"), ("total", "Total mm")]
            top = sorted(events, key=lambda e: (e["total"] or 0), reverse=True)[:15]
        pdf.set_font("dejavu", "", 8.5)
        with pdf.table(text_align="LEFT", line_height=4.6, borders_layout="HORIZONTAL_LINES") as table:
            head = table.row()
            for _, label in cols:
                head.cell(_safe(label))
            for e in top:
                r = table.row()
                for key, _ in cols:
                    r.cell(_safe("" if e[key] is None else e[key]))

    heading("Caveats")
    for item in insights:
        if item["kind"] == "caveat":
            para(f"•  {item['text']}", size=8.5)

    q = doc["quality"]
    heading("Data handling")
    para(
        f"{q['points']} series, {q['expected_days']:,} days each. Missing after QC: {q['missing']:,} days; "
        f"short gaps filled: {q['filled']}; out-of-range values removed: {q['out_of_range']}; spikes removed: "
        f"{q['spikes']}; duplicate dates dropped: {q['duplicates']}; values rejected by source QC flags: "
        f"{q['source_flagged']}. Years with more than 15 missing days are excluded from trends.",
        size=8.5,
    )
    para(f"Source: {doc['source']['attribution']}.", size=8, color=(90, 90, 90))
    return bytes(pdf.output())

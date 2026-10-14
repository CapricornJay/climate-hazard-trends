import type { ChartData, ChartOptions } from "chart.js";
import { Chart } from "react-chartjs-2";
import { num, pValue, signed } from "../format";
import type { Analysis, Trend } from "../types";
import { COLORS } from "./chartSetup";

interface Props {
  analysis: Analysis;
  metric: string;
  onMetric: (key: string) => void;
  selectedYear: number | null;
  onYear: (y: number) => void;
}

function TrendStats({ t, unit }: { t: Trend; unit: string }) {
  if (!t.enough_data || !t.mk || !t.sen || !t.ols) {
    return <p className="hint">Only {t.n} usable years, not enough for trend statistics.</p>;
  }
  const sig = t.mk.p < 0.05 ? "significant at 5%" : t.mk.p < 0.1 ? "weak evidence" : "not significant";
  return (
    <dl className="trend-stats">
      <div>
        <dt>Mann–Kendall</dt>
        <dd>
          τ = {num(t.mk.tau, 2)}, {pValue(t.mk.p)} <span className={`tag ${t.mk.p < 0.05 ? "tag-yes" : "tag-no"}`}>{sig}</span>
          {t.mk.autocorrelation_correction > 1.001 && <small> (autocorrelation-adjusted; unadjusted {pValue(t.mk.p_uncorrected)})</small>}
        </dd>
      </div>
      <div>
        <dt>Sen's slope</dt>
        <dd>{signed(t.sen.slope * 10, 2)} {unit} per decade <small>(95% CI {signed(t.sen.lo * 10, 2)} to {signed(t.sen.hi * 10, 2)})</small></dd>
      </div>
      {t.poisson && (
        <div>
          <dt>Quasi-Poisson</dt>
          <dd>
            {signed(t.poisson.pct_per_decade, 0)}% per decade <small>(95% CI {signed(t.poisson.pct_per_decade_lo, 0)}% to {signed(t.poisson.pct_per_decade_hi, 0)}%, {pValue(t.poisson.p)})</small>
          </dd>
        </div>
      )}
      <div>
        <dt>Linear regression</dt>
        <dd>{signed(t.ols.slope * 10, 2)} per decade, R² = {num(t.ols.r2, 2)}, {pValue(t.ols.p)}</dd>
      </div>
    </dl>
  );
}

export default function AnnualChart({ analysis, metric, onMetric, selectedYear, onYear }: Props) {
  const hz = analysis.hazard;
  const m = hz.metrics.find((x) => x.key === metric) ?? hz.metrics[0];
  const t = analysis.trends[m.key];
  const palette = COLORS[hz.id];
  const rows = analysis.annual;
  const years = rows.map((r) => r.year);
  const lookup = (pairs?: [number, number][]) => {
    const map = new Map(pairs ?? []);
    return years.map((y) => map.get(y) ?? null);
  };

  const data: ChartData<"bar" | "line", (number | null)[], number> = {
    labels: years,
    datasets: [
      {
        type: "bar",
        label: m.label,
        data: rows.map((r) => (r[m.key] as number | null) ?? null),
        backgroundColor: rows.map((r) => (!r.complete ? COLORS.muted : r.year === selectedYear ? palette.strong : palette.main)),
        borderRadius: 2,
        order: 3,
      },
      {
        type: "line",
        label: "5-year mean",
        data: lookup(t.rolling),
        borderColor: COLORS.rolling,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        spanGaps: true,
        order: 1,
      },
      {
        type: "line",
        label: t.line_model === "poisson" ? "Trend (quasi-Poisson fit)" : "Trend (Sen's slope)",
        data: lookup(t.line),
        borderColor: COLORS.trend,
        borderDash: [6, 4],
        borderWidth: 2,
        pointRadius: 0,
        spanGaps: true,
        order: 2,
      },
    ],
  };

  const options: ChartOptions<"bar" | "line"> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    onClick: (_e, elements) => {
      if (elements.length) onYear(years[elements[0].index]);
    },
    plugins: {
      legend: { position: "bottom", labels: { boxWidth: 14, boxHeight: 8 } },
      tooltip: {
        callbacks: {
          afterTitle: (items) => {
            const r = rows[items[0].dataIndex];
            return r.complete ? "" : `Incomplete year (${Math.round(r.coverage * 100)}% of days), not used in trends`;
          },
        },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
      y: { beginAtZero: m.kind === "count" || m.key !== "peak", title: { display: true, text: m.unit } },
    },
  };

  return (
    <section className="card">
      <header className="card-head">
        <h2>{m.label} per year</h2>
        <div className="segmented" role="tablist" aria-label="Indicator">
          {hz.metrics.map((x) => (
            <button key={x.key} type="button" className={x.key === m.key ? "active" : ""} onClick={() => onMetric(x.key)} title={x.description}>
              {x.label}
            </button>
          ))}
        </div>
      </header>
      <p className="hint">{m.description}. Click a bar to see that year day by day. Grey bars are years with too many missing days.</p>
      <div className="chart-box">
        <Chart type="bar" data={data} options={options} />
      </div>
      <TrendStats t={t} unit={m.unit.replace("/yr", "")} />
    </section>
  );
}

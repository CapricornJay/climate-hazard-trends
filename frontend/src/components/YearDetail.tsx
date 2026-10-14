import type { ChartData, ChartOptions } from "chart.js";
import type { AnnotationOptions } from "chartjs-plugin-annotation";
import { Chart } from "react-chartjs-2";
import { dayMonth, dayNumber, isoFromDayNumber, num, shortDate } from "../format";
import type { Analysis } from "../types";
import { COLORS } from "./chartSetup";
import { thresholdFor } from "./HeatCalendar";

interface Props {
  analysis: Analysis;
  year: number;
  onYear: (y: number) => void;
}

export default function YearDetail({ analysis, year, onYear }: Props) {
  const isHeat = analysis.hazard.id === "heatwave";
  const palette = COLORS[analysis.hazard.id];
  const unit = analysis.hazard.unit;
  const start = dayNumber(analysis.daily.start);
  const first = dayNumber(`${year}-01-01`);
  const last = dayNumber(`${year}-12-31`);
  const days = Array.from({ length: last - first + 1 }, (_, i) => isoFromDayNumber(first + i));
  const values = days.map((_, i) => analysis.daily.values[first - start + i] ?? null);
  const thresholds = days.map((d) => thresholdFor(analysis, d));
  const events = analysis.events.filter((e) => e.start.slice(0, 4) === String(year) || e.end.slice(0, 4) === String(year));
  const row = analysis.annual.find((r) => r.year === year);

  const boxes: Record<string, AnnotationOptions> = {};
  events.forEach((e, i) => {
    boxes[`ev${i}`] = {
      type: "box",
      xMin: Math.max(0, dayNumber(e.start) - first) - 0.5,
      xMax: Math.min(days.length - 1, dayNumber(e.end) - first) + 0.5,
      backgroundColor: palette.soft,
      borderWidth: 0,
    };
  });

  const data: ChartData<"line" | "bar", (number | null)[], string> = {
    labels: days.map(dayMonth),
    datasets: [
      isHeat
        ? { type: "line", label: "Daily maximum", data: values, borderColor: palette.main, borderWidth: 1.4, pointRadius: 0, spanGaps: false, order: 2 }
        : {
            type: "bar",
            label: "Daily precipitation",
            data: values,
            backgroundColor: values.map((v, i) => (v !== null && thresholds[i] !== null && v > thresholds[i]! ? palette.main : "#93c5fd")),
            order: 2,
          },
      {
        type: "line",
        label: `Threshold (${analysis.threshold.percentile}th percentile)`,
        data: thresholds,
        borderColor: "#111827",
        borderDash: [4, 4],
        borderWidth: 1.2,
        pointRadius: 0,
        order: 1,
      },
    ],
  };

  const options: ChartOptions<"line" | "bar"> = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "bottom", labels: { boxWidth: 14, boxHeight: 8 } },
      annotation: { annotations: boxes },
      tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${num(ctx.parsed.y as number)} ${unit}` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 12, maxRotation: 0 } },
      y: { title: { display: true, text: unit }, beginAtZero: !isHeat },
    },
  };

  const y0 = analysis.request.start_year;
  const y1 = analysis.request.end_year;
  return (
    <section className="card">
      <header className="card-head">
        <h2>{year}, day by day</h2>
        <div className="row">
          <button type="button" disabled={year <= y0} onClick={() => onYear(year - 1)} aria-label="Previous year">←</button>
          <button type="button" disabled={year >= y1} onClick={() => onYear(year + 1)} aria-label="Next year">→</button>
        </div>
      </header>
      <p className="hint">
        {events.length === 0
          ? `No ${isHeat ? "heatwaves" : "heavy-rain events"} in ${year}.`
          : `${events.length} ${isHeat ? "heatwave" : "heavy-rain event"}${events.length > 1 ? "s" : ""} (shaded): ` +
            events.map((e) => (e.start === e.end ? shortDate(e.start) : `${dayMonth(e.start)}–${shortDate(e.end)}`)).join("; ")}
        {row && !row.complete && " This year has too many missing days to count towards the trend."}
      </p>
      <div className="chart-box short">
        <Chart type={isHeat ? "line" : "bar"} data={data} options={options} />
      </div>
    </section>
  );
}

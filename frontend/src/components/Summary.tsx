import { useState } from "react";
import { api } from "../api";
import { num, pValue, shortDate, signed } from "../format";
import type { Analysis } from "../types";

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

export default function Summary({ analysis }: { analysis: Analysis }) {
  const [copied, setCopied] = useState(false);
  const hz = analysis.hazard;
  const isHeat = hz.id === "heatwave";
  const t = analysis.trends[hz.primary_metric];
  const headline = analysis.insights.find((i) => i.kind === "headline")?.text;
  const findings = analysis.insights.filter((i) => i.kind === "finding");
  const caveats = analysis.insights.filter((i) => i.kind === "caveat");
  const th = analysis.threshold;
  const unit = hz.unit;
  const longest = analysis.events.reduce<Analysis["events"][number] | null>((a, e) => (!a || e.duration > a.duration ? e : a), null);
  const base = `/api/v1/analyses/${analysis.id}`;

  async function copyLink() {
    const url = `${window.location.origin}${window.location.pathname}?a=${analysis.id}`;
    await navigator.clipboard.writeText(url).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <section className="card summary">
      <p className="eyebrow">
        {hz.label} · {analysis.region.label} · {analysis.request.start_year}–{analysis.request.end_year} · {analysis.source.label}
      </p>
      {headline && <p className="headline">{headline}</p>}

      <div className="stats">
        <Stat
          label="Threshold"
          value={th.mode === "fixed" ? `${num(th.value)} ${unit}` : `${num(th.min)}–${num(th.max)} ${unit}`}
          note={`${th.percentile}th pct, ${th.baseline[0]}–${th.baseline[1]}${th.mode === "doy" ? ", by day of year" : ""}`}
        />
        {t.decades && (
          <Stat
            label={`${isHeat ? "Heatwave" : "Heavy-rain"} days / yr`}
            value={`${num(t.decades.first.mean)} → ${num(t.decades.last.mean)}`}
            note={`${t.decades.first.from}s avg vs ${t.decades.last.from}–${String(t.decades.last.to).slice(2)} avg`}
          />
        )}
        {t.enough_data && t.mk && (
          <Stat
            label="Trend"
            value={t.poisson ? `${signed(t.poisson.pct_per_decade, 0)}% / decade` : `${signed((t.sen?.slope ?? 0) * 10)} / decade`}
            note={`Mann–Kendall ${pValue(t.mk.p)}${t.mk.p < 0.05 ? " (significant)" : ""}`}
          />
        )}
        <Stat label="Events" value={String(analysis.events_total)} note={`${num(analysis.events_total / analysis.annual.length, 1)} per year on average`} />
        {longest && (
          <Stat
            label={isHeat ? "Longest heatwave" : "Longest wet spell"}
            value={`${longest.duration} day${longest.duration > 1 ? "s" : ""}`}
            note={`from ${shortDate(longest.start)}`}
          />
        )}
      </div>

      <h3>What the data shows</h3>
      <ul className="findings">{findings.map((f, i) => <li key={i}>{f.text}</li>)}</ul>

      <details className="caveats">
        <summary>Things to keep in mind ({caveats.length})</summary>
        <ul>{caveats.map((c, i) => <li key={i}>{c.text}</li>)}</ul>
      </details>

      <div className="exports">
        <a href={api.url(`${base}/report.pdf`)} download>PDF report</a>
        <a href={api.url(`${base}/annual.csv`)} download>Yearly CSV</a>
        <a href={api.url(`${base}/events.csv`)} download>Events CSV</a>
        <a href={api.url(`${base}/trend.png`)} target="_blank" rel="noreferrer">Chart PNG</a>
        <button type="button" onClick={copyLink}>{copied ? "Copied" : "Copy link"}</button>
      </div>
    </section>
  );
}

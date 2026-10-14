import type { Analysis } from "../types";

export default function QualityPanel({ analysis }: { analysis: Analysis }) {
  const q = analysis.quality;
  const st = analysis.region.station;
  const rows: [string, number | string][] = [
    ["Days checked per series", q.expected_days.toLocaleString("en")],
    ["Missing days (after QC)", q.missing],
    ["Short gaps filled (≤ 2 days)", q.filled],
    ["Physically impossible values removed", q.out_of_range],
    ["Isolated spikes removed", q.spikes],
    ["Duplicate dates dropped", q.duplicates],
    ["Values rejected by NOAA QC flags", q.source_flagged],
  ];
  const flags = Object.entries(q.source_flags ?? {});

  return (
    <section className="card">
      <header className="card-head">
        <h2>Data and method</h2>
      </header>
      <p><strong>Definition.</strong> {analysis.definition}</p>
      <p>
        <strong>Source.</strong> {analysis.source.detail}.
        {st && <> Station record runs {st.first.slice(0, 4)}–{st.last.slice(0, 4)}.</>}
        {analysis.region.kind !== "point" && analysis.source.id === "era5" && (
          <> Regional series is a cos(latitude)-weighted mean of {q.points} grid points spaced {analysis.region.spacing_deg}° apart.</>
        )}
      </p>
      <table className="qc">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {flags.length > 0 && (
        <p className="hint">NOAA flags in the downloaded record: {flags.map(([k, n]) => `${n} × ${k}`).join(", ")}.</p>
      )}
      <p className="hint">
        {q.incomplete_years.length
          ? `Excluded from trends (more than 15 missing days): ${q.incomplete_years.join(", ")}.`
          : "Every year had enough data to be used."}{" "}
        Genuine extremes are never clipped: range checks only remove values that can't be physically real, and the
        spike check only removes one-day jumps that both neighbours disagree with.
      </p>
      <p className="hint">{analysis.source.attribution}.</p>
    </section>
  );
}

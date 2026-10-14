import { useMemo, useState } from "react";
import { num, shortDate } from "../format";
import type { Analysis, HazardEvent } from "../types";

type Col = { key: keyof HazardEvent; label: string; digits?: number; date?: boolean };

const HEAT_COLS: Col[] = [
  { key: "start", label: "Start", date: true },
  { key: "duration", label: "Days", digits: 0 },
  { key: "peak", label: "Peak °C" },
  { key: "mean_excess", label: "Avg excess °C", digits: 2 },
  { key: "cum_excess", label: "Heat load °C·d" },
];
const RAIN_COLS: Col[] = [
  { key: "start", label: "Start", date: true },
  { key: "duration", label: "Days", digits: 0 },
  { key: "peak", label: "Max mm/day" },
  { key: "total", label: "Total mm" },
];

export default function EventsTable({ analysis, onYear }: { analysis: Analysis; onYear: (y: number) => void }) {
  const isHeat = analysis.hazard.id === "heatwave";
  const cols = isHeat ? HEAT_COLS : RAIN_COLS;
  const [sort, setSort] = useState<{ key: keyof HazardEvent; desc: boolean }>({ key: isHeat ? "cum_excess" : "total", desc: true });
  const [showAll, setShowAll] = useState(false);

  const rows = useMemo(() => {
    const out = [...analysis.events];
    out.sort((a, b) => {
      const x = a[sort.key] ?? 0;
      const y = b[sort.key] ?? 0;
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
    });
    return out;
  }, [analysis.events, sort]);

  const visible = showAll ? rows : rows.slice(0, 12);
  return (
    <section className="card">
      <header className="card-head">
        <h2>{isHeat ? "Heatwaves" : "Heavy-rain events"}</h2>
        <span className="hint">{analysis.events_total} in total</span>
      </header>
      {rows.length === 0 ? (
        <p className="hint">None in this period.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c.key} aria-sort={sort.key === c.key ? (sort.desc ? "descending" : "ascending") : "none"}>
                    <button type="button" onClick={() => setSort({ key: c.key, desc: sort.key === c.key ? !sort.desc : true })}>
                      {c.label}{sort.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((e) => (
                <tr key={e.start} onClick={() => onYear(Number(e.start.slice(0, 4)))}>
                  {cols.map((c) => (
                    <td key={c.key}>{c.date ? shortDate(e[c.key] as string) : num(e[c.key] as number, c.digits ?? 1)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 12 && (
        <button type="button" className="link" onClick={() => setShowAll(!showAll)}>
          {showAll ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </section>
  );
}

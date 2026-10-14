import { useEffect, useMemo, useRef, useState } from "react";
import { dayNumber, doy365, isoFromDayNumber, MONTHS, num, shortDate } from "../format";
import type { Analysis } from "../types";

interface Props {
  analysis: Analysis;
  selectedYear: number | null;
  onYear: (y: number) => void;
}

const MONTH_START = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const LEFT = 42;
const TOP = 18;

/** threshold for a given ISO date, fixed or day-of-year */
export function thresholdFor(a: Analysis, iso: string): number | null {
  if (a.threshold.mode === "fixed") return a.threshold.value ?? null;
  return a.threshold.by_doy?.[doy365(iso)] ?? null;
}

/** Index set of days that belong to an event, relative to daily.start */
export function eventDays(a: Analysis): Uint8Array {
  const start = dayNumber(a.daily.start);
  const marks = new Uint8Array(a.daily.values.length);
  for (const ev of a.events) {
    const s = dayNumber(ev.start) - start;
    const e = dayNumber(ev.end) - start;
    for (let i = Math.max(0, s); i <= Math.min(marks.length - 1, e); i++) marks[i] = 1;
  }
  return marks;
}

function mix(a: [number, number, number], b: [number, number, number], t: number) {
  const k = Math.max(0, Math.min(1, t));
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`;
}

export default function HeatCalendar({ analysis, selectedYear, onYear }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);
  const isHeat = analysis.hazard.id === "heatwave";
  const y0 = analysis.request.start_year;
  const y1 = analysis.request.end_year;
  const nYears = y1 - y0 + 1;
  const rowH = Math.max(4, Math.min(14, Math.floor(420 / nYears)));
  const height = TOP + nYears * rowH;

  const cells = useMemo(() => {
    const start = dayNumber(analysis.daily.start);
    const marks = eventDays(analysis);
    return analysis.daily.values.map((v, i) => {
      const iso = isoFromDayNumber(start + i);
      const year = Number(iso.slice(0, 4));
      const col = start + i - dayNumber(`${year}-01-01`);
      return { iso, year, col, v, thr: thresholdFor(analysis, iso), event: marks[i] === 1 };
    });
  }, [analysis]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(320, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const cellW = (width - LEFT) / 366;

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = "#6b7280";
    ctx.font = "10px system-ui, sans-serif";
    MONTH_START.forEach((d, i) => ctx.fillText(MONTHS[i], LEFT + d * cellW + 2, 11));
    const labelEvery = nYears > 60 ? 10 : nYears > 25 ? 5 : nYears > 12 ? 2 : 1;

    for (const cell of cells) {
      const row = cell.year - y0;
      const x = LEFT + cell.col * cellW;
      const y = TOP + row * rowH;
      let color: string;
      if (cell.v === null) color = "#e5e7eb";
      else if (isHeat) {
        const thr = cell.thr ?? 0;
        if (cell.event) color = mix([239, 68, 68], [127, 29, 29], (cell.v - thr) / 6);
        else if (cell.v > thr) color = "#fdba74";
        else color = mix([241, 245, 249], [254, 215, 170], 1 - (thr - cell.v) / 20);
      } else {
        const thr = cell.thr ?? 1;
        if (cell.event) color = mix([59, 130, 246], [30, 58, 138], (cell.v - thr) / thr);
        else if (cell.v >= 1) color = mix([224, 242, 254], [125, 211, 252], cell.v / thr);
        else color = "#f8fafc";
      }
      ctx.fillStyle = color;
      ctx.fillRect(x, y, Math.ceil(cellW), rowH - (rowH > 6 ? 1 : 0));
    }

    for (let y = y0; y <= y1; y++) {
      if ((y - y0) % labelEvery === 0 || y === selectedYear) {
        ctx.fillStyle = y === selectedYear ? "#111827" : "#6b7280";
        ctx.fillText(String(y), 4, TOP + (y - y0) * rowH + rowH - 2);
      }
    }
    if (selectedYear !== null && selectedYear >= y0 && selectedYear <= y1) {
      ctx.strokeStyle = "#111827";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(LEFT - 1, TOP + (selectedYear - y0) * rowH - 1, width - LEFT + 1, rowH + 1);
    }
  }, [cells, width, height, cellW, rowH, y0, y1, nYears, selectedYear, isHeat]);

  function locate(e: React.MouseEvent) {
    const rect = canvas.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const year = y0 + Math.floor((y - TOP) / rowH);
    const col = Math.floor((x - LEFT) / cellW);
    if (year < y0 || year > y1 || col < 0 || col > 365) return null;
    const idx = dayNumber(`${year}-01-01`) + col - dayNumber(analysis.daily.start);
    const cell = cells[idx];
    return cell && cell.year === year ? { cell, x, y } : null;
  }

  const unit = analysis.hazard.unit;
  return (
    <section className="card">
      <header className="card-head">
        <h2>Every day, every year</h2>
        <div className="legend">
          {isHeat ? (
            <>
              <span><i style={{ background: "#fed7aa" }} />warm</span>
              <span><i style={{ background: "#fdba74" }} />above threshold</span>
              <span><i style={{ background: "#b91c1c" }} />heatwave day</span>
            </>
          ) : (
            <>
              <span><i style={{ background: "#bae6fd" }} />wet day</span>
              <span><i style={{ background: "#1d4ed8" }} />heavy-rain day</span>
            </>
          )}
          <span><i style={{ background: "#e5e7eb" }} />no data</span>
        </div>
      </header>
      <div ref={wrap} className="calendar-wrap">
        <canvas
          ref={canvas}
          style={{ width, height }}
          onMouseMove={(e) => {
            const hit = locate(e);
            if (!hit) return setHover(null);
            const { cell } = hit;
            const parts = [shortDate(cell.iso), cell.v === null ? "no data" : `${num(cell.v)} ${unit}`];
            if (cell.thr !== null) parts.push(`threshold ${num(cell.thr)} ${unit}`);
            if (cell.event) parts.push(isHeat ? "heatwave" : "heavy rain");
            setHover({ x: hit.x, y: hit.y, text: parts.join(" · ") });
          }}
          onMouseLeave={() => setHover(null)}
          onClick={(e) => {
            const hit = locate(e);
            if (hit) onYear(hit.cell.year);
          }}
        />
        {hover && (
          <div className="calendar-tip" style={{ left: Math.min(hover.x + 12, width - 240), top: hover.y + 12 }}>{hover.text}</div>
        )}
      </div>
      <p className="hint">Rows are years, columns are days. Click a row to open that year below.</p>
    </section>
  );
}

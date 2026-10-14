import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import AnnualChart from "./components/AnnualChart";
import "./components/chartSetup";
import EventsTable from "./components/EventsTable";
import HeatCalendar from "./components/HeatCalendar";
import QualityPanel from "./components/QualityPanel";
import RegionMap from "./components/RegionMap";
import RegionPicker, { describe, type RegionTab } from "./components/RegionPicker";
import SettingsPanel from "./components/SettingsPanel";
import Summary from "./components/Summary";
import YearDetail from "./components/YearDetail";
import type { Analysis, Meta, RegionSelection, Settings } from "./types";

const REPO = "https://github.com/CapricornJay/climate-hazard-trends";

const EXAMPLES: { label: string; region: RegionSelection; hazard?: Settings["hazard"] }[] = [
  { label: "Phoenix", region: { kind: "point", lat: 33.448, lon: -112.074, label: "Phoenix, Arizona, United States" } },
  { label: "Paris", region: { kind: "point", lat: 48.853, lon: 2.349, label: "Paris, Île-de-France, France" } },
  { label: "Delhi", region: { kind: "point", lat: 28.652, lon: 77.231, label: "Delhi, India" } },
  { label: "Texas (state)", region: { kind: "admin", osm_type: "R", osm_id: 114690, label: "Texas, United States" } },
  { label: "Mumbai, heavy rain", region: { kind: "point", lat: 19.073, lon: 72.883, label: "Mumbai, Maharashtra, India" }, hazard: "heavy_rain" },
];

type Status =
  | { kind: "idle" }
  | { kind: "loading"; since: number }
  | { kind: "waiting"; message: string; until: number }
  | { kind: "error"; message: string };

function defaultSettings(meta: Meta): Settings {
  const hz = meta.hazards.find((h) => h.id === meta.default_hazard)!;
  return {
    hazard: hz.id,
    source: "era5",
    startYear: meta.defaults.start_year,
    endYear: meta.defaults.end_year,
    baseline: meta.defaults.baseline,
    percentile: hz.defaults.percentile,
    minDuration: hz.defaults.min_duration,
    thresholdMode: "fixed",
  };
}

function settingsFrom(a: Analysis): Settings {
  const r = a.request;
  return {
    hazard: r.hazard,
    source: r.source,
    startYear: r.start_year,
    endYear: r.end_year,
    baseline: [r.baseline_start, r.baseline_end],
    percentile: r.percentile,
    minDuration: r.min_duration,
    thresholdMode: r.threshold_mode,
  };
}

function busiestYear(a: Analysis): number {
  const key = a.hazard.primary_metric;
  let best = a.annual[a.annual.length - 1];
  for (const r of a.annual) if ((r[key] as number) > ((best[key] as number) ?? -1)) best = r;
  return best.year;
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState("");
  const [tab, setTab] = useState<RegionTab>("city");
  const [region, setRegion] = useState<RegionSelection | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [metric, setMetric] = useState("days");
  const [year, setYear] = useState<number | null>(null);
  const [, tick] = useState(0);
  const retryTimer = useRef<number | undefined>(undefined);

  const show = useCallback((a: Analysis) => {
    setAnalysis(a);
    setDirty(false);
    setMetric(a.hazard.primary_metric);
    setYear(busiestYear(a));
    setStatus({ kind: "idle" });
    const url = new URL(window.location.href);
    url.searchParams.set("a", a.id);
    window.history.replaceState(null, "", url);
  }, []);

  // Load metadata (retrying a few times: the free-tier API may be asleep), then a shared analysis if the URL has one.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (let attempt = 0; attempt < 6 && !cancelled; attempt++) {
        try {
          const m = await api.meta();
          if (cancelled) return;
          setMeta(m);
          setMetaError("");
          const shared = new URLSearchParams(window.location.search).get("a");
          if (shared && /^[0-9a-f]{24}$/.test(shared)) {
            try {
              const a = await api.analysis(shared);
              if (cancelled) return;
              setRegion(a.request.region);
              setTab(a.request.region.kind === "admin" ? "state" : a.request.region.kind === "box" ? "box" : "city");
              setSettings(settingsFrom(a));
              show(a);
              return;
            } catch {
              /* expired or bad link: fall through to defaults */
            }
          }
          setSettings(defaultSettings(m));
          return;
        } catch (e) {
          setMetaError(`${(e as Error).message} Retrying…`);
          await new Promise((r) => setTimeout(r, 4000 + attempt * 3000));
        }
      }
      if (!cancelled) setMetaError("The API isn't responding. Please reload the page in a minute.");
    })();
    return () => {
      cancelled = true;
    };
  }, [show]);

  // Re-render once a second while loading/waiting so the timers in the status line move.
  useEffect(() => {
    if (status.kind !== "loading" && status.kind !== "waiting") return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [status.kind]);

  useEffect(() => () => window.clearTimeout(retryTimer.current), []);

  const run = useCallback(async (r: RegionSelection | null = region, s: Settings | null = settings) => {
    if (!r || !s) return;
    window.clearTimeout(retryTimer.current);
    setStatus({ kind: "loading", since: Date.now() });
    try {
      show(await api.analyze(r, s));
    } catch (e) {
      const err = e as ApiError;
      // Upstream minute quota: worth waiting for automatically.
      if (err.status === 503 && err.retryAfter && err.retryAfter <= 90) {
        setStatus({ kind: "waiting", message: err.message, until: Date.now() + err.retryAfter * 1000 });
        retryTimer.current = window.setTimeout(() => run(r, s), err.retryAfter * 1000 + 500);
      } else {
        setStatus({ kind: "error", message: err.message || "Something went wrong." });
      }
    }
  }, [region, settings, show]);

  function pickRegion(r: RegionSelection) {
    setRegion(r);
    setDirty(true);
    if (r.kind !== "point" && settings?.source === "ghcn") setSettings({ ...settings, source: "era5" });
  }

  function runExample(ex: (typeof EXAMPLES)[number]) {
    if (!meta) return;
    const s = { ...(settings ?? defaultSettings(meta)) };
    if (ex.hazard && ex.hazard !== s.hazard) {
      const hz = meta.hazards.find((h) => h.id === ex.hazard)!;
      Object.assign(s, { hazard: hz.id, percentile: hz.defaults.percentile, minDuration: hz.defaults.min_duration, thresholdMode: "fixed" });
    }
    if (ex.region.kind !== "point") s.source = "era5";
    setSettings(s);
    setRegion(ex.region);
    setTab(ex.region.kind === "admin" ? "state" : "city");
    run(ex.region, s);
  }

  const span = settings ? settings.endYear - settings.startYear + 1 : 0;
  const canRun = !!region && !!settings && !!meta && span >= meta.limits.min_years && status.kind !== "loading";
  const mapMode = tab === "city" ? "point" : tab === "box" ? "box" : "none";
  const shownAnalysis = analysis && !dirty ? analysis : null;

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Climate hazard trends</h1>
          <p className="lede">
            How often has it been dangerously hot, or extremely wet, in a place, and is that changing? Daily weather
            back to 1940, percentile-based hazard definitions, and trend statistics that say when the data can't tell.
          </p>
        </div>
        <nav>
          <a href={REPO} target="_blank" rel="noreferrer">Method &amp; code</a>
          <a href={api.url("/api/docs")} target="_blank" rel="noreferrer">API</a>
        </nav>
      </header>

      {metaError && !meta && <p className="banner">{metaError}</p>}

      {meta && settings && (
        <section className="setup">
          <div className="controls">
            <RegionPicker tab={tab} onTab={setTab} region={region} onRegion={pickRegion} maxBoxDeg={meta.limits.max_box_deg} />
            <SettingsPanel meta={meta} settings={settings} region={region}
              onChange={(s) => { setSettings(s); setDirty(true); }} />
            <button type="button" className="primary" disabled={!canRun} onClick={() => run()}>
              {status.kind === "loading" ? "Analysing…" : "Analyse"}
            </button>
            {!region && <p className="hint">Choose a region first.</p>}
            <StatusLine status={status} />
          </div>
          <div className="map-wrap">
            <RegionMap region={region} analysis={shownAnalysis} mode={mapMode} onPick={pickRegion} />
          </div>
        </section>
      )}

      {meta && !analysis && status.kind !== "loading" && (
        <section className="examples">
          <span>Or try:</span>
          {EXAMPLES.map((ex) => (
            <button key={ex.label} type="button" onClick={() => runExample(ex)}>{ex.label}</button>
          ))}
        </section>
      )}

      {analysis && (
        <div className={`report${dirty ? " stale" : ""}`}>
          {dirty && region && (
            <p className="banner">
              Showing results for {analysis.region.label}. Settings changed{region ? ` (now: ${describe(region)})` : ""}, press Analyse to update.
            </p>
          )}
          <Summary analysis={analysis} />
          <AnnualChart analysis={analysis} metric={metric} onMetric={setMetric} selectedYear={year} onYear={setYear} />
          <HeatCalendar analysis={analysis} selectedYear={year} onYear={setYear} />
          {year !== null && <YearDetail analysis={analysis} year={year} onYear={setYear} />}
          <div className="two-col">
            <EventsTable analysis={analysis} onYear={setYear} />
            <QualityPanel analysis={analysis} />
          </div>
        </div>
      )}

      <footer>
        ERA5 data from Copernicus/ECMWF via <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0) · Station data
        from NOAA NCEI GHCN-Daily · Boundaries and map © OpenStreetMap contributors
      </footer>
    </div>
  );
}

function StatusLine({ status }: { status: Status }) {
  if (status.kind === "loading") {
    const secs = Math.round((Date.now() - status.since) / 1000);
    return (
      <p className="status" role="status">
        Working… {secs}s
        {secs >= 6 && <span className="hint"> New regions need decades of daily data from upstream, so the first run takes a while. After that it's cached.</span>}
        {secs >= 25 && <span className="hint"> If the API was asleep (free hosting), waking it adds up to a minute.</span>}
      </p>
    );
  }
  if (status.kind === "waiting") {
    const secs = Math.max(0, Math.round((status.until - Date.now()) / 1000));
    return <p className="status" role="status">{status.message} Retrying automatically in {secs}s.</p>;
  }
  if (status.kind === "error") return <p className="status error-text" role="alert">{status.message}</p>;
  return null;
}

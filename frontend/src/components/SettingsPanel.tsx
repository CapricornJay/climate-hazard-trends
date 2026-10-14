import type { Meta, RegionSelection, Settings } from "../types";

interface Props {
  meta: Meta;
  settings: Settings;
  region: RegionSelection | null;
  onChange: (s: Settings) => void;
}

export default function SettingsPanel({ meta, settings, region, onChange }: Props) {
  const hazard = meta.hazards.find((h) => h.id === settings.hazard)!;
  const set = (patch: Partial<Settings>) => onChange({ ...settings, ...patch });
  const years = { min: meta.years.first, max: meta.years.last };
  const stationAllowed = !region || region.kind === "point";
  const span = settings.endYear - settings.startYear + 1;

  function setHazard(id: Settings["hazard"]) {
    const h = meta.hazards.find((x) => x.id === id)!;
    onChange({
      ...settings,
      hazard: id,
      percentile: h.defaults.percentile,
      minDuration: h.defaults.min_duration,
      thresholdMode: h.threshold_modes.includes(settings.thresholdMode) ? settings.thresholdMode : "fixed",
    });
  }

  return (
    <>
      <fieldset>
        <legend>Hazard</legend>
        <select value={settings.hazard} onChange={(e) => setHazard(e.target.value as Settings["hazard"])}>
          {meta.hazards.map((h) => (
            <option key={h.id} value={h.id}>{h.label}</option>
          ))}
        </select>
      </fieldset>

      <fieldset>
        <legend>Years</legend>
        <div className="row">
          <input type="number" min={years.min} max={years.max} value={settings.startYear} aria-label="From year"
            onChange={(e) => set({ startYear: Number(e.target.value) })} />
          <span className="dash">to</span>
          <input type="number" min={years.min} max={years.max} value={settings.endYear} aria-label="To year"
            onChange={(e) => set({ endYear: Number(e.target.value) })} />
        </div>
        <div className="chips">
          {[[1990, 2020], [1961, years.max], [1991, years.max], [years.min, years.max]].map(([a, b]) => (
            <button key={`${a}-${b}`} type="button" onClick={() => set({ startYear: a, endYear: b })}
              className={settings.startYear === a && settings.endYear === b ? "active" : ""}>
              {a}–{b}
            </button>
          ))}
        </div>
        {span < meta.limits.min_years && <p className="hint error-text">Pick at least {meta.limits.min_years} years.</p>}
      </fieldset>

      <fieldset>
        <legend>Data source</legend>
        <label className="radio">
          <input type="radio" checked={settings.source === "era5"} onChange={() => set({ source: "era5" })} />
          ERA5 reanalysis <small>gridded, consistent, works everywhere</small>
        </label>
        <label className={`radio ${stationAllowed ? "" : "disabled"}`}>
          <input type="radio" checked={settings.source === "ghcn"} disabled={!stationAllowed}
            onChange={() => set({ source: "ghcn" })} />
          Nearest NOAA station <small>{stationAllowed ? "real observations, gaps and all" : "only for a single point"}</small>
        </label>
      </fieldset>

      <details className="advanced">
        <summary>Definition settings</summary>
        <label>
          Percentile threshold
          <select value={settings.percentile} onChange={(e) => set({ percentile: Number(e.target.value) })}>
            {[90, 95, 97.5, 99].map((p) => <option key={p} value={p}>{p}th</option>)}
          </select>
        </label>
        <label>
          Minimum run length
          <select value={settings.minDuration} onChange={(e) => set({ minDuration: Number(e.target.value) })}>
            {Array.from({ length: hazard.min_duration_range[1] - hazard.min_duration_range[0] + 1 }, (_, i) => i + hazard.min_duration_range[0]).map((d) => (
              <option key={d} value={d}>{d} day{d > 1 ? "s" : ""}</option>
            ))}
          </select>
        </label>
        <label>
          Baseline (threshold period)
          <select value={settings.baseline.join("-")} onChange={(e) => set({ baseline: e.target.value.split("-").map(Number) as [number, number] })}>
            {meta.baselines.map(([a, b]) => <option key={a} value={`${a}-${b}`}>{a}–{b}</option>)}
          </select>
        </label>
        {hazard.threshold_modes.includes("doy") && (
          <label>
            Threshold type
            <select value={settings.thresholdMode} onChange={(e) => set({ thresholdMode: e.target.value as Settings["thresholdMode"] })}>
              <option value="fixed">One value all year (classic heatwave)</option>
              <option value="doy">Per calendar day (unusual for the season)</option>
            </select>
          </label>
        )}
        <p className="hint">
          Default: {hazard.id === "heatwave" ? "3+ days above the 95th percentile of daily maximum temperature" : "days above the 95th percentile of wet-day rainfall"},
          threshold from {meta.defaults.baseline.join("–")} (the WMO reference period for climate-change monitoring).
        </p>
      </details>
    </>
  );
}

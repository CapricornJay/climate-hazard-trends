import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AdminArea, Place, RegionSelection } from "../types";

export type RegionTab = "city" | "state" | "box";

interface Props {
  tab: RegionTab;
  onTab: (t: RegionTab) => void;
  region: RegionSelection | null;
  onRegion: (r: RegionSelection) => void;
  maxBoxDeg: number;
}

function placeLabel(p: Place) {
  return [p.name, p.admin1, p.country].filter(Boolean).join(", ");
}

function CitySearch({ onRegion }: { onRegion: Props["onRegion"] }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setResults([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api.places(term, ctrl.signal)
        .then((r) => {
          setResults(r.results);
          setError(r.results.length ? "" : "No matching places.");
        })
        .catch((e) => {
          if (e.name !== "AbortError") setError((e as ApiError).message);
        });
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q]);

  return (
    <div className="search">
      <input
        type="search"
        placeholder="Search a city, e.g. Phoenix"
        value={q}
        maxLength={80}
        onChange={(e) => setQ(e.target.value)}
        aria-label="City"
      />
      {results.length > 0 && (
        <ul className="search-results">
          {results.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => {
                  onRegion({ kind: "point", lat: p.lat, lon: p.lon, label: placeLabel(p) });
                  setResults([]);
                  setQ(placeLabel(p));
                }}
              >
                <span>{p.name}</span>
                <small>{[p.admin1, p.country].filter(Boolean).join(", ")}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="hint">{error}</p>}
      <p className="hint">…or click anywhere on the map.</p>
    </div>
  );
}

function StateSearch({ onRegion }: { onRegion: Props["onRegion"] }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<AdminArea[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // OpenStreetMap's Nominatim doesn't allow search-as-you-type, hence the button.
  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.states(q.trim());
      setResults(r.results);
      if (!r.results.length) setError("Nothing found. Try the English name, e.g. 'Bavaria' or 'New South Wales'.");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="search">
      <form onSubmit={search} className="row">
        <input type="search" placeholder="State or province, e.g. Texas" value={q} maxLength={80}
          onChange={(e) => setQ(e.target.value)} aria-label="State or province" />
        <button type="submit" disabled={busy}>{busy ? "…" : "Search"}</button>
      </form>
      {results && results.length > 0 && (
        <ul className="search-results">
          {results.map((a) => (
            <li key={`${a.osm_type}${a.osm_id}`}>
              <button type="button" onClick={() => {
                onRegion({ kind: "admin", osm_type: a.osm_type, osm_id: a.osm_id, label: a.label, bbox: a.bbox, geometry: a.geometry });
                setResults(null);
              }}>
                <span>{a.name}</span>
                <small>{a.label}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="hint">{error}</p>}
      <p className="hint">Boundaries from OpenStreetMap. Large states are averaged over up to 9 grid points.</p>
    </div>
  );
}

function BoxInputs({ region, onRegion, maxBoxDeg }: { region: RegionSelection | null; onRegion: Props["onRegion"]; maxBoxDeg: number }) {
  const current = region?.kind === "box" ? region : null;
  const [vals, setVals] = useState({ south: "", west: "", north: "", east: "" });
  const [error, setError] = useState("");

  useEffect(() => {
    if (current) {
      setVals({ south: String(current.south), west: String(current.west), north: String(current.north), east: String(current.east) });
      setError("");
    }
  }, [current?.south, current?.west, current?.north, current?.east]); // eslint-disable-line react-hooks/exhaustive-deps

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const [s, w, n, ea] = [vals.south, vals.west, vals.north, vals.east].map(Number);
    if ([s, w, n, ea].some((v) => !Number.isFinite(v))) return setError("Fill in all four edges.");
    if (s < -90 || n > 90 || w < -180 || ea > 180) return setError("Latitude must be within ±90, longitude within ±180.");
    if (n <= s || ea <= w) return setError("North must be above south and east right of west.");
    if (n - s > maxBoxDeg || ea - w > maxBoxDeg) return setError(`Keep each side under ${maxBoxDeg}°.`);
    setError("");
    onRegion({ kind: "box", south: s, west: w, north: n, east: ea });
  }

  const field = (k: keyof typeof vals, label: string) => (
    <label>
      {label}
      <input inputMode="decimal" value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
    </label>
  );

  return (
    <form onSubmit={apply} className="box-inputs">
      <div className="grid4">
        {field("north", "North")}
        {field("south", "South")}
        {field("west", "West")}
        {field("east", "East")}
      </div>
      <button type="submit">Use this box</button>
      {error && <p className="hint error-text">{error}</p>}
      <p className="hint">…or press and drag on the map to draw one.</p>
    </form>
  );
}

export default function RegionPicker({ tab, onTab, region, onRegion, maxBoxDeg }: Props) {
  const tabs: [RegionTab, string][] = [["city", "City / point"], ["state", "State"], ["box", "Lat-lon box"]];
  return (
    <fieldset className="region-picker">
      <legend>Region</legend>
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => onTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "city" && <CitySearch onRegion={onRegion} />}
      {tab === "state" && <StateSearch onRegion={onRegion} />}
      {tab === "box" && <BoxInputs region={region} onRegion={onRegion} maxBoxDeg={maxBoxDeg} />}
      {region && <p className="selected">Selected: <strong>{describe(region)}</strong></p>}
    </fieldset>
  );
}

export function describe(r: RegionSelection): string {
  if (r.kind === "point") return r.label ?? `${r.lat.toFixed(2)}, ${r.lon.toFixed(2)}`;
  if (r.kind === "box") return r.label ?? `${r.south}…${r.north}°N, ${r.west}…${r.east}°E`;
  return r.label;
}

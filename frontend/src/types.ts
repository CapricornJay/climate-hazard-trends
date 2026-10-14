import type { Geometry } from "geojson";

export type RegionSelection =
  | { kind: "point"; lat: number; lon: number; label?: string }
  | { kind: "box"; south: number; west: number; north: number; east: number; label?: string }
  | {
      kind: "admin";
      osm_type: "R" | "W";
      osm_id: number;
      label: string;
      bbox?: [number, number, number, number];
      geometry?: Geometry;
    };

export interface Place {
  id: number;
  name: string;
  admin1?: string;
  country?: string;
  country_code?: string;
  lat: number;
  lon: number;
  population?: number;
}

export interface AdminArea {
  osm_type: "R" | "W";
  osm_id: number;
  name: string;
  label: string;
  bbox: [number, number, number, number]; // south, west, north, east
  geometry: Geometry;
}

export interface Metric {
  key: string;
  label: string;
  unit: string;
  kind: "count" | "continuous";
  description: string;
}

export interface HazardInfo {
  id: "heatwave" | "heavy_rain";
  label: string;
  variable: string;
  unit: string;
  metrics: Metric[];
  primary_metric: string;
  defaults: { percentile: number; min_duration: number };
  min_duration_range: [number, number];
  threshold_modes: string[];
}

export interface Meta {
  years: { first: number; last: number };
  hazards: HazardInfo[];
  default_hazard: string;
  sources: { id: string; label: string; regions: string[] }[];
  baselines: [number, number][];
  defaults: { start_year: number; end_year: number; baseline: [number, number] };
  limits: { max_box_deg: number; min_years: number; max_region_points: number };
}

export interface Settings {
  hazard: "heatwave" | "heavy_rain";
  source: "era5" | "ghcn";
  startYear: number;
  endYear: number;
  baseline: [number, number];
  percentile: number;
  minDuration: number;
  thresholdMode: "fixed" | "doy";
}

export interface Trend {
  n: number;
  kind: string;
  enough_data: boolean;
  mk?: { s: number; tau: number; z: number; p: number; p_uncorrected: number; autocorrelation_correction: number };
  sen?: { slope: number; intercept: number; lo: number; hi: number };
  ols?: { slope: number; intercept: number; r2: number; p: number; lo: number; hi: number };
  poisson?: {
    pct_per_decade: number;
    pct_per_decade_lo: number;
    pct_per_decade_hi: number;
    fit_start: number;
    fit_end: number;
    pct_over_period: number;
    dispersion: number;
    p: number;
  } | null;
  rolling?: [number, number][];
  line?: [number, number][];
  line_model?: "sen" | "poisson";
  decades?: { first: { from: number; to: number; mean: number }; last: { from: number; to: number; mean: number } } | null;
  zero_years?: number;
  mean?: number;
}

export type AnnualRow = { year: number; complete: boolean; coverage: number } & Record<string, number | null | boolean>;

export interface HazardEvent {
  start: string;
  end: string;
  duration: number;
  peak: number;
  peak_date: string;
  mean?: number;
  mean_excess?: number;
  cum_excess?: number;
  total?: number;
  excess?: number;
}

export interface Quality {
  expected_days: number;
  missing: number;
  filled: number;
  out_of_range: number;
  spikes: number;
  duplicates: number;
  source_flagged: number;
  source_flags: Record<string, number>;
  incomplete_years: number[];
  points: number;
}

export interface SamplePoint {
  lat: number;
  lon: number;
  weight: number;
  cell_lat?: number | null;
  cell_lon?: number | null;
  elevation?: number | null;
}

export interface Analysis {
  id: string;
  created_at: string;
  request: {
    region: RegionSelection;
    hazard: Settings["hazard"];
    source: Settings["source"];
    start_year: number;
    end_year: number;
    baseline_start: number;
    baseline_end: number;
    percentile: number;
    min_duration: number;
    threshold_mode: Settings["thresholdMode"];
  };
  region: {
    kind: "point" | "box" | "admin";
    key: string;
    label: string;
    bbox: [number, number, number, number];
    geometry: Geometry | null;
    spacing_deg: number;
    points: SamplePoint[];
    station?: { id: string; name: string; lat: number; lon: number; distance_km: number; first: string; last: string };
  };
  hazard: HazardInfo;
  source: { id: string; label: string; attribution: string; detail: string };
  threshold: {
    mode: "fixed" | "doy";
    value?: number;
    by_doy?: (number | null)[];
    min?: number;
    max?: number;
    percentile: number;
    baseline: [number, number];
    min_duration: number;
  };
  definition: string;
  annual: AnnualRow[];
  events: HazardEvent[];
  events_total: number;
  trends: Record<string, Trend>;
  insights: { kind: "headline" | "finding" | "caveat"; text: string }[];
  quality: Quality;
  daily: { start: string; end: string; values: (number | null)[] };
}

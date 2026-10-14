import type { AdminArea, Analysis, Meta, Place, RegionSelection, Settings } from "./types";

// Empty in dev (Vite proxies /api) and when the API serves the frontend itself.
const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  retryAfter?: number;

  constructor(status: number, message: string, retryAfter?: number) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the API. If nobody has used it for a while it may still be waking up.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const retry = body?.retry_after ?? (Number(res.headers.get("Retry-After")) || undefined);
    throw new ApiError(res.status, body?.message ?? `Request failed (${res.status})`, retry);
  }
  return body as T;
}

function regionPayload(r: RegionSelection) {
  switch (r.kind) {
    case "point":
      return { kind: "point", lat: r.lat, lon: r.lon, label: r.label };
    case "box":
      return { kind: "box", south: r.south, west: r.west, north: r.north, east: r.east, label: r.label };
    case "admin":
      return { kind: "admin", osm_type: r.osm_type, osm_id: r.osm_id, label: r.label };
  }
}

export const api = {
  meta: () => request<Meta>("/api/v1/meta"),

  places: (q: string, signal?: AbortSignal) =>
    request<{ results: Place[] }>(`/api/v1/places?q=${encodeURIComponent(q)}`, { signal }),

  states: (q: string) => request<{ results: AdminArea[] }>(`/api/v1/admin-areas?q=${encodeURIComponent(q)}`),

  analyze: (region: RegionSelection, s: Settings) =>
    request<Analysis>("/api/v1/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        region: regionPayload(region),
        hazard: s.hazard,
        source: s.source,
        start_year: s.startYear,
        end_year: s.endYear,
        baseline_start: s.baseline[0],
        baseline_end: s.baseline[1],
        percentile: s.percentile,
        min_duration: s.minDuration,
        threshold_mode: s.thresholdMode,
      }),
    }),

  analysis: (id: string) => request<Analysis>(`/api/v1/analyses/${encodeURIComponent(id)}`),

  url: (path: string) => BASE + path,
};

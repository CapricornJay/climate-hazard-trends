"""Pre-compute a handful of analyses so the demo is instant.

The deployed API shares an egress IP with other tenants, and Open-Meteo's free
tier counts per IP. Running this from a laptop against an API that points at
the same MongoDB fills the cache using the laptop's quota instead.

    python scripts/warm_cache.py http://localhost:8000
"""

import sys
import time

import httpx

DEFAULTS = {"start_year": 1990, "end_year": 2020}

REGIONS = [
    {"kind": "point", "lat": 33.448, "lon": -112.074, "label": "Phoenix, Arizona, United States"},
    {"kind": "point", "lat": 48.853, "lon": 2.349, "label": "Paris, Île-de-France, France"},
    {"kind": "point", "lat": 28.652, "lon": 77.231, "label": "Delhi, India"},
    {"kind": "point", "lat": 40.714, "lon": -74.006, "label": "New York, United States"},
    {"kind": "point", "lat": 51.509, "lon": -0.126, "label": "London, England, United Kingdom"},
    {"kind": "point", "lat": -33.868, "lon": 151.207, "label": "Sydney, New South Wales, Australia"},
    {"kind": "point", "lat": 12.972, "lon": 77.594, "label": "Bengaluru, Karnataka, India"},
    {"kind": "admin", "osm_type": "R", "osm_id": 114690, "label": "Texas, United States"},
]

EXTRA = [
    {"region": REGIONS[0], "hazard": "heatwave", "start_year": 1961, "end_year": 2025},
    {"region": {"kind": "point", "lat": 19.073, "lon": 72.883, "label": "Mumbai, Maharashtra, India"}, "hazard": "heavy_rain"},
    {"region": REGIONS[3], "source": "ghcn"},
]


def run(base: str) -> None:
    jobs = [{"region": r} for r in REGIONS] + EXTRA
    with httpx.Client(base_url=base, timeout=180) as client:
        for job in jobs:
            body = {**DEFAULTS, **job}
            for attempt in range(5):
                r = client.post("/api/v1/analyses", json=body)
                if r.status_code in (429, 503):
                    wait = int(r.json().get("retry_after") or 30)
                    print(f"  busy, waiting {wait}s")
                    time.sleep(wait + 1)
                    continue
                break
            label = body["region"]["label"]
            print(f"{r.status_code} {r.headers.get('x-cache', '')}  {label} [{body.get('hazard', 'heatwave')}]")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")

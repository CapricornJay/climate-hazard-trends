"""End-to-end through the HTTP API with fake upstreams (no network)."""

from datetime import date, timedelta

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import MemoryStore


class FakeUpstream:
    """Pretends to be Open-Meteo's archive API: a seasonal cycle, a slow
    warming trend and some noise, deterministic per location."""

    def __init__(self):
        self.archive_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "archive-api.open-meteo.com":
            self.archive_calls += 1
            return httpx.Response(200, json=self.archive(request.url.params))
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [
                {"id": 1, "name": "Testville", "latitude": 10.0, "longitude": 20.0, "country": "Nowhere"}]})
        return httpx.Response(404)

    def archive(self, params):
        lats = [float(x) for x in params["latitude"].split(",")]
        lons = [float(x) for x in params["longitude"].split(",")]
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        n = (end - start).days + 1
        times = [(start + timedelta(days=i)).isoformat() for i in range(n)]
        out = []
        for lat, lon in zip(lats, lons):
            rng = np.random.default_rng(int(abs(lat * 100 + lon)))
            t = np.arange(n)
            years = (start.year + t / 365.25) - 1960
            v = 22 + 9 * np.sin(2 * np.pi * (t - 110) / 365.25) + 0.04 * years + rng.normal(0, 2.5, n)
            values = [round(float(x), 1) for x in v]
            values[5] = None  # a null, like the real API sometimes returns
            out.append({
                "latitude": lat, "longitude": lon, "elevation": 100.0, "timezone": "GMT",
                "daily_units": {"time": "iso8601", params["daily"]: "°C" if "temperature" in params["daily"] else "mm"},
                "daily": {"time": times, params["daily"]: values},
            })
        return out if len(out) > 1 else out[0]


@pytest.fixture
def setup():
    upstream = FakeUpstream()
    settings = Settings(storage="memory", rate_analysis_per_min=100, cors_origins="http://allowed.test")
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(settings, store=MemoryStore(), http_client=http)
    with TestClient(app) as client:
        yield client, upstream


BODY = {"region": {"kind": "point", "lat": 10.02, "lon": 20.01, "label": "Testville"}, "start_year": 1991, "end_year": 2020}


def test_analysis_is_computed_once_then_served_from_cache(setup):
    client, upstream = setup
    r1 = client.post("/api/v1/analyses", json=BODY)
    assert r1.status_code == 201, r1.text
    a = r1.json()
    assert a["hazard"]["id"] == "heatwave"
    assert a["threshold"]["mode"] == "fixed"
    assert len(a["annual"]) == 30
    assert a["insights"][0]["kind"] == "headline"
    assert len(a["daily"]["values"]) == (date(2020, 12, 31) - date(1991, 1, 1)).days + 1
    assert upstream.archive_calls == 1

    r2 = client.post("/api/v1/analyses", json=BODY)
    assert r2.status_code == 200 and r2.headers["x-cache"] == "HIT"
    assert r2.json()["id"] == a["id"]

    # Same grid cell, different settings: no new upstream call needed.
    r3 = client.post("/api/v1/analyses", json={**BODY, "percentile": 90, "threshold_mode": "doy"})
    assert r3.status_code == 201
    assert upstream.archive_calls == 1

    got = client.get(f"/api/v1/analyses/{a['id']}")
    assert got.status_code == 200 and got.json()["id"] == a["id"]


def test_widening_the_window_only_fetches_the_missing_years(setup):
    client, upstream = setup
    client.post("/api/v1/analyses", json=BODY)
    r = client.post("/api/v1/analyses", json={**BODY, "end_year": 2023})
    assert r.status_code == 201
    assert upstream.archive_calls == 2
    assert len(r.json()["annual"]) == 33


def test_bad_input_is_rejected_with_a_readable_message(setup):
    client, _ = setup
    cases = [
        {**BODY, "region": {"kind": "point", "lat": 91, "lon": 0}},
        {**BODY, "start_year": 2015},  # fewer than 10 years
        {**BODY, "end_year": 2999},
        {**BODY, "hazard": "earthquake"},
        {**BODY, "surprise": True},
        {**BODY, "region": {"kind": "box", "south": 10, "north": 50, "west": 0, "east": 10}},  # too big
        {**BODY, "region": {"kind": "box", "south": 10, "north": 12, "west": 0, "east": 2}, "source": "ghcn"},
        {**BODY, "hazard": "heavy_rain", "threshold_mode": "doy"},
        {**BODY, "region": {"kind": "admin", "osm_type": "R", "osm_id": {"$gt": 0}}},
    ]
    for body in cases:
        r = client.post("/api/v1/analyses", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"] == "invalid_request" and r.json()["message"]


def test_oversized_body_is_refused(setup):
    client, _ = setup
    r = client.post("/api/v1/analyses", content=b"{" + b" " * 20_000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_exports(setup):
    client, _ = setup
    aid = client.post("/api/v1/analyses", json=BODY).json()["id"]
    csv = client.get(f"/api/v1/analyses/{aid}/annual.csv")
    assert csv.status_code == 200 and csv.text.startswith("year,complete,coverage,days")
    assert len(csv.text.strip().splitlines()) == 31
    assert client.get(f"/api/v1/analyses/{aid}/events.csv").status_code == 200
    png = client.get(f"/api/v1/analyses/{aid}/trend.png")
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    pdf = client.get(f"/api/v1/analyses/{aid}/report.pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"


def test_unknown_or_malformed_ids(setup):
    client, _ = setup
    assert client.get("/api/v1/analyses/" + "0" * 24).status_code == 404
    assert client.get("/api/v1/analyses/../../etc/passwd").status_code == 404
    assert client.get("/api/v1/analyses/not-an-id").status_code == 422


def test_rate_limit_kicks_in():
    settings = Settings(storage="memory", rate_general_per_min=3)
    app = create_app(settings, store=MemoryStore(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream())))
    with TestClient(app) as client:
        codes = [client.get("/api/v1/meta").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[-1] == 429


def test_spoofed_forwarded_for_is_ignored_by_default():
    settings = Settings(storage="memory", rate_general_per_min=2)
    app = create_app(settings, store=MemoryStore(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream())))
    with TestClient(app) as client:
        codes = [client.get("/api/v1/meta", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(4)]
    assert 429 in codes


def test_security_headers_and_cors(setup):
    client, _ = setup
    r = client.get("/api/v1/meta", headers={"Origin": "http://allowed.test"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["access-control-allow-origin"] == "http://allowed.test"
    r = client.get("/api/v1/meta", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in r.headers


def test_place_search_is_cached(setup):
    client, _ = setup
    r = client.get("/api/v1/places", params={"q": "Testv"})
    assert r.status_code == 200 and r.json()["results"][0]["name"] == "Testville"
    assert client.get("/api/v1/places", params={"q": "x"}).status_code == 422

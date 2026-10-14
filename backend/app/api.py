import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import exports
from .analysis import SOURCES
from .config import FIRST_YEAR, last_complete_year
from .hazards import HAZARDS
from .models import MAX_BOX_DEG, MIN_YEARS, AnalysisIn, clean_text
from .ratelimit import RateLimited

router = APIRouter(prefix="/api/v1")
ANALYSIS_ID = r"^[0-9a-f]{24}$"
SearchQuery = Query(min_length=2, max_length=80)


def limit(bucket: str):
    async def check(request: Request):
        state = request.app.state
        wait = state.limiters[bucket].take(state.client_ip(request))
        if wait > 0:
            raise RateLimited(wait)

    return Depends(check)


@router.get("/health")
async def health(request: Request):
    state = request.app.state
    db_ok = await state.store.ping()
    body = {"status": "ok" if db_ok else "degraded", "database": db_ok, "openmeteo_quota": state.budget.snapshot()}
    return JSONResponse(body, status_code=200 if db_ok else 503)


@router.get("/meta", dependencies=[limit("general")])
async def meta(request: Request):
    last = last_complete_year()
    return {
        "years": {"first": FIRST_YEAR, "last": last},
        "hazards": [h.to_dict() for h in HAZARDS.values()],
        "default_hazard": "heatwave",
        "sources": [{k: s[k] for k in ("id", "label", "regions")} for s in SOURCES.values()],
        "baselines": [[1961, 1990], [1981, 2010], [1991, 2020]],
        "defaults": {"start_year": 1990, "end_year": min(2020, last), "baseline": [1961, 1990]},
        "limits": {
            "max_box_deg": MAX_BOX_DEG,
            "min_years": MIN_YEARS,
            "max_region_points": request.app.state.settings.max_region_points,
        },
    }


@router.get("/places", dependencies=[limit("general")])
async def places(request: Request, q: str = SearchQuery):
    """City search (Open-Meteo geocoding). Fine for search-as-you-type."""
    q = clean_text(q)
    if len(q) < 2:
        return {"results": []}
    state = request.app.state
    key = f"places|{q.lower()}"
    results = await state.store.get_lookup(key)
    if results is None:
        results = await state.openmeteo.search_places(q)
        await state.store.put_lookup(key, results, 7 * 86400)
    return {"results": results}


@router.get("/admin-areas", dependencies=[limit("search")])
async def admin_areas(request: Request, q: str = SearchQuery):
    """State/province search (Nominatim). Called on submit only, never per keystroke."""
    q = clean_text(q)
    if len(q) < 2:
        return {"results": []}
    state = request.app.state
    key = f"states|{q.lower()}"
    results = await state.store.get_lookup(key)
    if results is None:
        results = await state.nominatim.search_states(q)
        for area in results:
            await state.store.put_lookup(f"area|{area['osm_type']}{area['osm_id']}", area, 90 * 86400)
        await state.store.put_lookup(key, results, 30 * 86400)
    return {"results": results}


@router.post("/analyses", dependencies=[limit("analysis")])
async def create_analysis(body: AnalysisIn, request: Request):
    state = request.app.state
    ip = state.client_ip(request)

    async def before_fetch():
        # Only charged when the analysis needs data we don't have cached.
        wait = state.limiters["upstream"].take(ip)
        if wait > 0:
            raise RateLimited(
                wait,
                "You've requested a lot of new regions in a short time. Regions that were analysed before "
                "still load instantly; new ones will be available again shortly.",
            )

    doc, cached = await state.analyses.run(body, before_fetch)
    return JSONResponse(
        doc,
        status_code=200 if cached else 201,
        headers={"X-Cache": "HIT" if cached else "MISS", "Location": f"/api/v1/analyses/{doc['id']}"},
    )


async def _load(request: Request, aid: str) -> dict:
    doc = await request.app.state.analyses.get(aid)
    if not doc:
        raise HTTPException(404, "No analysis with that id. It may have expired; run it again.")
    return doc


def _filename(doc: dict, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", doc["region"]["label"].lower()).strip("-")[:40] or "region"
    req = doc["request"]
    return f"{doc['hazard']['id']}-{slug}-{req['start_year']}-{req['end_year']}-{suffix}"


@router.get("/analyses/{aid}", dependencies=[limit("general")])
async def get_analysis(request: Request, aid: str = Path(pattern=ANALYSIS_ID)):
    doc = await _load(request, aid)
    # Content-addressed: the same id always means the same result.
    return JSONResponse(doc, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/analyses/{aid}/annual.csv", dependencies=[limit("general")])
async def annual_csv(request: Request, aid: str = Path(pattern=ANALYSIS_ID)):
    doc = await _load(request, aid)
    return Response(exports.annual_csv(doc), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(doc, "annual.csv")}"'})


@router.get("/analyses/{aid}/events.csv", dependencies=[limit("general")])
async def events_csv(request: Request, aid: str = Path(pattern=ANALYSIS_ID)):
    doc = await _load(request, aid)
    return Response(exports.events_csv(doc), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(doc, "events.csv")}"'})


@router.get("/analyses/{aid}/trend.png", dependencies=[limit("export")])
async def trend_png(request: Request, aid: str = Path(pattern=ANALYSIS_ID),
                    metric: str | None = Query(None, pattern=r"^[a-z0-9_]{1,24}$")):
    doc = await _load(request, aid)
    png = await run_in_threadpool(exports.trend_png, doc, metric)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/analyses/{aid}/report.pdf", dependencies=[limit("export")])
async def report_pdf(request: Request, aid: str = Path(pattern=ANALYSIS_ID)):
    doc = await _load(request, aid)
    pdf = await run_in_threadpool(exports.report_pdf, doc)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(doc, "report.pdf")}"'})

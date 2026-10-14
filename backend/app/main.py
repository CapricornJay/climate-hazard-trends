import json
import logging
import os
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from .analysis import AnalysisService
from .api import router
from .config import Settings
from .hazards import InsufficientData
from .ratelimit import RateLimited, TokenBucket, client_ip
from .series import SeriesRepo
from .sources.ghcn import Ghcn
from .sources.http import UpstreamError, make_client
from .sources.nominatim import Nominatim
from .sources.openmeteo import CallBudget, OpenMeteo
from .store import MemoryStore, MongoStore

log = logging.getLogger("app")

API_CSP = "default-src 'none'; frame-ancestors 'none'"
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://tile.openstreetmap.org; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        csp = None if path.startswith(("/api/docs", "/api/openapi")) else API_CSP if path.startswith("/api/") else APP_CSP

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                h.setdefault("X-Frame-Options", "DENY")
                if csp:
                    h.setdefault("Content-Security-Policy", csp)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyTooLarge(Exception):
    pass


class BodySizeLimit:
    """The only request bodies we accept are small JSON analysis requests."""

    def __init__(self, app, max_bytes: int = 16_384):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            return await _send_json(send, 413, {"error": "too_large", "message": "Request body too large."})

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await _send_json(send, 413, {"error": "too_large", "message": "Request body too large."})


async def _send_json(send, status: int, body: dict):
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


def create_app(settings: Settings | None = None, store=None, http_client=None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = app.state
        if store is not None:
            state.store = store
        elif settings.storage == "memory":
            log.warning("STORAGE=memory: nothing will be persisted")
            state.store = MemoryStore()
        else:
            state.store = MongoStore(settings.mongodb_uri, settings.mongodb_db)
        await state.store.init()
        state.http = http_client or make_client(settings.user_agent)
        state.budget = CallBudget(settings.openmeteo_minute_budget, settings.openmeteo_hour_budget, settings.openmeteo_day_budget)
        state.openmeteo = OpenMeteo(state.http, state.budget)
        state.nominatim = Nominatim(state.http)
        state.ghcn = Ghcn(state.http)
        repo = SeriesRepo(state.store, state.openmeteo, state.ghcn, max_request_weight=min(settings.openmeteo_hour_budget, 2500))
        state.analyses = AnalysisService(state.store, repo, state.nominatim, settings.max_region_points)
        yield
        if http_client is None:
            await state.http.aclose()
        await state.store.close()

    app = FastAPI(
        title="Climate hazard trends",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.client_ip = partial(client_ip, trusted_hops=settings.trusted_proxy_hops, header=settings.client_ip_header)
    app.state.limiters = {
        "general": TokenBucket(settings.rate_general_per_min, 60),
        "search": TokenBucket(30, 60, burst=10),
        "analysis": TokenBucket(settings.rate_analysis_per_min, 60, burst=10),
        "upstream": TokenBucket(settings.rate_upstream_per_hour, 3600, burst=5),
        "export": TokenBucket(settings.rate_export_per_min, 60, burst=5),
    }

    @app.exception_handler(RateLimited)
    async def _rate_limited(request: Request, exc: RateLimited):
        return JSONResponse({"error": "rate_limited", "message": exc.message, "retry_after": exc.retry_after},
                            status_code=429, headers={"Retry-After": str(exc.retry_after)})

    @app.exception_handler(UpstreamError)
    async def _upstream(request: Request, exc: UpstreamError):
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse({"error": "upstream", "message": str(exc), "retry_after": exc.retry_after},
                            status_code=exc.status_code, headers=headers)

    @app.exception_handler(InsufficientData)
    async def _insufficient(request: Request, exc: InsufficientData):
        return JSONResponse({"error": "insufficient_data", "message": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"].removeprefix("Value error, ")}
                   for e in exc.errors()]
        message = details[0]["message"] if details else "Invalid request"
        return JSONResponse({"error": "invalid_request", "message": message, "details": details}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": "http_error", "message": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal", "message": "Something went wrong on our side."}, status_code=500)

    app.include_router(router)

    if settings.static_dir and os.path.isdir(settings.static_dir):
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")

    # Added inner-to-outer: CORS ends up outermost so even 413/429s carry CORS headers.
    app.add_middleware(BodySizeLimit)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Cache", "Retry-After", "Location"],
        max_age=600,
    )
    return app


app = create_app()

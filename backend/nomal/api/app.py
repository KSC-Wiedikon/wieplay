"""FastAPI application: API routes, security headers, the built web app, the sync loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from nomal import __version__
from nomal.api import admin, auth, media, teams, videos, worker
from nomal.core.config import Settings, get_settings
from nomal.core.db import make_engine, make_sessionmaker
from nomal.core.storage import ObjectStore, make_store
from nomal.core.sync import BridgeClient, BridgeError, HttpBridgeClient, run_sync

log = logging.getLogger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def content_security_policy(storage_origin: str | None) -> str:
    """Media bytes and upload parts go straight to object storage (PLAN §7.6)."""
    storage = f" {storage_origin}" if storage_origin else ""
    return (
        f"default-src 'self'; img-src 'self' data: blob:{storage}; "
        f"media-src 'self' blob:{storage}; connect-src 'self'{storage}; "
        "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    keys = dict(settings.bridge_public_keys)

    def bridge_client_factory() -> BridgeClient:
        if settings.dev_bridge:
            from nomal.api.dev_bridge import FixtureBridgeClient

            return FixtureBridgeClient()
        return HttpBridgeClient(settings.bridge_api_url, settings.bridge_service_token)

    async def sync_loop() -> None:
        while True:
            await asyncio.sleep(settings.sync_interval_minutes * 60)
            try:
                await asyncio.to_thread(_sync_once, sessions, bridge_client_factory)
            except Exception:
                log.exception("background sync failed")

    async def reaper_loop() -> None:
        while True:
            await asyncio.sleep(settings.reaper_interval_seconds)
            try:
                await asyncio.to_thread(_reap_once, sessions, app.state.store)
            except Exception:
                log.exception("job reaper failed")

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = []
        if settings.sync_interval_minutes > 0:
            tasks.append(asyncio.create_task(sync_loop()))
        if settings.reaper_interval_seconds > 0:
            tasks.append(asyncio.create_task(reaper_loop()))
        yield
        for task in tasks:
            task.cancel()
        engine.dispose()

    app = FastAPI(
        title="nomal",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.env != "prod" else None,
    )
    app.state.sessionmaker = sessions
    app.state.bridge_client_factory = bridge_client_factory
    store: ObjectStore | None = make_store(settings)
    app.state.store = store
    csp = content_security_policy(settings.storage_origin)

    if settings.dev_bridge:
        from nomal.api.dev_bridge import DEV_KID, make_dev_bridge

        dev_router, dev_public = make_dev_bridge(settings)
        keys[DEV_KID] = dev_public
        app.include_router(dev_router)
    app.state.settings = settings.model_copy(update={"bridge_public_keys": keys})

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Cookie-authenticated writes must come from our own origin (CSRF).
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None and origin != settings.public_origin:
                return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", csp)
        return response

    @app.exception_handler(videos.FilmingBlocked)
    async def filming_blocked(_: Request, exc: videos.FilmingBlocked) -> JSONResponse:
        return JSONResponse(videos.blocked_body(exc.check), status_code=409)

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(admin.router)
    app.include_router(videos.router)
    app.include_router(media.router)
    app.include_router(worker.router)

    if settings.web_dist:
        _mount_web(app, Path(settings.web_dist))
    return app


def _sync_once(sessionmaker: sessionmaker[Session], factory: Callable[[], BridgeClient]) -> None:
    with sessionmaker() as session, contextlib.suppress(BridgeError):  # recorded in sync_state
        run_sync(session, factory())


def _reap_once(sessionmaker: sessionmaker[Session], store: ObjectStore | None) -> None:
    with sessionmaker() as session:
        count = worker.reap(session)
        if count:
            log.warning("reaped %d expired job leases", count)
        if store is not None:
            videos.sweep_deleted(session, store)


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA: real files when they exist, index.html for app routes."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"NOMAL_WEB_DIST has no index.html: {root}")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            cache = (
                "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            )
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def asgi() -> FastAPI:
    """Entry point for uvicorn: `uvicorn --factory nomal.api.app:asgi`."""
    return create_app()

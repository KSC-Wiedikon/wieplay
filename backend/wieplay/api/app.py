"""FastAPI application: API routes, security headers, the built web app, the sync loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from wieplay import __version__
from wieplay.api import (
    admin,
    analysis,
    auth,
    court,
    filters,
    guests,
    helpers,
    library,
    media,
    players,
    scoreboard,
    stats,
    tagging,
    teams,
    videos,
    worker,
)
from wieplay.core import scorelog
from wieplay.core.config import Settings, get_settings
from wieplay.core.db import make_engine, make_sessionmaker
from wieplay.core.retention import run_housekeeping
from wieplay.core.storage import ObjectStore, make_store
from wieplay.core.sync import BridgeClient, BridgeError, HttpBridgeClient, run_sync
from wieplay.core.taxonomy import seed as seed_taxonomies

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


class Guard:
    """Cross-origin writes refused (CSRF: cookie-authenticated writes must come from our own
    origin) and the security headers on every response.

    Plain ASGI on purpose: Starlette's `@app.middleware("http")` hides a client that went away
    from the route (`request.is_disconnected()` never turns true behind it), so a worker's
    long poll for a job went on for its full 25 s after the worker had gone and could lease a
    job to nobody: the job then waited for its lease to run out (PLAN §8.1).
    """

    def __init__(self, app: ASGIApp, *, public_origin: str, csp: str) -> None:
        self.app = app
        self.public_origin = public_origin
        self.csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None and origin != self.public_origin:
                refused = JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
                await refused(scope, receive, self._with_headers(send))
                return
        await self.app(scope, receive, self._with_headers(send))

    def _with_headers(self, send: Send) -> Send:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "same-origin")
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", self.csp)
            await send(message)

        return send_with_headers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    keys = dict(settings.bridge_public_keys)

    def bridge_client_factory() -> BridgeClient:
        if settings.dev_bridge:
            from wieplay.api.dev_bridge import FixtureBridgeClient

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
        try:
            with sessions() as db:
                seed_taxonomies(db)
        except Exception:
            log.exception("could not seed the taxonomy table (tagging still works from YAML)")
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
        title="wieplay",
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
        from wieplay.api.dev_bridge import DEV_KID, make_dev_bridge

        dev_router, dev_public = make_dev_bridge(settings)
        keys[DEV_KID] = dev_public
        app.include_router(dev_router)
    app.state.settings = settings.model_copy(update={"bridge_public_keys": keys})

    app.add_middleware(Guard, public_origin=settings.public_origin, csp=csp)

    @app.exception_handler(videos.FilmingBlocked)
    async def filming_blocked(_: Request, exc: videos.FilmingBlocked) -> JSONResponse:
        return JSONResponse(videos.blocked_body(exc.check), status_code=409)

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.router)
    app.include_router(guests.auth_router)
    app.include_router(teams.router)
    app.include_router(guests.router)
    app.include_router(helpers.router)
    app.include_router(admin.router)
    app.include_router(videos.router)
    app.include_router(media.router)
    app.include_router(worker.router)
    app.include_router(tagging.router)
    app.include_router(library.router)
    app.include_router(stats.router)
    app.include_router(filters.router)
    app.include_router(analysis.router)
    app.include_router(court.router)
    app.include_router(players.router)
    app.include_router(scoreboard.router)

    if settings.web_dist:
        _mount_web(app, Path(settings.web_dist))
    return app


def _sync_once(sessionmaker: sessionmaker[Session], factory: Callable[[], BridgeClient]) -> None:
    with sessionmaker() as session, contextlib.suppress(BridgeError):  # recorded in sync_state
        client = factory()
        run_sync(session, client)
        # Videos with a game and no scoreboard log yet: tried once a day (PLAN §10.10).
        try:
            scorelog.import_missing(session, client)
        except Exception:
            session.rollback()
            log.exception("score log sweep failed")


HOUSEKEEPING_EVERY = timedelta(hours=24)
_last_housekeeping: list[datetime] = []


def _reap_once(sessionmaker: sessionmaker[Session], store: ObjectStore | None) -> None:
    with sessionmaker() as session:
        count = worker.reap(session)
        if count:
            log.warning("reaped %d expired job leases", count)
        if store is not None:
            videos.sweep_deleted(session, store)
            library.sweep_exports(session, store)
        now = datetime.now(UTC)
        if not _last_housekeeping or now - _last_housekeeping[-1] >= HOUSEKEEPING_EVERY:
            _last_housekeeping[:] = [now]
            done = run_housekeeping(session, now)
            for video_id in done.video_ids:
                media.permission_cache.forget_video(video_id)  # as in a coach's delete
            if done.any():
                log.info("housekeeping: %s", done.counts())


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA: real files when they exist, index.html for app routes."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"WIEPLAY_WEB_DIST has no index.html: {root}")

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
    """Entry point for uvicorn: `uvicorn --factory wieplay.api.app:asgi`."""
    return create_app()

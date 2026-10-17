"""Playback through the API (PLAN §7.6).

The API serves the playlists. Every segment, sprite and poster request is checked against
the session and answered with a 302 to an object-storage URL signed for 5 minutes for that
one object, so a copied URL is useless without the session or dead within minutes.
"""

from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from nomal.api.access import visible_video
from nomal.api.deps import DbDep, SettingsDep, StoreDep, ViewerDep, audit, session_key
from nomal.core.media import (
    RENDITION_NAMES,
    SEGMENT_NAME,
    THUMB_NAME,
    master_playlist,
    thumbnails_vtt,
)
from nomal.core.models import Video, VideoRendition

router = APIRouter(prefix="/api/media", tags=["media"])

HLS = "application/vnd.apple.mpegurl"
PRIVATE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}
PERMISSION_TTL = 60.0
CACHE_MAX = 5000


class _PermissionCache:
    """(session, video) → allowed until; avoids loading every team per 4-second segment."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until: dict[tuple[str, uuid.UUID], float] = {}

    def ok(self, key: tuple[str, uuid.UUID]) -> bool:
        with self._lock:
            until = self._until.get(key)
            return until is not None and until > time.monotonic()

    def remember(self, key: tuple[str, uuid.UUID]) -> None:
        with self._lock:
            if len(self._until) >= CACHE_MAX:
                now = time.monotonic()
                self._until = {k: v for k, v in self._until.items() if v > now}
                if len(self._until) >= CACHE_MAX:
                    self._until.clear()
            self._until[key] = time.monotonic() + PERMISSION_TTL

    def forget_video(self, video_id: uuid.UUID) -> None:
        with self._lock:
            self._until = {k: v for k, v in self._until.items() if k[1] != video_id}

    def clear(self) -> None:
        """Forget every answer (a team helper was removed: their sessions ask again)."""
        with self._lock:
            self._until.clear()


permission_cache = _PermissionCache()
_viewed: set[tuple[str, uuid.UUID]] = set()


def _playable(db: DbDep, viewer: ViewerDep, video_id: uuid.UUID) -> Video:
    video, _ = visible_video(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "This video is not ready yet")
    return video


def _check(request: Request, db: DbDep, viewer: ViewerDep, video_id: uuid.UUID) -> None:
    key = (session_key(request), video_id)
    if permission_cache.ok(key):
        return
    _playable(db, viewer, video_id)
    permission_cache.remember(key)


def _signed(store: StoreDep, settings: SettingsDep, key: str) -> RedirectResponse:
    url = store.presign_get(key, settings.media_url_ttl_seconds)
    return RedirectResponse(url, status_code=302, headers=PRIVATE)


@router.get("/{video_id}/master.m3u8")
def master(video_id: uuid.UUID, request: Request, viewer: ViewerDep, db: DbDep) -> Response:
    video = _playable(db, viewer, video_id)
    permission_cache.remember((session_key(request), video_id))
    seen = (session_key(request), video_id)
    if seen not in _viewed:
        # A video view is logged once per video per session (PLAN §5.8).
        _viewed.add(seen)
        audit(db, viewer.member_id, "video_viewed", "video", str(video_id), s=seen[0][:12])
        db.commit()
    return Response(master_playlist(video.renditions), media_type=HLS, headers=PRIVATE)


@router.get("/{video_id}/{rendition}/index.m3u8")
def media_playlist(
    video_id: uuid.UUID, rendition: str, request: Request, viewer: ViewerDep, db: DbDep
) -> Response:
    _check(request, db, viewer, video_id)
    row = db.get(VideoRendition, (video_id, rendition))
    if row is None:
        raise HTTPException(404, "Rendition not found")
    return Response(row.playlist, media_type=HLS, headers=PRIVATE)


@router.get("/{video_id}/thumbs.vtt")
def thumbs_vtt(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> Response:
    video = _playable(db, viewer, video_id)
    if not video.thumbs:
        raise HTTPException(404, "No thumbnails")
    body = thumbnails_vtt(video, f"/api/media/{video_id}")
    return PlainTextResponse(body, media_type="text/vtt", headers=PRIVATE)


@router.get("/{video_id}/thumbs/{name}")
def thumb(
    video_id: uuid.UUID,
    name: str,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RedirectResponse:
    if not THUMB_NAME.fullmatch(name):
        raise HTTPException(404, "Not found")
    _check(request, db, viewer, video_id)
    return _signed(store, settings, f"media/{video_id}/thumbs/{name}")


@router.get("/{video_id}/poster.jpg")
def poster(
    video_id: uuid.UUID,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RedirectResponse:
    video = _playable(db, viewer, video_id)
    if not (video.thumbs or {}).get("poster"):
        raise HTTPException(404, "No poster")
    return _signed(store, settings, f"media/{video_id}/thumbs/poster.jpg")


@router.get("/{video_id}/{rendition}/{name}")
def segment(
    video_id: uuid.UUID,
    rendition: str,
    name: str,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RedirectResponse:
    if not SEGMENT_NAME.fullmatch(name) or rendition not in RENDITION_NAMES:
        raise HTTPException(404, "Not found")
    _check(request, db, viewer, video_id)
    return _signed(store, settings, f"media/{video_id}/{rendition}/{name}")

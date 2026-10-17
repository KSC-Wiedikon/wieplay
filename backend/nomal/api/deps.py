"""Request-scoped dependencies: settings, database session, the logged-in viewer."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from nomal.core import guests
from nomal.core.config import Settings
from nomal.core.grants import analyst_lineages
from nomal.core.models import AuditLog, UserSession
from nomal.core.permissions import Viewer, can_manage_nomal
from nomal.core.storage import ObjectStore
from nomal.core.sync import BridgeClient, BridgeError

SESSION_COOKIE = "nomal_session"
LAST_SEEN_EVERY = timedelta(minutes=5)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def hash_session_id(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def current_viewer(request: Request, db: DbDep) -> Viewer:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "Not logged in")
    row = db.get(UserSession, hash_session_id(raw))
    now = datetime.now(UTC)
    if row is None or row.expires_at <= now:
        raise HTTPException(401, "Session expired")
    if row.claims.get("guest") is True:
        # A guest club's session (PLAN §4.5): everything from the database, every request, so
        # a person taken off the roster, a disabled account or team ends it at once.
        guest = guests.guest_viewer(db, row.member_id)
        if guest is None:
            raise HTTPException(401, "Session expired")
        viewer = guest
    else:
        # Team helper grants are Nomal's own and change while a session runs: read them on
        # every request, so a new helper sees the team at once and a removed one loses it.
        viewer = _wiedisync_viewer(row.claims).with_grants(analyst_lineages(db, row.member_id))
    if now - row.last_seen_at > LAST_SEEN_EVERY:
        row.last_seen_at = now
        db.commit()
    request.state.viewer = viewer
    return viewer


def _wiedisync_viewer(claims: dict[str, Any]) -> Viewer:
    """A KSCW member from the bridge's claims. Guest team ids are dropped: a Wiedisync login
    never reaches a guest club's team."""
    teams = [t for t in claims.get("teams", []) if not guests.is_guest_id(int(t["team"]))]
    return Viewer.from_claims({**claims, "teams": teams})


ViewerDep = Annotated[Viewer, Depends(current_viewer)]


def admin_viewer(viewer: ViewerDep) -> Viewer:
    if not can_manage_nomal(viewer):
        raise HTTPException(403, "Admins only")
    return viewer


AdminDep = Annotated[Viewer, Depends(admin_viewer)]


def audit(
    db: Session,
    actor: int | None,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    **details: Any,
) -> None:
    db.add(
        AuditLog(
            actor_member_id=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details=details,
        )
    )


def get_store(request: Request) -> ObjectStore:
    store: ObjectStore | None = request.app.state.store
    if store is None:
        raise HTTPException(503, "Video storage is not configured")
    return store


StoreDep = Annotated[ObjectStore, Depends(get_store)]


def get_bridge(request: Request) -> BridgeClient | None:
    try:
        client: BridgeClient = request.app.state.bridge_client_factory()
    except BridgeError:
        return None
    return client


BridgeDep = Annotated[BridgeClient | None, Depends(get_bridge)]


def session_key(request: Request) -> str:
    """Stable per-session key (hash of the cookie) for caches and audit de-duplication."""
    return hash_session_id(request.cookies.get(SESSION_COOKIE, ""))

"""Request-scoped dependencies: settings, database session, the logged-in viewer."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from nomal.core.config import Settings
from nomal.core.models import AuditLog, UserSession
from nomal.core.permissions import Viewer, can_manage_nomal

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
    if now - row.last_seen_at > LAST_SEEN_EVERY:
        row.last_seen_at = now
        db.commit()
    viewer = Viewer.from_claims(row.claims)
    request.state.viewer = viewer
    return viewer


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

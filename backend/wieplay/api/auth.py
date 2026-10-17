"""Login through the Wiedisync bridge (PLAN §4.2).

/api/auth/login     → remembers a random state in a short cookie, sends the browser to
                      the bridge's authorize URL.
/api/auth/callback  ← the bridge sends the browser back with a 60-second, single-use,
                      Ed25519-signed token; WiePlay verifies it and starts its own session.
"""

from __future__ import annotations

import base64
import html
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError

from wieplay.api.deps import (
    SESSION_COOKIE,
    DbDep,
    SettingsDep,
    ViewerDep,
    audit,
    hash_session_id,
)
from wieplay.core.guests import is_guest_id
from wieplay.core.models import LoginNonce, UserSession
from wieplay.core.tokens import TokenError, verify_bridge_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

STATE_COOKIE = "wieplay_auth"
STATE_TTL = 600
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def safe_next(raw: str | None) -> str:
    """Only same-site absolute paths; anything else lands on the home page."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return "/"
    if raw.startswith("/api/"):
        return "/"
    return raw


def _encode_state(state: str, next_path: str) -> str:
    return state + "." + base64.urlsafe_b64encode(next_path.encode()).decode().rstrip("=")


def _decode_state(value: str | None) -> tuple[str, str] | None:
    if not value or "." not in value:
        return None
    state, encoded = value.split(".", 1)
    try:
        next_path = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except ValueError:
        return None
    return state, safe_next(next_path)


def _login_failed(code: str) -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><title>WiePlay</title>"
        "<body style='font-family:system-ui;margin:3rem;color:#13203A;background:#EEF1F5'>"
        f"<p>Login failed ({html.escape(code)}).</p><p><a href='/'>Try again</a></p>"
    )
    return HTMLResponse(body, status_code=400, headers=NO_STORE)


@router.get("/login")
def login(settings: SettingsDep, next: str = "/") -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    query = urlencode({"state": state, "return": settings.callback_url})
    response = RedirectResponse(f"{settings.bridge_authorize_url}?{query}", 302, headers=NO_STORE)
    response.set_cookie(
        STATE_COOKIE,
        _encode_state(state, safe_next(next)),
        max_age=STATE_TTL,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth",
    )
    return response


@router.get("/callback", response_model=None)
def callback(
    request: Request, settings: SettingsDep, db: DbDep, token: str = "", state: str = ""
) -> Response:
    remembered = _decode_state(request.cookies.get(STATE_COOKIE))
    if remembered is None or not state or not secrets.compare_digest(remembered[0], state):
        return _login_failed("state")
    try:
        claims = verify_bridge_token(
            token, keys=settings.bridge_public_keys, issuer=settings.bridge_issuer
        )
    except TokenError:
        return _login_failed("token")
    if not secrets.compare_digest(claims.state, state):
        return _login_failed("state")
    if is_guest_id(claims.member_id):
        return _login_failed("member")  # guest clubs' ids: never a Wiedisync member

    try:
        db.add(LoginNonce(jti=claims.jti))
        db.flush()
    except IntegrityError:
        db.rollback()
        return _login_failed("replay")

    raw_session = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        UserSession(
            id_hash=hash_session_id(raw_session),
            member_id=claims.member_id,
            claims=claims.as_session_claims(),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_hours),
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        )
    )
    audit(db, claims.member_id, "login")
    db.commit()

    response = RedirectResponse(remembered[1], 302, headers=NO_STORE)
    response.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(STATE_COOKIE, path="/api/auth")
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, viewer: ViewerDep, settings: SettingsDep, db: DbDep) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        row = db.get(UserSession, hash_session_id(raw))
        if row is not None:
            db.delete(row)
    audit(db, viewer.member_id, "logout")
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True)
    return response

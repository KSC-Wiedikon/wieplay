"""A fake Wiedisync bridge for local development (WIEPLAY_ENV=local, WIEPLAY_DEV_BRIDGE=true).

It signs login tokens with a throwaway key and serves fixture teams, so the whole login
and sync flow runs without Wiedisync. Never mounted outside local development.
"""

from __future__ import annotations

import html
import secrets
import time
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wieplay.api import dev_fixtures
from wieplay.core.config import Settings
from wieplay.core.tokens import generate_keypair, sign_token

DEV_KID = "dev"


class FixtureBridgeClient:
    def list_teams(self) -> Mapping[str, Any]:
        return dev_fixtures.team_listing(date.today())

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]:
        return dev_fixtures.snapshot(team_ids, date.today())

    def adult_check(self, team_id: int, on: date) -> Mapping[str, Any]:
        return dev_fixtures.adult_check(team_id, on)

    def score_log(self, game_id: int) -> Mapping[str, Any]:
        """No scoreboard logs in local development: every game has none."""
        return {"version": 1, "game": {"id": game_id}, "source": None, "events": [], "sets": []}

    def club_games(
        self, *, on: date | None = None, ids: Sequence[int] | None = None, sport: str | None = None
    ) -> Mapping[str, Any]:
        return dev_fixtures.club_games(date.today(), on, ids, sport)


def make_dev_bridge(settings: Settings) -> tuple[APIRouter, str]:
    """Router for /api/dev-bridge and the public key it signs with."""
    private_pem, public_pem = generate_keypair()
    router = APIRouter(prefix="/api/dev-bridge", tags=["dev-bridge"])

    def _check_return(url: str) -> str:
        if url != settings.callback_url:
            raise HTTPException(400, "return URL not allowed")
        return url

    @router.get("/authorize", response_class=HTMLResponse)
    def authorize(state: str, request: Request) -> HTMLResponse:
        back = _check_return(request.query_params.get("return", ""))

        def row(key: str, claims: dict[str, Any]) -> str:
            link = urlencode({"persona": key, "state": state, "return": back})
            label = f"{claims['name']} · roles {', '.join(claims['roles'])}"
            return (
                f"<li><a href='/api/dev-bridge/issue?{html.escape(link)}'>{html.escape(key)}</a>"
                f" · {html.escape(label)}</li>"
            )

        rows = "".join(row(k, c) for k, c in dev_fixtures.personas(date.today()).items())
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Fake Wiedisync</title>"
            "<body style='font-family:system-ui;margin:3rem'>"
            "<h1>Fake Wiedisync login</h1><p>Local development only. Log in as:</p>"
            f"<ul>{rows}</ul>"
        )

    @router.get("/issue")
    def issue(persona: str, state: str, request: Request) -> RedirectResponse:
        back = _check_return(request.query_params.get("return", ""))
        claims = dev_fixtures.personas(date.today()).get(persona)
        if claims is None:
            raise HTTPException(404, "unknown persona")
        now = int(time.time())
        token = sign_token(
            {
                **claims,
                "iss": settings.bridge_issuer,
                "aud": "wieplay",
                "iat": now,
                "exp": now + 60,
                "jti": secrets.token_urlsafe(16),
                "state": state,
            },
            private_pem=private_pem,
            kid=DEV_KID,
        )
        return RedirectResponse(f"{back}?{urlencode({'token': token, 'state': state})}", 302)

    return router, public_pem

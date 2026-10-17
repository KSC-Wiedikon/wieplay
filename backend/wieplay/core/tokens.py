"""Login tokens from the Wiedisync bridge (PLAN §4.2).

The bridge signs a short-lived EdDSA (Ed25519) JWT; WiePlay verifies it with the bridge's
public key, chosen by the token's `kid`. Each token can be used once (the caller records
its `jti`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

AUDIENCE = "wieplay"
MAX_LIFETIME_SECONDS = 300
TEAM_RELS = frozenset({"player", "coach", "team_responsible", "captain"})


class TokenError(Exception):
    """The token is not acceptable. The message is safe to log, not to show verbatim."""


@dataclass(frozen=True)
class TeamRel:
    team: int
    season: str
    rel: tuple[str, ...]
    guest: bool = False


@dataclass(frozen=True)
class BridgeClaims:
    member_id: int
    member_uuid: str
    name: str
    roles: tuple[str, ...]
    sport_admin: tuple[str, ...]
    teams: tuple[TeamRel, ...]
    state: str
    jti: str

    def as_session_claims(self) -> dict[str, Any]:
        """What a session keeps (no state, no jti)."""
        return {
            "mid": self.member_id,
            "sub": self.member_uuid,
            "name": self.name,
            "roles": list(self.roles),
            "sport_admin": list(self.sport_admin),
            "teams": [
                {"team": t.team, "season": t.season, "rel": list(t.rel), "guest": t.guest}
                for t in self.teams
            ],
        }


def _str_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TokenError(f"claim {field} must be a list of strings")
    return tuple(value)


def _teams(value: Any) -> tuple[TeamRel, ...]:
    if not isinstance(value, list):
        raise TokenError("claim teams must be a list")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise TokenError("claim teams must contain objects")
        team, season = item.get("team"), item.get("season")
        if not isinstance(team, int) or isinstance(team, bool) or not isinstance(season, str):
            raise TokenError("team entries need an integer team and a season")
        rels = _str_list(item.get("rel", []), "teams.rel")
        if not set(rels) <= TEAM_RELS:
            raise TokenError("unknown team relation")
        out.append(TeamRel(team=team, season=season, rel=rels, guest=item.get("guest") is True))
    return tuple(out)


def verify_bridge_token(
    token: str,
    *,
    keys: Mapping[str, str],
    issuer: str,
    leeway: int = 10,
) -> BridgeClaims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError(f"malformed token: {exc}") from exc
    if header.get("alg") != "EdDSA":
        raise TokenError("unexpected algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        raise TokenError("unknown key id")
    try:
        payload = jwt.decode(
            token,
            keys[kid],
            algorithms=["EdDSA"],
            audience=AUDIENCE,
            issuer=issuer,
            leeway=leeway,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if int(payload["exp"]) - int(payload["iat"]) > MAX_LIFETIME_SECONDS:
        raise TokenError("token lifetime too long")
    mid, name, state = payload.get("mid"), payload.get("name"), payload.get("state")
    if not isinstance(mid, int) or isinstance(mid, bool):
        raise TokenError("claim mid must be an integer")
    if not isinstance(name, str) or not isinstance(state, str) or not state:
        raise TokenError("claims name and state are required")
    return BridgeClaims(
        member_id=mid,
        member_uuid=str(payload["sub"]),
        name=name,
        roles=_str_list(payload.get("roles", []), "roles"),
        sport_admin=_str_list(payload.get("sport_admin", []), "sport_admin"),
        teams=_teams(payload.get("teams", [])),
        state=state,
        jti=str(payload["jti"]),
    )


def generate_keypair() -> tuple[str, str]:
    """A fresh Ed25519 key pair as (private PEM, public PEM). Dev bridge and tests."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def sign_token(payload: Mapping[str, Any], *, private_pem: str, kid: str) -> str:
    """Sign like the bridge does. Dev bridge and tests only."""
    return jwt.encode(dict(payload), private_pem, algorithm="EdDSA", headers={"kid": kid})

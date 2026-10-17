import time

import jwt
import pytest

from tests.conftest import ISSUER, KID, OTHER_PRIVATE_PEM, PUBLIC_PEM, make_token
from wieplay.core.tokens import TokenError, verify_bridge_token

KEYS = {KID: PUBLIC_PEM}
CLAIMS = {
    "mid": 7,
    "sub": "4b1d0b5e-0000-4000-8000-000000000007",
    "name": "Alex Muster",
    "roles": ["user"],
    "sport_admin": [],
    "teams": [{"team": 92, "season": "2026/27", "rel": ["player", "captain"]}],
}


def verify(token: str):
    return verify_bridge_token(token, keys=KEYS, issuer=ISSUER)


def test_valid_token() -> None:
    claims = verify(make_token(CLAIMS, "s1"))
    assert claims.member_id == 7
    assert claims.state == "s1"
    assert claims.teams[0].team == 92
    assert claims.teams[0].rel == ("player", "captain")
    assert claims.as_session_claims()["teams"][0]["season"] == "2026/27"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"exp": int(time.time()) - 60}, "expired"),
        ({"aud": "someone-else"}, "audience"),
        ({"iss": "wiedisync-prod"}, "issuer"),
    ],
)
def test_rejects_wrong_registered_claims(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(TokenError, match=f"(?i){message}"):
        verify(make_token(CLAIMS, "s", **overrides))


def test_rejects_unknown_kid_and_foreign_key() -> None:
    with pytest.raises(TokenError, match="key id"):
        verify(make_token(CLAIMS, "s", kid="other"))
    with pytest.raises(TokenError, match="Signature"):
        verify(make_token(CLAIMS, "s", private_pem=OTHER_PRIVATE_PEM))


def test_rejects_other_algorithms() -> None:
    token = jwt.encode(
        {**CLAIMS, "state": "s"},
        "secret-secret-secret-secret-1234",
        algorithm="HS256",
        headers={"kid": KID},
    )
    with pytest.raises(TokenError, match="algorithm"):
        verify(token)


def test_rejects_long_lived_tokens() -> None:
    with pytest.raises(TokenError, match="lifetime"):
        verify(make_token(CLAIMS, "s", lifetime=3600))


def test_rejects_bad_claim_shapes() -> None:
    with pytest.raises(TokenError, match="relation"):
        verify(make_token({**CLAIMS, "teams": [{"team": 1, "season": "x", "rel": ["owner"]}]}, "s"))
    with pytest.raises(TokenError, match="mid"):
        verify(make_token({**CLAIMS, "mid": "7"}, "s"))
    with pytest.raises(TokenError, match="state"):
        verify(make_token(CLAIMS, ""))

"""A token signed by the real Wiedisync bridge (kscw-endpoints wieplay-bridge.js, signWiePlayToken)
must verify here. The fixture was produced by that code; it is long expired, hence the leeway."""

import json
from pathlib import Path

from wieplay.core.tokens import verify_bridge_token

FIXTURE = Path(__file__).parent / "fixtures" / "bridge_token_js.json"


def test_token_signed_by_the_javascript_bridge_verifies() -> None:
    data = json.loads(FIXTURE.read_text())
    claims = verify_bridge_token(
        data["token"], keys={"k1": data["pub"]}, issuer="wiedisync-dev", leeway=10**10
    )
    assert claims.member_id == 42
    assert claims.teams[0].rel == ("captain", "player")
    assert claims.state == "abcdefghijklmnopqrstuvwx"

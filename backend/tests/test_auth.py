from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from nomal.api.dev_fixtures import personas
from tests.conftest import OTHER_PRIVATE_PEM, PUBLIC_URL, login, make_token, start_login

PLAYER = personas(datetime.now(UTC).date())["player-h3"]


def test_login_redirects_to_the_bridge_with_state_and_callback(client: TestClient) -> None:
    response = client.get("/api/auth/login", params={"next": "/teams/9101"})
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://bridge.test/kscw/nomal/v1/authorize"
    )
    query = parse_qs(location.query)
    assert query["return"] == [f"{PUBLIC_URL}/api/auth/callback"]
    assert len(query["state"][0]) >= 30
    assert "nomal_auth" in response.cookies


def test_full_login_starts_a_session_and_returns_to_next(client: TestClient) -> None:
    state = start_login(client, "/teams/9101")
    response = client.get(
        "/api/auth/callback", params={"token": make_token(PLAYER, state), "state": state}
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/teams/9101"
    assert response.headers["referrer-policy"] == "no-referrer"
    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["member"]["name"] == "Alex Muster"
    assert body["is_admin"] is False
    assert body["teams"] == []  # nothing enabled or synced yet


def test_unsafe_next_falls_back_to_home(client: TestClient) -> None:
    for bad in ["https://evil.example/", "//evil.example", "/\\evil.example", "/api/admin/sync"]:
        state = start_login(client, bad)
        response = client.get(
            "/api/auth/callback", params={"token": make_token(PLAYER, state), "state": state}
        )
        assert response.headers["location"] == "/"


def test_a_token_works_only_once(client: TestClient) -> None:
    state = start_login(client)
    token = make_token(PLAYER, state)
    assert (
        client.get("/api/auth/callback", params={"token": token, "state": state}).status_code == 302
    )
    client.cookies.set("nomal_auth", client.cookies.get("nomal_auth") or "", path="/api/auth")
    state2 = start_login(client)
    replay = client.get("/api/auth/callback", params={"token": token, "state": state2})
    assert replay.status_code == 400


def test_state_must_match_cookie_and_token(client: TestClient) -> None:
    state = start_login(client)
    wrong_cookie = client.get(
        "/api/auth/callback", params={"token": make_token(PLAYER, "other"), "state": "other"}
    )
    assert wrong_cookie.status_code == 400
    wrong_token_state = client.get(
        "/api/auth/callback", params={"token": make_token(PLAYER, "other"), "state": state}
    )
    assert wrong_token_state.status_code == 400
    assert "(state)" in wrong_token_state.text


def test_foreign_signature_is_refused(client: TestClient) -> None:
    state = start_login(client)
    token = make_token(PLAYER, state, private_pem=OTHER_PRIVATE_PEM)
    response = client.get("/api/auth/callback", params={"token": token, "state": state})
    assert response.status_code == 400
    assert client.get("/api/me").status_code == 401


def test_logout_ends_the_session(client: TestClient) -> None:
    login(client, PLAYER)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_cross_origin_writes_are_refused(client: TestClient) -> None:
    login(client, PLAYER)
    response = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.post("/api/auth/logout", headers={"Origin": PUBLIC_URL}).status_code == 204


def test_expired_sessions_are_refused(client: TestClient, db_engine: Engine) -> None:
    login(client, PLAYER)
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE user_session SET expires_at = :t"),
            {"t": datetime.now(UTC) - timedelta(minutes=1)},
        )
    assert client.get("/api/me").status_code == 401


def test_login_is_audited(client: TestClient, db_engine: Engine) -> None:
    login(client, PLAYER)
    with db_engine.connect() as conn:
        actions = conn.execute(text("SELECT action, actor_member_id FROM audit_log")).all()
    assert ("login", PLAYER["mid"]) in [tuple(a) for a in actions]


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_spa_routes_answer_get_and_head(tmp_path, settings) -> None:  # type: ignore[no-untyped-def]
    from nomal.api.app import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>WiePlay</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app = create_app(settings.model_copy(update={"web_dist": str(tmp_path)}))
    with TestClient(app) as c:
        page = c.get("/teams/9101")
        assert page.status_code == 200
        assert "content-security-policy" in page.headers
        assert c.head("/teams/9101").status_code == 200
        asset = c.get("/assets/app.js")
        assert asset.headers["cache-control"].endswith("immutable")
        assert c.get("/../../etc/passwd").text.startswith("<!doctype html>")
        assert c.get("/api/unknown").status_code == 404

"""Guest clubs (PLAN §4.5): an admin's guest team, its invite links and logins, its people,
the wall between guest clubs and KSCW, the adults declaration, the video limit, the training
data, and that the Wiedisync sync never touches a guest row."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from nomal.api.dev_bridge import FixtureBridgeClient
from nomal.core import actions, analysis, guests, recap
from nomal.core.models import (
    AnalysisArtifact,
    Job,
    ModelRun,
    Segment,
    TeamLineage,
    Video,
    WdTeam,
)
from nomal.core.sync import _lineage_for
from tests.fakes import FakeStore
from tests.media_helpers import (
    H3,
    MEDIA_CAPS,
    TODAY,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)

PASSWORD = "correct horse battery"
WRONG = "Wrong email or password"


@pytest.fixture
def new_client(app: FastAPI) -> Iterator[Any]:
    """Logged-out browsers (each with its own cookies)."""
    clients: list[TestClient] = []

    def make() -> TestClient:
        c = TestClient(app, follow_redirects=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def token_of(link: str) -> str:
    assert link.startswith("http://testserver/join/"), link
    return link.rsplit("/", 1)[1]


def accept(client: TestClient, link: str, email: str | None, password: str = PASSWORD) -> Any:
    body: dict[str, Any] = {"password": password, "accept_privacy": True}
    if email is not None:
        body["email"] = email
    return client.post(f"/api/auth/guest/invite/{token_of(link)}", json=body)


def audit_rows(db: Engine, action: str) -> list[Any]:
    with db.connect() as conn:
        return list(
            conn.execute(
                text("SELECT actor_member_id, object_id, details FROM audit_log WHERE action = :a"),
                {"a": action},
            )
        )


def sql(db: Engine, statement: str, **params: Any) -> list[Any]:
    with db.begin() as conn:
        result = conn.execute(text(statement), params)
        return list(result) if result.returns_rows else []


@pytest.fixture
def gast(club: dict[str, TestClient], new_client: Any) -> dict[str, Any]:
    """A guest club's team, its coach logged in from the admin's invite link, and one player
    on the roster (no account yet)."""
    admin = club["admin"]
    created = admin.post(
        "/api/admin/guest-teams",
        json={"club": "VBC Gast", "name": "Herren 2", "league": "3. Liga", "training_ok": False},
    )
    assert created.status_code == 201, created.text
    team = created.json()
    invited = admin.post(
        f"/api/admin/guest-teams/{team['id']}/coaches", json={"name": "Gina Gast", "number": None}
    )
    assert invited.status_code == 201, invited.text
    coach = new_client()
    joined = accept(coach, invited.json()["link"], "Gina@Example.org ")
    assert joined.status_code == 200, joined.text
    assert joined.json() == {"next": f"/teams/{team['id']}"}
    player = coach.post(
        f"/api/teams/{team['id']}/people", json={"name": "Paul Gast", "number": 7, "role": "player"}
    )
    assert player.status_code == 201, player.text
    return {
        "team": team,
        "id": team["id"],
        "coach": coach,
        "coach_id": invited.json()["person"]["member_id"],
        "player_id": player.json()["member_id"],
    }


def join_player(gast: dict[str, Any], new_client: Any, email: str = "paul@example.org") -> Any:
    link = gast["coach"].post(f"/api/teams/{gast['id']}/people/{gast['player_id']}/invite")
    assert link.status_code == 200, link.text
    player = new_client()
    assert accept(player, link.json()["link"], email).status_code == 200
    return player


def declare(gast: dict[str, Any]) -> None:
    response = gast["coach"].post(f"/api/teams/{gast['id']}/adults")
    assert response.status_code == 200, response.text


def create_guest_video(client: TestClient, team_id: int, **extra: Any) -> Any:
    body = {
        "recorded_on": (TODAY - timedelta(days=1)).isoformat(),
        "title": "Friendly vs Volero",
        "files": [{"name": "C0001.MP4", "size": 1000, "type": "video/mp4"}],
        **extra,
    }
    return client.post(f"/api/teams/{team_id}/videos", json=body)


# --- admin and invites ------------------------------------------------------------------------


def test_admin_creates_a_guest_team_and_invites_its_coach(
    club: dict[str, TestClient], gast: dict[str, Any], db_engine: Engine
) -> None:
    admin = club["admin"]
    [listed] = admin.get("/api/admin/guest-teams").json()
    assert listed["club"] == "VBC Gast" and listed["name"] == "Herren 2"
    assert listed["id"] >= guests.GUEST_ID_BASE and listed["enabled"] is True
    assert (listed["video_limit"], listed["videos"], listed["people"], listed["accounts"]) == (
        30, 0, 2, 1,
    )  # fmt: skip
    assert listed["training_ok"] is False and listed["adults_declared_at"] is None
    # The season defaults to the current one; the same team twice is refused.
    assert listed["season"] == club["admin"].get(f"/api/teams/{H3}").json()["season"]
    again = admin.post("/api/admin/guest-teams", json={"club": "vbc gast", "name": "herren 2"})
    assert again.status_code == 409
    basketball = {"club": "X", "name": "Y", "sport": "basketball"}  # volleyball only for now
    assert admin.post("/api/admin/guest-teams", json=basketball).status_code == 422
    # Not among Wiedisync's teams, and not switched there.
    assert gast["id"] not in {t["id"] for t in admin.get("/api/admin/teams").json()}
    assert admin.put(f"/api/admin/teams/{gast['id']}", json={"enabled": False}).status_code == 404
    # Coaches and players cannot reach the admin section.
    assert gast["coach"].get("/api/admin/guest-teams").status_code == 403
    kscw_coach = club["coach"].post("/api/admin/guest-teams", json={"club": "A", "name": "B"})
    assert kscw_coach.status_code == 403

    assert [r[2]["club"] for r in audit_rows(db_engine, "guest_team_created")] == ["VBC Gast"]
    assert len(audit_rows(db_engine, "guest_invite_created")) == 1
    [(actor, _, details)] = audit_rows(db_engine, "guest_invite_accepted")
    assert actor == gast["coach_id"] and details["new_account"] is True
    # The email is kept lower case and trimmed; the token only as a hash.
    [(email,)] = sql(db_engine, "SELECT email FROM guest_account")
    assert email == "gina@example.org"
    [(token_hash,)] = sql(db_engine, "SELECT token_hash FROM guest_invite")
    assert len(token_hash) == 64


def test_the_admin_edits_limit_training_and_switches_the_team_off(
    club: dict[str, TestClient], gast: dict[str, Any], db_engine: Engine
) -> None:
    admin, coach = club["admin"], gast["coach"]
    url = f"/api/admin/guest-teams/{gast['id']}"
    changed = admin.put(url, json={"video_limit": 12, "training_ok": True}).json()
    assert (changed["video_limit"], changed["training_ok"]) == (12, True)
    assert coach.get(f"/api/teams/{gast['id']}").json()["video_limit"] == 12

    # Off: its people keep their session but see nothing; invite links stop working.
    link = coach.post(f"/api/teams/{gast['id']}/people/{gast['player_id']}/invite").json()["link"]
    assert admin.put(url, json={"enabled": False}).json()["enabled"] is False
    assert coach.get(f"/api/teams/{gast['id']}").status_code == 404
    assert coach.get("/api/me").json()["teams"] == []
    assert coach.get(f"/api/auth/guest/invite/{token_of(link)}").status_code == 410
    assert admin.get(f"/api/teams/{gast['id']}").status_code == 404
    assert admin.put(url, json={"enabled": True}).json()["enabled"] is True
    assert coach.get(f"/api/teams/{gast['id']}").status_code == 200
    actions_logged = [
        r[0] for r in sql(db_engine, "SELECT action FROM audit_log WHERE action LIKE 'guest_team%'")
    ]
    assert actions_logged == [
        "guest_team_created", "guest_team_updated", "guest_team_disabled", "guest_team_enabled",
    ]  # fmt: skip


def test_an_invite_link_sets_up_an_account_once(
    gast: dict[str, Any], new_client: Any, db_engine: Engine
) -> None:
    coach = gast["coach"]
    url = f"/api/teams/{gast['id']}/people/{gast['player_id']}/invite"
    made = coach.post(url).json()
    expires = datetime.fromisoformat(made["expires_at"])
    assert timedelta(days=13, hours=23) < expires - datetime.now(UTC) <= timedelta(days=14)
    token = token_of(made["link"])

    info = new_client().get(f"/api/auth/guest/invite/{token}")
    assert info.status_code == 200
    assert info.json() == {
        "club": "VBC Gast", "team": "Herren 2", "team_id": gast["id"],
        "season": gast["team"]["season"], "sport": "volleyball", "name": "Paul Gast",
        "role": "player", "has_account": False,
    }  # fmt: skip
    assert info.headers["cache-control"] == "no-store"

    browser = new_client()
    body = {"email": "paul@example.org", "password": PASSWORD}
    post = f"/api/auth/guest/invite/{token}"
    assert browser.post(post, json={**body, "accept_privacy": False}).status_code == 422
    assert browser.post(post, json=body).status_code == 422  # the privacy page must be accepted
    short = {**body, "password": "too short", "accept_privacy": True}
    assert browser.post(post, json=short).status_code == 422
    no_email = {"password": PASSWORD, "accept_privacy": True}
    assert browser.post(post, json=no_email).status_code == 422
    bad_email = {**body, "email": "paul", "accept_privacy": True}
    assert browser.post(post, json=bad_email).status_code == 422
    taken = {**body, "email": "GINA@example.org", "accept_privacy": True}
    assert browser.post(post, json=taken).status_code == 409

    done = browser.post(post, json={**body, "accept_privacy": True})
    assert done.status_code == 200 and done.json() == {"next": f"/teams/{gast['id']}"}
    me = browser.get("/api/me").json()
    assert me["guest"] is True and me["club"] == "VBC Gast" and me["member"]["name"] == "Paul Gast"
    assert [t["id"] for t in me["teams"]] == [gast["id"]]
    # Used: gone. Unknown: not found. A guest session lasts 14 days.
    assert browser.post(post, json={**body, "accept_privacy": True}).status_code == 410
    assert new_client().get(f"/api/auth/guest/invite/{token}").status_code == 410
    assert new_client().get("/api/auth/guest/invite/nope").status_code == 404
    [(lasts,)] = sql(
        db_engine,
        "SELECT expires_at - created_at FROM user_session WHERE member_id = :m",
        m=gast["player_id"],
    )
    assert lasts == timedelta(days=14)


def test_a_new_link_resets_the_password_and_old_links_expire(
    gast: dict[str, Any], new_client: Any, db_engine: Engine
) -> None:
    coach = gast["coach"]
    url = f"/api/teams/{gast['id']}/people/{gast['coach_id']}/invite"
    older = coach.post(url).json()["link"]
    newer = coach.post(url).json()["link"]
    assert new_client().get(f"/api/auth/guest/invite/{token_of(older)}").status_code == 410
    info = new_client().get(f"/api/auth/guest/invite/{token_of(newer)}").json()
    assert info["has_account"] is True and info["role"] == "coach"
    # No email asked: the account keeps its own. A new password logs out the old sessions.
    browser = new_client()
    assert accept(browser, newer, None, password="a brand new password").status_code == 200
    assert coach.get("/api/me").status_code == 401
    assert browser.get("/api/me").json()["member"]["name"] == "Gina Gast"
    coach = browser
    login = new_client()
    old = {"email": "gina@example.org", "password": PASSWORD}
    assert login.post("/api/auth/guest/login", json=old).status_code == 401
    new = {"email": "gina@example.org", "password": "a brand new password"}
    assert login.post("/api/auth/guest/login", json=new).status_code == 204
    [(_, _, details)] = [r for r in audit_rows(db_engine, "guest_invite_accepted") if
                         r[0] == gast["coach_id"] and not r[2]["new_account"]]  # fmt: skip
    assert details["new_account"] is False

    # An expired link is gone too.
    expired = coach.post(url).json()["link"]
    sql(db_engine, "UPDATE guest_invite SET expires_at = now() - interval '1 minute'")
    assert new_client().get(f"/api/auth/guest/invite/{token_of(expired)}").status_code == 410
    assert accept(new_client(), expired, None).status_code == 410


# --- login ------------------------------------------------------------------------------------


def test_guest_login_wrong_unknown_locked_disabled(
    gast: dict[str, Any], new_client: Any, db_engine: Engine
) -> None:
    def login(email: str, password: str) -> Any:
        return new_client().post(
            "/api/auth/guest/login", json={"email": email, "password": password}
        )

    good = new_client()
    ok = good.post(
        "/api/auth/guest/login", json={"email": " GINA@example.org", "password": PASSWORD}
    )
    assert ok.status_code == 204 and "nomal_session" in ok.cookies
    assert good.get("/api/me").json()["club"] == "VBC Gast"

    wrong, unknown = login("gina@example.org", "not the password"), login("x@y.org", PASSWORD)
    assert (wrong.status_code, unknown.status_code) == (401, 401)
    assert wrong.json() == unknown.json() == {"detail": WRONG}
    # Logged for a known account only: a flood of made-up emails does not fill the log.
    assert [r[1] for r in audit_rows(db_engine, "guest_login_failed")] == [str(gast["coach_id"])]
    assert [r[0] for r in audit_rows(db_engine, "guest_login")].count(gast["coach_id"]) == 2

    # Ten wrong in a row lock the account for 15 minutes, the right password included.
    for _ in range(9):
        assert login("gina@example.org", "not the password").status_code == 401
    assert login("gina@example.org", PASSWORD).status_code == 401
    [(locked,)] = sql(db_engine, "SELECT locked_until FROM guest_account WHERE email = :e",
                      e="gina@example.org")  # fmt: skip
    assert timedelta(minutes=14) < locked - datetime.now(UTC) <= timedelta(minutes=15)
    sql(db_engine, "UPDATE guest_account SET locked_until = now() - interval '1 second'")
    assert login("gina@example.org", PASSWORD).status_code == 204

    # Disabled: no login, and running sessions end.
    sql(db_engine, "UPDATE guest_account SET disabled_at = now()")
    assert login("gina@example.org", PASSWORD).json() == {"detail": WRONG}
    assert good.get("/api/me").status_code == 401
    assert gast["coach"].get("/api/me").status_code == 401


def test_a_wiedisync_login_never_carries_guest_ids(
    gast: dict[str, Any], as_: Any, new_client: Any
) -> None:
    """A forged or wrong bridge token cannot open a guest team, nor log in as a guest."""
    from tests.conftest import make_token, start_login
    from tests.media_helpers import P

    browser = new_client()
    claims = {**P["player-h3"], "teams": [{"team": gast["id"], "season": "x", "rel": ["coach"]}]}
    state = start_login(browser)
    browser.get("/api/auth/callback", params={"token": make_token(claims, state), "state": state})
    assert browser.get(f"/api/teams/{gast['id']}").status_code == 404
    assert gast["id"] not in {t["id"] for t in browser.get("/api/teams").json()}

    other = new_client()
    as_guest = {**P["player-h3"], "mid": gast["coach_id"], "guest": True}
    state = start_login(other)
    refused = other.get(
        "/api/auth/callback", params={"token": make_token(as_guest, state), "state": state}
    )
    assert refused.status_code == 400 and "(member)" in refused.text


# --- people -----------------------------------------------------------------------------------


def test_coaches_keep_the_roster_and_players_read_it(
    gast: dict[str, Any], new_client: Any, club: dict[str, TestClient]
) -> None:
    coach, url = gast["coach"], f"/api/teams/{gast['id']}/people"
    people = coach.get(url).json()
    assert [(p["name"], p["role"], p["account"], p["me"]) for p in people] == [
        ("Gina Gast", "coach", True, True), ("Paul Gast", "player", False, False),
    ]  # fmt: skip
    assert people[0]["last_login_at"] is not None

    assert coach.post(url, json={"name": "Pia Gast", "number": 7}).status_code == 409  # taken
    pia = coach.post(url, json={"name": " Pia Gast ", "number": 9}).json()
    assert (pia["name"], pia["number"], pia["role"]) == ("Pia Gast", 9, "player")
    assert coach.post(url, json={"name": "", "number": 1}).status_code == 422
    formula = {"name": '=HYPERLINK("http://x")'}  # names go into CSVs and watermarks
    assert coach.post(url, json=formula).status_code == 422
    assert coach.post(url, json={"name": "X", "role": "captain"}).status_code == 422

    edited = coach.patch(f"{url}/{pia['member_id']}", json={"number": None, "name": "Pia G."})
    assert (edited.json()["name"], edited.json()["number"]) == ("Pia G.", None)
    assert coach.patch(f"{url}/{H3}", json={"name": "x"}).status_code == 404
    assert coach.patch(f"{url}/101", json={"name": "x"}).status_code == 404  # a KSCW member

    player = join_player(gast, new_client)
    seen = player.get(url).json()
    assert {p["name"] for p in seen} == {"Gina Gast", "Paul Gast", "Pia G."}
    assert all(p["account"] is None and p["last_login_at"] is None for p in seen)
    assert player.post(url, json={"name": "Me Too"}).status_code == 403
    assert player.delete(f"{url}/{pia['member_id']}").status_code == 403
    assert player.post(f"{url}/{pia['member_id']}/invite").status_code == 403
    team = player.get(f"/api/teams/{gast['id']}").json()
    assert team["can_manage_people"] is False and team["can_upload"] is False
    # A player of the team, as at KSCW: tags and reviews, shares and downloads nothing.
    assert (team["can_tag"], team["can_share"], team["can_export"]) == (True, False, False)
    assert team["guest"] is True and team["club"] == "VBC Gast"

    # Admins manage a guest team's people too (and re-invite: a password reset link).
    admin = club["admin"]
    assert admin.get(url).json()[0]["account"] is True
    assert admin.post(f"{url}/{gast['coach_id']}/invite").status_code == 200
    # A KSCW team's roster is Wiedisync's: not here.
    assert club["coach"].get(f"/api/teams/{H3}/people").status_code == 404


def test_a_removed_person_loses_the_team_at_once(
    gast: dict[str, Any], new_client: Any, db_engine: Engine
) -> None:
    coach, url = gast["coach"], f"/api/teams/{gast['id']}/people"
    player = join_player(gast, new_client)
    assert player.get(f"/api/teams/{gast['id']}").status_code == 200

    assert coach.delete(f"{url}/{gast['coach_id']}").status_code == 409  # not yourself
    assert coach.delete(f"{url}/{gast['player_id']}").status_code == 204
    assert coach.delete(f"{url}/{gast['player_id']}").status_code == 404
    # On no other team: the account is disabled and the running session ends.
    assert player.get(f"/api/teams/{gast['id']}").status_code == 401
    assert player.get("/api/me").status_code == 401
    [(disabled,)] = sql(
        db_engine, "SELECT disabled_at FROM guest_account WHERE member_id = :m",
        m=gast["player_id"],
    )  # fmt: skip
    assert disabled is not None
    login = {"email": "paul@example.org", "password": PASSWORD}
    assert new_client().post("/api/auth/guest/login", json=login).status_code == 401
    [(_, _, details)] = audit_rows(db_engine, "guest_person_removed")
    assert details == {"member_id": gast["player_id"], "account_disabled": True}
    # Their name leaves Nomal too, as for a KSCW member who leaves.
    assert sql(db_engine, "SELECT id FROM wd_member WHERE id = :m", m=gast["player_id"]) == []


# --- the wall between guest clubs and KSCW ----------------------------------------------------


def test_kscw_members_never_see_a_guest_team(
    app: FastAPI, gast: dict[str, Any], club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    declare(gast)
    video = upload_all(gast["coach"], store, create_guest_video(gast["coach"], gast["id"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    team, vid = gast["id"], video["id"]
    for who in ("coach-h3", "player-h3", "vb-sport-admin", "player-d1"):
        kscw = as_(who)
        assert team not in {t["id"] for t in kscw.get("/api/teams").json()}, who
        assert team not in {t["id"] for t in kscw.get("/api/me").json()["teams"]}, who
        for path in (
            f"/api/teams/{team}",
            f"/api/teams/{team}/videos",
            f"/api/teams/{team}/people",
            f"/api/teams/{team}/stats",
            f"/api/videos/{vid}",
            f"/api/videos/{vid}/tagging",
            f"/api/videos/{vid}/people",
            f"/api/videos/{vid}/comments",
            f"/api/media/{vid}/master.m3u8",
        ):
            assert kscw.get(path).status_code == 404, (who, path)
        assert kscw.post(f"/api/teams/{team}/people", json={"name": "x"}).status_code == 404

    # Full admins see it, marked with its club.
    admin = club["admin"]
    listed = {t["id"]: t for t in admin.get("/api/teams").json()}
    assert listed[team]["club"] == "VBC Gast" and listed[H3]["club"] is None
    assert admin.get(f"/api/videos/{vid}").status_code == 200


def test_guests_see_only_their_own_team(
    gast: dict[str, Any], club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = gast["coach"]
    kscw_video = create_match(club["coach"]).json()
    me = coach.get("/api/me").json()
    assert (me["guest"], me["club"], me["is_admin"], me["roles"]) == (True, "VBC Gast", False, [])
    assert [t["id"] for t in me["teams"]] == [gast["id"]]
    assert [t["id"] for t in coach.get("/api/teams").json()] == [gast["id"]]
    for path in (
        f"/api/teams/{H3}",
        f"/api/teams/{H3}/videos",
        f"/api/teams/{H3}/games",
        f"/api/videos/{kscw_video['id']}",
        f"/api/teams/{H3}/helpers",
    ):
        assert coach.get(path).status_code in (403, 404), path
    assert coach.get("/api/inbox").status_code == 403
    assert coach.get("/api/admin/teams").status_code == 403
    assert coach.post("/api/admin/sync").status_code == 403
    # No helpers on guest teams; the helper search is closed to guests.
    assert coach.get(f"/api/teams/{gast['id']}/helpers").status_code == 403
    assert coach.get(f"/api/teams/{gast['id']}/helpers/candidates?q=pia").status_code == 403
    detail = coach.get(f"/api/teams/{gast['id']}").json()
    assert (detail["can_grant"], detail["can_upload"], detail["can_manage_people"]) == (
        False, True, True,
    )  # fmt: skip
    assert club["admin"].get(f"/api/teams/{gast['id']}/helpers").status_code == 403
    # No Wiedisync games: an empty list, and nothing to film for.
    assert coach.get(f"/api/teams/{gast['id']}/games").json() == []
    assert coach.get(f"/api/teams/{gast['id']}/upcoming-filming").json() == []


def test_the_helper_search_never_lists_guest_people(
    gast: dict[str, Any], club: dict[str, TestClient]
) -> None:
    coach = club["coach"]
    gast["coach"].post(f"/api/teams/{gast['id']}/people", json={"name": "Pia Gast"})
    found = coach.get(f"/api/teams/{H3}/helpers/candidates", params={"q": "pia"}).json()
    assert [c["name"] for c in found] == ["Pia Helfer"]
    assert coach.get(f"/api/teams/{H3}/helpers/candidates", params={"q": "gast"}).json() == []
    guest_member = {"member_id": gast["player_id"]}
    assert coach.post(f"/api/teams/{H3}/helpers", json=guest_member).status_code == 404


# --- filming, limit, imports ------------------------------------------------------------------


class SpyBridge(FixtureBridgeClient):
    calls: list[tuple[str, Any]] = []  # noqa: RUF012

    def snapshot(self, team_ids: Any) -> Any:
        SpyBridge.calls.append(("snapshot", list(team_ids)))
        return super().snapshot(team_ids)

    def adult_check(self, team_id: int, on: date) -> Any:
        SpyBridge.calls.append(("adult_check", team_id))
        return super().adult_check(team_id, on)


def test_uploads_need_the_adults_declaration_and_never_ask_wiedisync(
    app: FastAPI, gast: dict[str, Any], store: FakeStore, db_engine: Engine
) -> None:
    app.state.bridge_client_factory = SpyBridge
    SpyBridge.calls = []
    coach, team = gast["coach"], gast["id"]
    on = (TODAY - timedelta(days=1)).isoformat()
    blocked = create_guest_video(coach, team)
    assert blocked.status_code == 409
    assert [b["code"] for b in blocked.json()["blockers"]] == ["adults_not_declared"]
    filming = coach.get(f"/api/teams/{team}/filming", params={"on": on}).json()
    assert filming["allowed"] is False

    # Only the guest club's own coaches make the statement; players and admins cannot.
    assert coach.get(f"/api/teams/{team}").json()["adults_declared_at"] is None
    declare(gast)
    assert coach.get(f"/api/teams/{team}").json()["adults_declared_at"] is not None
    assert coach.get(f"/api/teams/{team}/filming", params={"on": on}).json()["allowed"] is True
    video = create_guest_video(coach, team)
    assert video.status_code == 201, video.text
    upload_all(coach, store, video.json())  # the last file checks the rules again
    assert SpyBridge.calls == []  # neither the roster sync nor the adult check
    [(actor, _, details)] = audit_rows(db_engine, "guest_adults_declared")
    assert actor == gast["coach_id"] and details["team_id"] == team


def test_only_the_guest_coach_declares(
    gast: dict[str, Any], new_client: Any, club: dict[str, TestClient]
) -> None:
    player = join_player(gast, new_client)
    assert player.post(f"/api/teams/{gast['id']}/adults").status_code == 403
    assert club["admin"].post(f"/api/teams/{gast['id']}/adults").status_code == 403
    assert club["coach"].post(f"/api/teams/{H3}/adults").status_code == 404


def test_the_video_limit(
    gast: dict[str, Any], club: dict[str, TestClient], db_engine: Engine
) -> None:
    coach, team = gast["coach"], gast["id"]
    declare(gast)
    club["admin"].put(f"/api/admin/guest-teams/{team}", json={"video_limit": 1})
    first = create_guest_video(coach, team)
    assert first.status_code == 201
    second = create_guest_video(coach, team)
    assert second.status_code == 409
    assert "1 of 1 videos" in second.json()["detail"]
    assert [r[2]["blockers"] for r in audit_rows(db_engine, "upload_refused")] == [["video_limit"]]
    detail = coach.get(f"/api/teams/{team}").json()
    assert (detail["video_count"], detail["video_limit"]) == (1, 1)
    # A deleted video frees its place.
    assert coach.delete(f"/api/videos/{first.json()['id']}").status_code == 204
    assert create_guest_video(coach, team).status_code == 201
    # No limit: no count.
    club["admin"].put(f"/api/admin/guest-teams/{team}", json={"video_limit": None})
    assert create_guest_video(coach, team).status_code == 201


def test_guests_upload_files_only(gast: dict[str, Any]) -> None:
    coach, team = gast["coach"], gast["id"]
    declare(gast)
    link = create_guest_video(
        coach, team, files=[], import_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert link.status_code == 403
    inbox = create_guest_video(coach, team, files=[], inbox_file_ids=[str(uuid.uuid4())])
    assert inbox.status_code == 403


def test_a_guest_export_carries_the_clubs_name(
    app: FastAPI, gast: dict[str, Any], club: dict[str, TestClient], store: FakeStore,
    db_engine: Engine,
) -> None:  # fmt: skip
    coach = gast["coach"]
    declare(gast)
    video = upload_all(coach, store, create_guest_video(coach, gast["id"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    ready = coach.get(f"/api/videos/{video['id']}").json()
    assert ready["status"] == "ready"
    assert (ready["names"]["us"], ready["names"]["them"]) == ("VBC Gast Herren 2", "Volero")
    clip_id = str(uuid.uuid4())
    clip = {"video_id": video["id"], "start_ms": 0, "end_ms": 4_000, "title": "Ace"}
    assert coach.put(f"/api/clips/{clip_id}", json=clip).status_code == 200
    export = coach.post("/api/exports", json={"kind": "clip", "clip_id": clip_id})
    assert export.status_code == 201, export.text
    [(watermark,)] = sql(db_engine, "SELECT params->>'watermark' FROM job WHERE kind = 'export'")
    assert watermark.startswith("VBC Gast intern · Gina Gast · ")
    # Its originals live in a folder of their own on the NAS.
    [(raw,)] = sql(db_engine, "SELECT params->>'raw_subdir' FROM job WHERE kind = 'ingest'")
    assert "/vb-guest-VBC-Gast-Herren-2/" in raw
    # The game recap's footer names the guest club, not KSC Wiedikon.
    with app.state.sessionmaker() as db:
        row = db.get(Video, uuid.UUID(video["id"]))
        assert row is not None
        assert recap.card(db, row, "en")[0].footer == "Best plays · WiePlay · VBC Gast"


# --- the Wiedisync sync -----------------------------------------------------------------------

GUEST_ROWS = {
    "team_lineage": "SELECT * FROM team_lineage WHERE guest_club IS NOT NULL ORDER BY id",
    "wd_team": "SELECT * FROM wd_team WHERE guest ORDER BY id",
    "wd_member": "SELECT * FROM wd_member WHERE id >= 1000000000 ORDER BY id",
    "wd_team_member": (
        "SELECT * FROM wd_team_member WHERE team_id >= 1000000000 ORDER BY member_id, rel"
    ),
    "guest_account": "SELECT * FROM guest_account ORDER BY member_id",
    "video": "SELECT id, team_id, status FROM video ORDER BY id",
}


def guest_rows(db: Engine) -> dict[str, list[Any]]:
    return {name: [tuple(r) for r in sql(db, query)] for name, query in GUEST_ROWS.items()}


def test_the_sync_leaves_every_guest_row_alone(
    gast: dict[str, Any], club: dict[str, TestClient], db_engine: Engine
) -> None:
    admin = club["admin"]
    declare(gast)
    assert create_guest_video(gast["coach"], gast["id"]).status_code == 201
    before = guest_rows(db_engine)
    assert len(before["wd_member"]) == 2 and len(before["wd_team_member"]) == 2
    assert admin.post("/api/admin/sync").status_code == 200
    # A KSCW team switched off: its people are purged, the guests' stay.
    assert admin.put(f"/api/admin/teams/{H3}", json={"enabled": False}).status_code == 200
    assert admin.post("/api/admin/sync").status_code == 200
    assert guest_rows(db_engine) == before
    assert gast["coach"].get(f"/api/teams/{gast['id']}").status_code == 200
    assert len(gast["coach"].get(f"/api/teams/{gast['id']}/people").json()) == 2


def test_a_wiedisync_team_never_joins_a_guest_lineage(gast: dict[str, Any], app: FastAPI) -> None:
    """A Wiedisync team without a key is matched by name: never to a guest club's team of the
    same name."""
    with app.state.sessionmaker() as db:
        guest_lineage = db.get(WdTeam, gast["id"]).lineage  # type: ignore[union-attr]
        found = _lineage_for(
            db, {"id": 4242, "sport": "volleyball", "name": "Herren 2", "team_key": None}
        )
        assert found.id != guest_lineage.id and found.guest_club is None
        db.rollback()


# --- training data ----------------------------------------------------------------------------


def _reviewed_video(db: Session, team: WdTeam, run: ModelRun) -> Video:
    now = datetime.now(UTC)
    video = Video(
        id=uuid.uuid4(), team_id=team.id, lineage_id=team.lineage_id, kind="match",
        title=f"{team.name} match", sport="volleyball", recorded_on=TODAY, status="ready",
        created_by=1, rallies_reviewed_at=now, actions_reviewed_at=now,
    )  # fmt: skip
    db.add(video)
    db.flush()
    db.add(
        AnalysisArtifact(
            video_id=video.id,
            model_run_id=run.id,
            kind="features",
            nas_path=f"v/{video.id}.npz",
            format="npz",
            sha256="a" * 64,
            bytes=1,
        )
    )
    db.add(
        Segment(
            id=uuid.uuid4(),
            video_id=video.id,
            kind="rally",
            start_ms=1_000,
            end_ms=9_000,
            source="manual",
            review="unreviewed",
        )
    )
    db.flush()
    return video


def test_guest_games_train_the_models_only_with_the_clubs_ok(
    gast: dict[str, Any], club: dict[str, TestClient], app: FastAPI
) -> None:
    with app.state.sessionmaker() as db:
        run = ModelRun(kind="analyse", model_name="test", model_version="t")
        db.add(run)
        db.flush()
        ours = _reviewed_video(db, db.get(WdTeam, H3), run)  # type: ignore[arg-type]
        theirs = _reviewed_video(db, db.get(WdTeam, gast["id"]), run)  # type: ignore[arg-type]
        db.commit()

        def rallies() -> tuple[set[str], dict[str, str]]:
            found = analysis.job_inputs(db, Job(kind="train_rallies"))
            return {e["video_id"] for e in found["train"]}, {
                e["video_id"]: e["reason"] for e in found["skipped"]
            }

        def actions_skipped() -> dict[str, str]:
            found = actions.job_inputs(db, Job(kind="train_actions"))
            return {e["video_id"]: e["reason"] for e in found["skipped"]}

        train, skipped = rallies()
        assert train == {str(ours.id)}
        assert skipped == {str(theirs.id): "guest club, no training"}
        assert actions_skipped().get(str(theirs.id)) == "guest club, no training"
        assert actions_skipped()[str(ours.id)] == "no tags"  # ours goes on: nothing to learn yet

        lineage = db.get(TeamLineage, theirs.lineage_id)
        assert lineage is not None
        lineage.guest_training_ok = True
        db.commit()
        train, skipped = rallies()
        assert train == {str(ours.id), str(theirs.id)} and skipped == {}
        assert actions_skipped()[str(theirs.id)] == "no tags"


# --- hardening ---------------------------------------------------------------------------------


def test_a_burst_of_logins_gets_busy_not_every_thread(
    gast: dict[str, Any], new_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guests, "HASH_WAIT_SECONDS", 0.05)
    held = [guests._HASHING.acquire(timeout=1) for _ in range(4)]  # every slot hashing
    try:
        login = {"email": "gina@example.org", "password": PASSWORD}
        busy = new_client().post("/api/auth/guest/login", json=login)
        assert busy.status_code == 429 and busy.headers["retry-after"] == "60"
    finally:
        for taken in held:
            if taken:
                guests._HASHING.release()
    login = {"email": "gina@example.org", "password": PASSWORD}
    assert new_client().post("/api/auth/guest/login", json=login).status_code == 204


def test_guests_see_no_worker_error_text() -> None:
    from nomal.core.permissions import Viewer

    kscw = Viewer(1, "u", "Jo", frozenset(), frozenset(), {})
    gast = Viewer(guests.GUEST_ID_BASE + 1, "g", "Gina", frozenset(), frozenset(), {}, guest=True)
    raw = "OSError: /mnt/nas/nomal/raw/2026-27/vb-H3/x.mp4: No such file"
    assert guests.error_for(kscw, raw) == raw
    assert guests.error_for(gast, raw) == guests.PROCESSING_FAILED
    assert guests.error_for(gast, None) is None

"""Team helpers (PLAN §5.1 "analyst"): who picks them, the member search, what a helper may
do, the audit log, and that a removed helper loses the team at once."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import (
    D1,
    H3,
    HERREN1,
    MEDIA_CAPS,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)

H3_PREV = 9001
PIA, LIA, JO, ALEX = 111, 110, 106, 101  # D1 player, DU23 player (under 18), H3 coach, H3 player


def helpers_url(team: int = H3) -> str:
    return f"/api/teams/{team}/helpers"


def add(client: TestClient, member: int, team: int = H3, **body: Any) -> Any:
    return client.post(helpers_url(team), json={"member_id": member, **body})


def audit_rows(db: Engine, action: str) -> list[Any]:
    with db.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT actor_member_id, object_type, details FROM audit_log WHERE action = :a"
                ),
                {"a": action},
            )
        )


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    """A processed H3 match video."""
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    return video


def test_who_may_pick_helpers(club: dict[str, TestClient], as_: Any) -> None:
    coach, admin = club["coach"], club["admin"]
    assert coach.get(helpers_url()).json() == []
    assert coach.get(helpers_url(H3_PREV)).status_code == 200  # any season of the team
    assert coach.get(helpers_url(HERREN1)).status_code == 200  # team manager there
    assert coach.get(helpers_url(D1)).status_code == 404  # not their team
    assert admin.get(helpers_url()).status_code == 200
    assert as_("vb-sport-admin").get(helpers_url()).status_code == 200
    assert as_("vb-sport-admin").get(helpers_url(HERREN1)).status_code == 404  # other sport
    player = as_("player-h3")
    assert player.get(helpers_url()).status_code == 403
    assert add(player, PIA).status_code == 403
    assert player.get(f"{helpers_url()}/candidates", params={"q": "pia"}).status_code == 403
    assert as_("player-d1").get(helpers_url()).status_code == 404  # cannot see H3 at all

    team = coach.get(f"/api/teams/{H3}").json()
    assert team["can_grant"] is True and team["helper"] is False
    assert player.get(f"/api/teams/{H3}").json()["can_grant"] is False


def test_member_search_shows_names_and_teams_only(
    club: dict[str, TestClient], db_engine: Engine
) -> None:
    coach = club["coach"]
    url = f"{helpers_url()}/candidates"
    found = coach.get(url, params={"q": "pia"}).json()
    assert found == [{"id": PIA, "name": "Pia Helfer", "teams": ["D1"]}]
    sam = coach.get(url, params={"q": "sam beisp"}).json()
    assert sam == [{"id": 102, "name": "Sam Beispiel", "teams": ["D1", "H3"]}]
    assert coach.get(url, params={"q": "p"}).json() == []  # at least 2 letters
    assert coach.get(url, params={"q": "%%"}).json() == []  # no wildcards
    assert coach.get(url, params={"q": "jo trainer"}).json() == []  # coaches need no grant
    # Only active members.
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE wd_member SET active = false WHERE id = :id"), {"id": PIA})
    assert coach.get(url, params={"q": "pia"}).json() == []
    assert add(coach, PIA).status_code == 404


def test_a_coach_adds_and_removes_a_helper(
    club: dict[str, TestClient], as_: Any, db_engine: Engine
) -> None:
    coach = club["coach"]
    pia = as_("player-d1")  # logged in before the grant: grants are read per request
    assert {t["id"] for t in pia.get("/api/me").json()["teams"]} == {D1}

    added = add(coach, PIA, note="Reviews the rallies of the home games")
    assert added.status_code == 201, added.text
    helper = added.json()
    assert helper["name"] == "Pia Helfer" and helper["teams"] == ["D1"]
    assert helper["granted_by_name"] == "Jo Trainer"
    assert helper["note"] == "Reviews the rallies of the home games"
    assert coach.get(helpers_url()).json() == [helper]
    assert coach.get(helpers_url(H3_PREV)).json() == [helper]  # one grant for the lineage
    assert coach.get(f"{helpers_url()}/candidates", params={"q": "pia"}).json() == []
    assert add(coach, PIA).status_code == 409  # already helping
    assert add(coach, JO).status_code == 409  # the coach
    assert add(coach, 999_999).status_code == 404

    [(actor, object_type, details)] = audit_rows(db_engine, "helper_added")
    assert actor == JO and object_type == "team_lineage"
    assert details["member_id"] == PIA and details["team_id"] == H3

    me = pia.get("/api/me").json()
    teams = {t["id"]: t for t in me["teams"]}
    assert set(teams) == {D1, H3, H3_PREV}  # every season of the team
    h3 = teams[H3]
    assert h3["helper"] is True and h3["rel"] == []
    assert (h3["can_tag"], h3["can_share"], h3["can_export"]) == (True, True, False)
    assert teams[D1]["helper"] is False and teams[D1]["rel"] == ["player"]
    detail = pia.get(f"/api/teams/{H3}").json()
    assert detail["can_upload"] is True and detail["can_grant"] is False
    assert pia.get(helpers_url()).status_code == 403  # helpers don't pick helpers

    # Removed: the same session loses the team on its next request.
    assert coach.delete(f"{helpers_url()}/{helper['id']}").status_code == 204
    assert coach.delete(f"{helpers_url()}/{helper['id']}").status_code == 404
    assert pia.get(f"/api/teams/{H3}").status_code == 404
    assert {t["id"] for t in pia.get("/api/teams").json()} == {D1}
    assert coach.get(helpers_url()).json() == []
    [(actor, _, details)] = audit_rows(db_engine, "helper_removed")
    assert actor == JO and details["grant_id"] == helper["id"]

    # Added again later: a new grant; the old one stays as history.
    assert add(coach, PIA).status_code == 201
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM team_grant")).scalar() == 2


def test_a_grant_from_another_team_cannot_be_removed_here(club: dict[str, TestClient]) -> None:
    coach = club["coach"]
    helper = add(coach, PIA, team=HERREN1).json()  # Jo manages Herren 1 too
    assert coach.delete(f"{helpers_url(H3)}/{helper['id']}").status_code == 404
    assert coach.delete(f"{helpers_url(H3)}/{uuid.uuid4()}").status_code == 404
    assert coach.get(helpers_url(HERREN1)).json()[0]["id"] == helper["id"]


def test_a_helper_tags_and_shares_but_neither_downloads_nor_deletes(
    ready: dict[str, Any], club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    coach = club["coach"]
    pia = as_("player-d1")
    assert pia.get(f"/api/videos/{ready['id']}").status_code == 404
    assert add(coach, PIA).status_code == 201

    video = pia.get(f"/api/videos/{ready['id']}").json()
    assert (video["can_tag"], video["can_share"]) == (True, True)
    assert (video["can_export"], video["can_delete"]) == (False, False)
    assert pia.get(f"/api/videos/{ready['id']}/tagging").json()["can_tag"] is True

    # Tag and mark a rally, correct the roster, mark the rallies reviewed.
    rally = {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": 1_000, "end_ms": 9_000,
             "won_by": "us"}  # fmt: skip
    serve = {"id": str(uuid.uuid4()), "code": "S", "t_ms": 1_200, "side": "us", "number": 7,
             "attributes": {"grade": "#"}}  # fmt: skip
    changes = pia.post(
        f"/api/videos/{ready['id']}/changes", json={"segments": [rally], "events": [serve]}
    )
    assert changes.status_code == 200, changes.text
    roster = [{"side": "us", "number": 7, "member_id": ALEX}, {"side": "them", "number": 12}]
    assert pia.put(f"/api/videos/{ready['id']}/roster", json={"entries": roster}).status_code == 200
    reviewed = pia.post(f"/api/videos/{ready['id']}/rallies-reviewed", json={"reviewed": True})
    assert reviewed.status_code == 200, reviewed.text

    # Share a clip with the team; no download, no delete.
    clip_id = str(uuid.uuid4())
    clip = {"video_id": ready["id"], "start_ms": 1_000, "end_ms": 6_000, "title": "Ace",
            "visibility": "team"}  # fmt: skip
    assert pia.put(f"/api/clips/{clip_id}", json=clip).status_code == 200
    assert pia.post("/api/exports", json={"kind": "clip", "clip_id": clip_id}).status_code == 403
    assert pia.delete(f"/api/videos/{ready['id']}").status_code == 403

    # Upload for the team; an unfinished upload of their own they may cancel, a coach's not.
    own = create_match(pia)
    assert own.status_code == 201, own.text
    theirs = create_match(coach).json()
    assert pia.delete(f"/api/videos/{theirs['id']}").status_code == 403
    assert pia.delete(f"/api/videos/{own.json()['id']}").status_code == 204


def test_a_disabled_team_hides_its_helpers(club: dict[str, TestClient], as_: Any) -> None:
    coach, admin = club["coach"], club["admin"]
    assert add(coach, PIA).status_code == 201
    pia = as_("player-d1")
    assert pia.get(f"/api/teams/{H3}").status_code == 200
    assert admin.put(f"/api/admin/teams/{H3}", json={"enabled": False}).status_code == 200
    assert pia.get(f"/api/teams/{H3}").status_code == 404
    assert {t["id"] for t in pia.get("/api/teams").json()} == {D1}
    assert coach.get(helpers_url()).status_code == 404
    assert admin.get(helpers_url()).status_code == 404


def test_helpers_are_viewers_for_the_filming_rules(club: dict[str, TestClient]) -> None:
    """The adult check covers the people filmed (the team's roster), not its helpers."""
    coach = club["coach"]
    assert add(coach, LIA).status_code == 201  # under 18, plays in DU23-1
    upcoming = coach.get(f"/api/teams/{H3}/upcoming-filming").json()
    assert upcoming and all(u["allowed"] for u in upcoming)

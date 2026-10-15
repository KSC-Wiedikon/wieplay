"""M2: tags, rallies, sets and the score; the roster snapshot; revisions (PLAN §6.6, §6.7, §9)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import MEDIA_CAPS, create_match, run_pipeline, upload_all, worker_client


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    """A processed H3 match video (game k=1: KSC away, official sets stored home/away)."""
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    return video


def tag(
    code: str, t_ms: int, side: str = "us", number: int | None = 7, **attrs: Any
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "code": code,
        "t_ms": t_ms,
        "side": side,
        "number": number,
        "attributes": attrs,
    }


def rally(start: int, end: int, won_by: str | None) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": "rally",
        "start_ms": start,
        "end_ms": end,
        "won_by": won_by,
    }


def test_the_workspace_bundle_and_roster_snapshot(
    ready: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    bundle = club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()
    assert bundle["can_tag"] is True and bundle["sport"] == "volleyball"
    assert [s["code"] for s in bundle["taxonomy"]["skills"]] == ["S", "R", "E", "A", "B", "D", "F"]
    us = [(r["number"], r["name"]) for r in bundle["roster"] if r["side"] == "us"]
    assert us == [(1, "Kim Probe"), (3, "Sam Beispiel"), (7, "Alex Muster"), (11, "Robin Test")]
    assert next(r for r in bundle["roster"] if r["number"] == 1)["is_libero"] is True
    # Game k=1: KSC played away, so the official [home, away] sets are turned into [us, them].
    assert bundle["official_sets"][0] == [20, 25]

    player = as_("player-h3").get(f"/api/videos/{ready['id']}/tagging").json()
    assert player["can_tag"] is False and len(player["roster"]) == 4
    assert as_("basketball-player").get(f"/api/videos/{ready['id']}/tagging").status_code == 404


def test_tags_make_rallies_and_the_score(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "start_us": 0, "start_them": 0,
              "first_serve": "us"}  # fmt: skip
    r1, r2, r3 = (
        rally(1_000, 10_000, "us"),
        rally(12_000, 20_000, "them"),
        rally(22_000, 30_000, "us"),
    )
    serve, kill = tag("S", 1_200), tag("A", 8_000, number=3, grade="#", zone_from=4)
    their_serve = tag("S", 11_500, side="them", number=12)
    body = {"periods": [period], "segments": [r1, r2, r3], "events": [serve, kill, their_serve]}
    out = club["coach"].post(f"/api/videos/{ready['id']}/changes", json=body)
    assert out.status_code == 200, out.text
    segs = {s["id"]: s for s in out.json()["segments"]}
    assert (segs[r1["id"]]["score_us_after"], segs[r1["id"]]["score_them_after"]) == (1, 0)
    assert segs[r1["id"]]["serving"] == "us"
    assert segs[r2["id"]]["serving"] == "them"  # their serve tag sits 0.5 s before the marker
    assert (segs[r2["id"]]["score_us_after"], segs[r2["id"]]["score_them_after"]) == (1, 1)
    assert segs[r3["id"]]["serving"] == "them"  # the previous rally's winner serves
    assert (segs[r3["id"]]["score_us_after"], segs[r3["id"]]["score_them_after"]) == (2, 1)
    assert out.json()["periods"][0]["final_us"] == 2
    events = {e["id"]: e for e in out.json()["events"]}
    assert events[kill["id"]]["member_id"] == 102  # #3 in the snapshot
    assert events[their_serve["id"]]["member_id"] is None

    # Correcting the first rally rescores the rest.
    r1["won_by"] = "them"
    again = club["coach"].post(f"/api/videos/{ready['id']}/changes", json={"segments": [r1]})
    segs = {s["id"]: s for s in again.json()["segments"]}
    assert (segs[r3["id"]]["score_us_after"], segs[r3["id"]]["score_them_after"]) == (1, 2)


def test_writes_are_idempotent_and_revisioned(
    ready: dict[str, Any], club: dict[str, TestClient], db_engine: Engine
) -> None:
    t = tag("A", 5_000, grade="+")
    url = f"/api/videos/{ready['id']}/changes"
    first = club["coach"].post(url, json={"events": [t]}).json()["events"][0]
    resent = club["coach"].post(url, json={"events": [t]}).json()["events"][0]
    assert first["version"] == resent["version"] == 1  # a replay changes nothing
    t["attributes"]["grade"] = "#"
    assert club["coach"].post(url, json={"events": [t]}).json()["events"][0]["version"] == 2
    t["deleted"] = True
    club["coach"].post(url, json={"events": [t]})
    bundle = club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()
    assert all(e["id"] != t["id"] for e in bundle["events"])
    revisions = club["coach"].get(f"/api/events/{t['id']}/revisions").json()
    assert [(r["version"], r["action"]) for r in revisions] == [
        (1, "create"),
        (2, "update"),
        (3, "delete"),
    ]
    assert revisions[0]["after"]["attributes"] == {"grade": "+"}
    assert {r["changed_by"] for r in revisions} == {106}  # the coach, via nomal.actor
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM event")).scalar() == 1  # soft delete


def test_single_put_and_validation(ready: dict[str, Any], club: dict[str, TestClient]) -> None:
    t = tag("S", 2_000, side="them", number=9, grade="=")
    put = club["coach"].put(f"/api/events/{t['id']}", json={**t, "video_id": ready["id"]})
    assert put.status_code == 200 and put.json()["version"] == 1
    url = f"/api/videos/{ready['id']}/changes"
    for bad in (
        tag("X", 1),
        tag("A", 1, grade="?"),
        tag("A", 1, zone_from=10),
        tag("A", 1, speed=3),
    ):
        assert club["coach"].post(url, json={"events": [bad]}).status_code == 422, bad
    too_late = tag("A", 10 * 3_600_000)
    assert club["coach"].post(url, json={"events": [too_late]}).status_code == 422
    backwards = {**rally(5_000, 4_000, None)}
    assert club["coach"].post(url, json={"segments": [backwards]}).status_code == 422


def test_who_may_tag(ready: dict[str, Any], club: dict[str, TestClient], as_: Any) -> None:
    url = f"/api/videos/{ready['id']}/changes"
    assert as_("player-h3").post(url, json={"events": [tag("A", 1)]}).status_code == 403
    assert club["admin"].post(url, json={"events": [tag("A", 1)]}).status_code == 403
    assert as_("vb-sport-admin").post(url, json={"events": [tag("A", 1)]}).status_code == 200


def test_tags_of_one_video_cannot_be_moved_to_another(
    app: FastAPI, ready: dict[str, Any], club: dict[str, TestClient], store: FakeStore
) -> None:
    t = tag("A", 1_000)
    club["coach"].post(f"/api/videos/{ready['id']}/changes", json={"events": [t]})
    other = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    moved = club["coach"].post(f"/api/videos/{other['id']}/changes", json={"events": [t]})
    assert moved.status_code == 422


def test_a_video_that_is_not_ready_cannot_be_tagged(
    club: dict[str, TestClient], store: FakeStore
) -> None:
    video = create_match(club["coach"]).json()
    r = club["coach"].post(f"/api/videos/{video['id']}/changes", json={"events": [tag("A", 1)]})
    assert r.status_code == 409


def test_the_roster_snapshot_can_change_and_tags_follow(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/changes"
    seven, seventeen = tag("A", 1_000, number=7), tag("A", 2_000, number=17)
    club["coach"].post(url, json={"events": [seven, seventeen]})
    roster = [
        {"side": "us", "number": 17, "member_id": 101},  # Alex wore 17 in this match
        {"side": "us", "number": 3, "member_id": 102},
        {"side": "them", "number": 12},
    ]
    r = club["coach"].put(f"/api/videos/{ready['id']}/roster", json={"entries": roster})
    assert r.status_code == 200, r.text
    events = {
        e["id"]: e for e in club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()["events"]
    }
    assert events[seven["id"]]["member_id"] is None
    assert events[seventeen["id"]]["member_id"] == 101
    bad = [{"side": "them", "number": 4, "member_id": 101}]
    assert (
        club["coach"].put(f"/api/videos/{ready['id']}/roster", json={"entries": bad}).status_code
        == 422
    )
    dup = [{"side": "us", "number": 4}, {"side": "us", "number": 4}]
    assert (
        club["coach"].put(f"/api/videos/{ready['id']}/roster", json={"entries": dup}).status_code
        == 422
    )


def test_the_taxonomy_table_is_seeded(club: dict[str, TestClient], db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT code, ends_rally_on FROM taxonomy WHERE sport = 'volleyball' ORDER BY code"
            )
        ).all()
    assert [r.code for r in rows] == ["A", "B", "D", "E", "F", "R", "S"]
    assert dict(rows)["A"] == {"#": "self", "=": "other", "/": "other"}
    assert club["coach"].get("/api/taxonomies/basketball").status_code == 404

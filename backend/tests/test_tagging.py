"""M2: tags, rallies, sets and the score; the roster snapshot; revisions (PLAN §6.6, §6.7, §9)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import (
    HERREN1,
    MEDIA_CAPS,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)


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
    r1["won_by"], r1["base_version"] = "them", 1
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
    t["attributes"]["grade"], t["base_version"] = "#", 1
    assert club["coach"].post(url, json={"events": [t]}).json()["events"][0]["version"] == 2
    t["deleted"], t["base_version"] = True, 2
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
    basketball = club["coach"].get("/api/taxonomies/basketball").json()
    assert [k["code"] for k in basketball["skills"]][:3] == ["S2", "S3", "FT"]
    assert club["coach"].get("/api/taxonomies/handball").status_code == 404


# --- After the review of the M2 backend ------------------------------------------------------


def test_a_late_retry_never_overwrites_or_undeletes(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/changes"
    create = tag("A", 5_000, grade="+")
    club["coach"].post(url, json={"events": [create]})
    club["coach"].post(url, json={"events": [{**create, "deleted": True, "base_version": 1}]})
    late = club["coach"].post(url, json={"events": [create]}).json()  # the create, again
    assert late["stale"] == [create["id"]]
    bundle = club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()
    assert all(e["id"] != create["id"] for e in bundle["events"])  # still deleted


def test_several_changes_to_one_item_in_a_batch(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    first = tag("S", 1_000, side="them", number=None)
    second = {**first, "attributes": {"grade": "="}}
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0}
    body = {"events": [first, second], "periods": [period, {**period, "start_us": 3}]}
    out = club["coach"].post(f"/api/videos/{ready['id']}/changes", json=body)
    assert out.status_code == 200, out.text
    assert out.json()["events"][0]["attributes"] == {"grade": "="}
    assert out.json()["periods"][0]["start_us"] == 3


def test_the_roster_only_takes_members_of_this_team(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/roster"
    for outsider in (110, 107):  # DU23-1's minor, a basketball player
        entries = [{"side": "us", "number": 9, "member_id": outsider}]
        r = club["coach"].put(url, json={"entries": entries})
        assert r.status_code == 422, outsider
    assert club["coach"].put(url, json={"entries": []}).status_code == 422
    huge = [{"side": "us", "number": 9, "member_id": 2**31}]
    assert club["coach"].put(url, json={"entries": huge}).status_code == 422


def test_bounds_and_references_are_checked(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/changes"
    bad_bodies = [
        {"events": [tag("A", 1_000, zone_from=True)]},
        {"events": [{**tag("A", 1_000), "frame": 2**31}]},
        {"segments": [rally(1_000, 10**19, None)]},
        {"segments": [rally(10**9, 10**9 + 5_000, None)]},  # past the end of the video
        {"periods": [{"id": str(uuid.uuid4()), "no": 1, "start_ms": 5_000, "end_ms": 4_000}]},
        {
            "periods": [
                {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0},
                {"id": str(uuid.uuid4()), "no": 1, "start_ms": 60_000},
            ]
        },
    ]
    for body in bad_bodies:
        assert club["coach"].post(url, json=body).status_code == 422, body


def test_rescoring_writes_no_revisions(
    ready: dict[str, Any], club: dict[str, TestClient], db_engine: Engine
) -> None:
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "first_serve": "us"}
    r1 = rally(1_000, 5_000, "us")
    club["coach"].post(
        f"/api/videos/{ready['id']}/changes", json={"periods": [period], "segments": [r1]}
    )
    with db_engine.connect() as conn:
        actions = conn.execute(text("SELECT action FROM segment_revision")).scalars().all()
    assert actions == ["create"]  # the score the server added is not someone's edit


def test_simultaneous_first_opens_take_one_snapshot(
    ready: dict[str, Any], as_: Any, db_engine: Engine
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    coaches = [as_("coach-h3") for _ in range(12)]
    url = f"/api/videos/{ready['id']}/tagging"
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda c: c.get(url).status_code, coaches))
    assert codes == [200] * 12
    with db_engine.connect() as conn:
        rows = conn.execute(text("SELECT count(*) FROM video_roster")).scalar()
    assert rows == 4


def test_server_links_tags_to_rallies_and_resends_are_not_conflicts(
    ready: dict[str, Any], club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/changes"
    kill = tag("A", 8_000, grade="#")
    out = club["coach"].post(url, json={"events": [kill]}).json()
    assert out["events"][0]["segment_id"] is None  # the rally is still open in the browser
    r1 = rally(1_000, 10_000, "us")
    club["coach"].post(url, json={"segments": [r1]})
    bundle = club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()
    assert bundle["events"][0]["segment_id"] == r1["id"]
    # The answer to the first send got lost; the browser sends the same change again.
    again = club["coach"].post(url, json={"events": [kill]}).json()
    assert again["stale"] == [] and again["events"][0]["version"] == 1
    # Deleting a rally echoes it back with its new version, for undo.
    gone = club["coach"].post(url, json={"segments": [{**r1, "deleted": True, "base_version": 1}]})
    echoed = next(s for s in gone.json()["segments"] if s["id"] == r1["id"])
    assert echoed["deleted_at"] is not None and echoed["version"] == 2
    bundle = club["coach"].get(f"/api/videos/{ready['id']}/tagging").json()
    assert bundle["events"][0]["segment_id"] is None


def test_roster_candidates_are_the_teams_players(
    ready: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    url = f"/api/videos/{ready['id']}/roster/candidates"
    people = club["coach"].get(url).json()
    assert [c["number"] for c in people] == [1, 3, 7, 11]  # every player: no consent to ask
    assert set(people[0]) == {"member_id", "name", "number"}
    assert as_("vb-sport-admin").get(url).status_code == 200
    assert as_("player-h3").get(url).status_code == 403


def test_basketball_quarters_and_score_from_made_shots(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]  # also team manager of Herren 1
    for mid in (107, 108):
        coach.put(f"/api/teams/{HERREN1}/consent/{mid}", json={"status": "accepted"})
    created = coach.post(
        f"/api/teams/{HERREN1}/videos",
        json={"kind": "match", "game_id": HERREN1 * 100 + 1,
              "files": [{"name": "GOPR0001.MP4", "size": 1000, "type": "video/mp4"}]},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    video = upload_all(coach, store, created.json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    bundle = coach.get(f"/api/videos/{video['id']}/tagging").json()
    assert bundle["sport"] == "basketball" and bundle["can_tag"] is True
    assert [(r["number"], r["name"]) for r in bundle["roster"]] == [
        (5, "Lou Dunk"),
        (23, "Mika Korb"),
    ]

    q1 = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0}
    q2 = {"id": str(uuid.uuid4()), "no": 2, "start_ms": 600_000}
    shots = [
        tag("S3", 10_000, number=23, grade="+"),
        tag("S2", 20_000, number=5, grade="-"),
        tag("FT", 30_000, side="them", number=12, grade="+"),
        tag("RO", 31_000, number=5),
        tag("S2", 700_000, number=5, grade="+"),
    ]
    out = coach.post(
        f"/api/videos/{video['id']}/changes",
        json={"periods": [q1, q2], "events": shots},
    )
    assert out.status_code == 200, out.text
    finals = {p["no"]: (p["final_us"], p["final_them"]) for p in out.json()["periods"]}
    assert finals == {1: (3, 1), 2: (5, 1)}
    url = f"/api/videos/{video['id']}/changes"
    assert coach.post(url, json={"events": [tag("RO", 1, number=5, grade="+")]}).status_code == 422
    assert (
        coach.post(url, json={"events": [tag("S2", 1, number=5, zone_from=4)]}).status_code == 422
    )
    # Reel moments carry the running score right after the tag (there are no rallies).
    preview = coach.post(
        "/api/reels/preview", json={"video_ids": [video["id"]], "codes": ["S2", "S3"]}
    ).json()
    assert [(p["code"], p["player_name"], p["score_us"], p["score_them"]) for p in preview] == [
        ("S3", "Mika Korb", 3, 0),
        ("S2", "Lou Dunk", 3, 0),
        ("S2", "Lou Dunk", 5, 1),
    ]

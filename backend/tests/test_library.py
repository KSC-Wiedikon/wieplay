"""M3: clips, reels, comments, exports (PLAN §6.8, §7.7, §5.1, §5.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import (
    MEDIA_CAPS,
    claim,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)

EXPORT_CAPS = ["export", "nas"]


@pytest.fixture
def tagged(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    """A ready H3 match with a set, two rallies and three tags of #7 (Alex, member 101)."""
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)

    def tag(code: str, t_ms: int, grade: str) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "code": code, "t_ms": t_ms, "side": "us",
                "number": 7, "attributes": {"grade": grade}}  # fmt: skip

    body = {
        "periods": [{"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "first_serve": "us"}],
        "segments": [
            {
                "id": str(uuid.uuid4()),
                "kind": "rally",
                "start_ms": 10_000,
                "end_ms": 20_000,
                "won_by": "us",
            },
            {
                "id": str(uuid.uuid4()),
                "kind": "rally",
                "start_ms": 21_000,
                "end_ms": 30_000,
                "won_by": "them",
            },
        ],
        "events": [tag("A", 15_000, "#"), tag("A", 25_000, "="), tag("S", 10_500, "+")],
    }
    r = club["coach"].post(f"/api/videos/{video['id']}/changes", json=body)
    assert r.status_code == 200, r.text
    return video


def clip(video: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"video_id": video["id"], "start_ms": 1_000, "end_ms": 6_000, "title": "Block", **kw}


def test_clips_private_and_shared(
    tagged: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    player, coach = as_("player-h3"), club["coach"]
    mine = str(uuid.uuid4())
    assert player.put(f"/api/clips/{mine}", json=clip(tagged)).status_code == 200
    shared = player.put(f"/api/clips/{uuid.uuid4()}", json=clip(tagged, visibility="team"))
    assert shared.status_code == 403  # players keep clips for themselves
    team_clip = str(uuid.uuid4())
    assert (
        coach.put(f"/api/clips/{team_clip}", json=clip(tagged, visibility="team")).status_code
        == 200
    )

    seen_by_player = {c["id"] for c in player.get(f"/api/videos/{tagged['id']}/clips").json()}
    seen_by_coach = {c["id"] for c in coach.get(f"/api/videos/{tagged['id']}/clips").json()}
    assert seen_by_player == {mine, team_clip}
    assert seen_by_coach == {team_clip}  # private means private, coaches included
    assert [c["id"] for c in player.get("/api/clips/mine").json()] == [mine]
    # Someone else's id cannot be taken over.
    assert coach.put(f"/api/clips/{mine}", json=clip(tagged)).status_code == 409
    assert as_("basketball-player").get(f"/api/videos/{tagged['id']}/clips").status_code == 404
    assert coach.delete(f"/api/clips/{mine}").status_code == 404
    assert coach.delete(f"/api/clips/{team_clip}").status_code == 204
    long = clip(tagged, end_ms=1_000 + 11 * 60 * 1000)
    assert player.put(f"/api/clips/{uuid.uuid4()}", json=long).status_code == 422


def test_smart_reels_follow_the_tags_and_the_viewer(
    tagged: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    player = as_("player-h3")
    query = {"member_id": 101, "codes": ["A"]}
    preview = player.post("/api/reels/preview", json=query).json()
    assert [(p["grade"], p["start_ms"], p["end_ms"]) for p in preview] == [
        ("#", 12_000, 17_000),  # attack window: -3 s / +2 s from the taxonomy
        ("=", 22_000, 27_000),
    ]
    only_kills = player.post("/api/reels/preview", json={**query, "grades": ["#"]}).json()
    assert len(only_kills) == 1
    assert as_("basketball-player").post("/api/reels/preview", json=query).json() == []

    reel = player.post(
        "/api/reels", json={"title": "My attacks", "kind": "smart", "query": query}
    ).json()
    assert len(player.get(f"/api/reels/{reel['id']}/items").json()) == 2
    assert club["coach"].get(f"/api/reels/{reel['id']}/items").status_code == 404  # private
    team = player.post(
        "/api/reels",
        json={"title": "x", "kind": "smart", "query": query, "visibility": "team",
              "lineage_id": club["coach"].get("/api/teams/9101").json()["lineage_id"]},
    )  # fmt: skip
    assert team.status_code == 403


def test_team_reels_and_manual_items(
    tagged: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    lineage = club["coach"].get("/api/teams/9101").json()["lineage_id"]
    items = [{"video_id": tagged["id"], "start_ms": 5_000, "end_ms": 9_000, "caption": "Look"}]
    created = club["coach"].post(
        "/api/reels",
        json={"title": "Before set 1", "kind": "manual", "items": items, "visibility": "team",
              "lineage_id": lineage},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    reel = created.json()
    player = as_("player-h3")
    assert reel["id"] in {r["id"] for r in player.get("/api/reels").json()}
    assert player.get(f"/api/reels/{reel['id']}/items").json()[0]["caption"] == "Look"
    assert as_("basketball-player").get(f"/api/reels/{reel['id']}/items").status_code == 404
    foreign = [{"video_id": str(uuid.uuid4()), "start_ms": 0, "end_ms": 1_000}]
    bad = club["coach"].post("/api/reels", json={"title": "x", "kind": "manual", "items": foreign})
    assert bad.status_code == 422


def test_condensed_match_merges_rallies(tagged: dict[str, Any], as_: Any) -> None:
    pieces = as_("player-h3").get(f"/api/videos/{tagged['id']}/condensed").json()
    # Rallies 10-20 s and 21-30 s with 2 s pre-roll / 1.5 s post-roll overlap: one piece.
    assert [(p["start_ms"], p["end_ms"]) for p in pieces] == [(8_000, 31_500)]


def test_comments_audience_and_mentions(
    tagged: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    player, coach = as_("player-h3"), club["coach"]
    url = f"/api/videos/{tagged['id']}/comments"

    def post(client: TestClient, **kw: Any) -> Any:
        return client.post(url, json={"id": str(uuid.uuid4()), "body": "Look here", **kw})

    assert post(coach, t_ms=15_000, mentions=[101]).status_code == 201
    assert post(coach, audience="coaches").status_code == 201
    assert post(coach, audience="person", recipient=102).status_code == 201  # Sam
    assert post(coach, mentions=[107]).status_code == 422  # not on this team
    assert post(coach, audience="person", recipient=110).status_code == 422
    seen = player.get(url).json()
    assert [c["audience"] for c in seen] == ["team"]  # Alex: not the coaches' or Sam's
    assert seen[0]["author_name"] == "Jo Trainer" and seen[0]["mentions"] == [101]
    assert len(coach.get(url).json()) == 3
    mine = post(player, body="Thanks").json()
    edited = player.patch(f"/api/comments/{mine['id']}", json={"body": "Thanks, coach"})
    assert edited.json()["body"] == "Thanks, coach" and edited.json()["edited_at"]
    assert coach.patch(f"/api/comments/{mine['id']}", json={"body": "x"}).status_code == 404
    assert coach.delete(f"/api/comments/{mine['id']}").status_code == 204  # coaches moderate
    assert as_("basketball-player").get(url).status_code == 404


def test_exports_render_on_the_worker_and_download_once_ready(
    app: FastAPI,
    tagged: dict[str, Any],
    club: dict[str, TestClient],
    as_: Any,
    store: FakeStore,
    db_engine: Engine,
) -> None:
    coach = club["coach"]
    clip_id = str(uuid.uuid4())
    coach.put(f"/api/clips/{clip_id}", json=clip(tagged, title="Our block"))
    assert as_("player-h3").post("/api/exports", json={"kind": "condensed",
                                 "video_id": tagged["id"]}).status_code == 403  # fmt: skip
    created = coach.post("/api/exports", json={"kind": "clip", "clip_id": clip_id})
    assert created.status_code == 201, created.text
    export = created.json()
    assert export["status"] == "queued"

    w = worker_client(app, club["admin"], EXPORT_CAPS)
    [job] = claim(w, EXPORT_CAPS)
    assert job["kind"] == "export"
    assert job["params"]["watermark"].startswith("KSCW intern · Jo Trainer · ")
    [piece] = job["params"]["pieces"]
    assert (piece["start_ms"], piece["end_ms"]) == (1_000, 6_000)
    assert [p["offset_ms"] for p in piece["parts"]] == [0]
    key = job["params"]["key"]
    assert (
        w.post(f"/api/worker/jobs/{job['id']}/sign", json={"op": "put", "keys": [key]}).status_code
        == 200
    )
    other = {"op": "put", "keys": [f"media/{tagged['id']}/1080p/seg_00000.m4s"]}
    assert w.post(f"/api/worker/jobs/{job['id']}/sign", json=other).status_code == 403
    assert coach.get(f"/api/exports/{export['id']}/download").status_code == 409  # not yet
    done = w.post(
        f"/api/worker/jobs/{job['id']}/complete", json={"bytes": 123, "duration_ms": 5000}
    )
    assert done.status_code == 204, done.text

    listed = coach.get("/api/exports").json()[0]
    assert listed["status"] == "ready" and listed["expires_at"]
    download = coach.get(f"/api/exports/{export['id']}/download")
    assert (
        download.status_code == 302 and "download=Our%20block.mp4" in download.headers["location"]
    )
    assert as_("vb-sport-admin").get(f"/api/exports/{export['id']}/download").status_code == 404

    condensed = coach.post("/api/exports", json={"kind": "condensed", "video_id": tagged["id"]})
    assert condensed.status_code == 201 and condensed.json()["duration_ms"] == 23_500

    from nomal.api.library import sweep_exports

    store.objects[key] = 123
    with app.state.sessionmaker() as db:
        assert sweep_exports(db, store, now=datetime.now(UTC) + timedelta(days=31)) == 1
    assert key not in store.objects
    assert coach.get(f"/api/exports/{export['id']}/download").status_code == 409
    with db_engine.connect() as conn:
        actions = conn.execute(text("SELECT action FROM audit_log")).scalars().all()
    assert "export_requested" in actions and "export_downloaded" in actions


def test_a_failed_export_fails_alone(
    app: FastAPI, tagged: dict[str, Any], club: dict[str, TestClient]
) -> None:
    created = club["coach"].post(
        "/api/exports", json={"kind": "condensed", "video_id": tagged["id"]}
    )
    w = worker_client(app, club["admin"], EXPORT_CAPS)
    [job] = claim(w, EXPORT_CAPS)
    w.post(f"/api/worker/jobs/{job['id']}/fail", json={"error": "disk full", "retryable": False})
    export = club["coach"].get("/api/exports").json()[0]
    assert export["id"] == created.json()["id"] and export["status"] == "failed"
    assert club["coach"].get(f"/api/videos/{tagged['id']}").json()["status"] == "ready"


def test_drill_segments_carry_a_label(tagged: dict[str, Any], club: dict[str, TestClient]) -> None:
    drill = {"id": str(uuid.uuid4()), "kind": "drill", "label": "Serve receive 6v6",
             "start_ms": 40_000, "end_ms": 50_000}  # fmt: skip
    r = club["coach"].post(f"/api/videos/{tagged['id']}/changes", json={"segments": [drill]})
    assert r.status_code == 200
    labels = [s["label"] for s in r.json()["segments"] if s["kind"] == "drill"]
    assert labels == ["Serve receive 6v6"]

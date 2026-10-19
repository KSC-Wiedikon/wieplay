"""Identifying the players with the coach (PLAN §10.5): the queue of unsure cards, answers as
facts (the cards linked anew around them), undo, a card's moments, a tap in the paused video,
and "which team is ours". Made-up boxes and numbers only."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import make_ready
from tests.test_players_api import CAPS, claim, complete, trajectory
from wieplay.core import identify
from wieplay.core.config import Settings
from wieplay.core.models import AuditLog, Period, Video
from wieplay.core.players import PlayerCluster
from wieplay.ml import players as pl

# Track id → (from s, to s, box): when each person is seen (10 analysed frames a second).
SCHEDULE: dict[int, tuple[float, float, tuple[float, float, float, float]]] = {
    1: (0.0, 8.0, (0.10, 0.50, 0.15, 0.80)),  # the big card: the coach names it #7
    2: (9.0, 12.0, (0.12, 0.50, 0.17, 0.80)),  # reads #7, never with 1
    3: (13.0, 15.0, (0.14, 0.50, 0.19, 0.80)),  # no number, right after #7 is gone
    4: (0.0, 25.0, (0.60, 0.20, 0.63, 0.40)),  # theirs, far, all along
    5: (10.0, 14.0, (0.30, 0.50, 0.35, 0.80)),  # ours, unsure, seen while #7's pieces are
    6: (0.0, 25.0, (0.80, 0.50, 0.85, 0.80)),  # ours, sure
}


def index_bytes() -> bytes:
    geom = pl.Geometry(roi=(0.0, 0.0, 1.0, 1.0))
    col = pl.Collector(geom, 1000, 1000, (0, 0, 1000, 1000))
    img = np.zeros((1000, 1000, 3), np.uint8)
    for k in range(251):
        t = k / 10
        ids = [tid for tid, (a, b, _) in SCHEDULE.items() if a <= t <= b]
        boxes = np.array([[v * 1000 for v in SCHEDULE[i][2]] for i in ids], dtype=float)
        col.add(
            round(t * 1000), 0, img, boxes.reshape(-1, 4), np.full(len(ids), 0.9), np.array(ids)
        )
    tracks = pl.finish_tracks(col)
    return gzip.compress(json.dumps(pl.encode_index(col, tracks)).encode(), mtime=0)


def row(tid: int, side: str) -> dict[str, Any]:
    a, b, _ = SCHEDULE[tid]
    return {"id": tid, "t0": int(a * 1000), "t1": int(b * 1000), "n": int((b - a) * 10) + 1,
            "side": side, "number": None, "number_conf": 0.0, "crop": [0, tid], "h": 120.0,
            "foot": None}  # fmt: skip


def result(job: dict[str, Any], store: FakeStore, calibrated: bool = True) -> dict[str, Any]:
    prefix, out = job["params"]["prefix"], job["params"]["out_dir"]
    data = index_bytes()
    store.put_object(f"{prefix}index.json.gz", data)
    store.put_object(f"{prefix}sheet-0.jpg", b"\xff\xd8jpeg")

    def card(key: int, team: str, tid: int, **extra: Any) -> dict[str, Any]:
        side = "far" if tid == 4 else "near"
        return {"key": key, "team": team, "crops": [[0, key]], "tracks": [row(tid, side)], **extra}

    return {
        "model": {
            "name": "wieplay-players",
            "version": "abc123",
            "weights_sha256": "e" * 64,
            "params": {"detect_fps": 10.0},
        },
        "tracks_artifact": {"nas_path": f"{out}/players-x.npz", "sha256": "f" * 64, "bytes": 99},
        "index": {
            "key": f"{prefix}index.json.gz",
            "nas_path": f"{out}/players-x/index.json.gz",
            "sha256": hashlib.sha256(data).hexdigest(),  # the API caches indexes by it
            "bytes": len(data),
        },
        "sheets": {
            "keys": [f"{prefix}sheet-0.jpg"],
            "nas_dir": f"{out}/players-x",
            "cell": [80, 160],
            "cols": 10,
            "rows": 8,
        },
        "clusters": [
            card(1, "us", 1, confidence=0.3),
            card(2, "us", 2, number=7, number_conf=0.8, confidence=0.3),
            card(3, "us", 3, confidence=0.2),
            card(4, "them", 4, confidence=0.3),
            card(5, "us", 5, confidence=0.25),
            card(6, "us", 6, number=11, number_conf=0.9, confidence=0.9),
        ],
        "stats": {"tracks": 6, "clusters": 6, "calibrated": calibrated},
        "timing": {"wall_s": 1.0},
    }


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"players_auto": True})  # the players job follows


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], CAPS)


@pytest.fixture
def found(ready: Any, analyser: TestClient, store: FakeStore) -> dict[str, Any]:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    complete(analyser, job, result(job, store))
    return job


def queue(client: TestClient, video: Any) -> dict[str, Any]:
    r = client.get(f"/api/videos/{video['id']}/players/queue")
    assert r.status_code == 200, r.text
    return dict(r.json())


def cards(app: FastAPI, video: Any) -> dict[int, PlayerCluster]:
    with app.state.sessionmaker() as db:
        rows = db.scalars(
            select(PlayerCluster).where(PlayerCluster.video_id == uuid.UUID(video["id"]))
        ).all()
        for r in rows:
            db.expunge(r)
        return {r.key: r for r in rows}


def test_the_queue_is_the_unsure_cards_biggest_first(
    ready: Any, found: Any, club: dict[str, TestClient], app: FastAPI
) -> None:
    q = queue(club["coach"], ready)
    by_key = {c.id: k for k, c in cards(app, ready).items()}
    assert [by_key[uuid.UUID(i["card"])] for i in q["items"]] == [4, 1, 5, 2, 3]
    assert q["named"] == 0 and 0 < q["sure_share"] < 0.5
    assert q["teams"] is None  # calibrated: the teams are known
    summary = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["players"]
    assert summary["uncertain"] == 5 and summary["sure_share"] == q["sure_share"]


def test_an_answer_links_the_cards_anew_and_undo_takes_it_back(
    ready: Any, found: Any, club: dict[str, TestClient], app: FastAPI
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}/players"
    ids = {k: str(c.id) for k, c in cards(app, ready).items()}
    r = coach.post(f"{url}/clusters/{ids[1]}/answer", json={"kind": "number", "number": 7})
    assert r.status_code == 200, r.text
    out = r.json()
    why = {x["card"]: x["why"] for x in out["relinked"]}
    # Card 2 read #7 and is never seen with card 1: it joins. Card 3 has no number: it stays
    # (who is missing is not used).
    assert why == {ids[2]: "number"}
    now = cards(app, ready)
    assert now[1].number == 7 and now[1].member_id == 101 and len(now[1].tracks) == 2
    assert now[2].merged_into == now[1].id and now[3].merged_into is None
    assert len(out["queue"]["items"]) == 3  # theirs, cards 5 and 3 left
    assert out["queue"]["sure_share"] > queue(coach, ready)["sure_share"] - 1e-9
    with app.state.sessionmaker() as db:
        assert db.scalars(select(AuditLog).where(AuditLog.action == "player_identified")).first()

    r = coach.post(f"{url}/restore", json={"cards": out["undo"]})
    assert r.status_code == 200, r.text
    back = cards(app, ready)
    assert back[1].number is None and len(back[1].tracks) == 1
    assert back[2].merged_into is None
    assert len(r.json()["queue"]["items"]) == 5


def test_other_answers_a_team_not_a_player_same_as_and_two_people(
    ready: Any, found: Any, club: dict[str, TestClient], app: FastAPI
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}/players"
    ids = {k: str(c.id) for k, c in cards(app, ready).items()}
    r = coach.post(f"{url}/clusters/{ids[5]}/answer", json={"kind": "team", "team": "them"})
    assert r.status_code == 200 and cards(app, ready)[5].team == "them"
    assert cards(app, ready)[6].team == "us"  # this card only
    r = coach.post(f"{url}/clusters/{ids[4]}/answer", json={"kind": "not_player"})
    assert r.status_code == 200 and cards(app, ready)[4].not_player
    r = coach.post(f"{url}/clusters/{ids[3]}/answer", json={"kind": "same_as", "into": ids[2]})
    assert r.status_code == 200 and cards(app, ready)[3].merged_into == uuid.UUID(ids[2])
    r = coach.post(f"{url}/clusters/{ids[2]}/answer", json={"kind": "split", "tracks": [3]})
    assert r.status_code == 200, r.text
    after = cards(app, ready)
    assert [int(t["id"]) for t in after[2].tracks] == [2] and len(after) == 7
    # Undo of a split empties the new card back into the old one.
    r = coach.post(f"{url}/restore", json={"cards": r.json()["undo"]})
    assert r.status_code == 200
    assert sorted(int(t["id"]) for t in cards(app, ready)[2].tracks) == [2, 3]
    # Some of a card's pieces named at once: they move to the card with that number.
    coach.post(f"{url}/clusters/{ids[1]}/answer", json={"kind": "number", "number": 3})
    r2 = coach.post(
        f"{url}/clusters/{ids[2]}/answer", json={"kind": "pieces", "tracks": [3], "number": 3}
    )
    assert r2.status_code == 200, r2.text
    assert sorted(int(t["id"]) for t in cards(app, ready)[1].tracks) == [1, 3]
    coach.post(f"{url}/restore", json={"cards": r2.json()["undo"]})
    assert sorted(int(t["id"]) for t in cards(app, ready)[2].tracks) == [2, 3]
    # The same person plays for one team; a card of another video is refused.
    bad = coach.post(f"{url}/clusters/{ids[2]}/answer", json={"kind": "same_as", "into": ids[4]})
    assert bad.status_code == 422
    assert (
        coach.post(f"{url}/restore", json={"cards": [{"id": str(uuid.uuid4())}]}).status_code == 422
    )


def test_moments_frames_and_a_tap_in_the_paused_video(
    ready: Any, found: Any, club: dict[str, TestClient], app: FastAPI
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}/players"
    ids = {k: str(c.id) for k, c in cards(app, ready).items()}
    moments = coach.get(f"{url}/clusters/{ids[1]}/moments").json()
    assert len(moments) == 1 and moments[0]["track"] == 1
    assert 0 <= moments[0]["t_ms"] <= 8000 and moments[0]["box"][0] == pytest.approx(0.10, abs=1e-3)
    assert len(moments[0]["clip"]) > 10  # boxes a few seconds around it
    frame = coach.get(f"{url}/frame", params={"t_ms": 10_000}).json()
    tracks = {b["track"]: b for b in frame["boxes"]}
    assert set(tracks) == {2, 4, 5, 6} and tracks[6]["sure"] and not tracks[2]["sure"]
    r = coach.post(f"{url}/tap", json={"t_ms": 10_000, "track": 2, "number": 3})
    assert r.status_code == 200, r.text
    assert cards(app, ready)[2].number == 3
    # A tap on a card the coach named otherwise takes that track out first.
    coach.post(f"{url}/clusters/{ids[2]}/answer", json={"kind": "same_as", "into": ids[6]})
    r = coach.post(f"{url}/tap", json={"t_ms": 10_000, "track": 2, "number": 1})
    assert r.status_code == 200, r.text
    after = cards(app, ready)
    split_off = [c for c in after.values() if c.number == 1]
    assert len(split_off) == 1 and [int(t["id"]) for t in split_off[0].tracks] == [2]
    assert (
        coach.post(f"{url}/tap", json={"t_ms": 10_000, "track": 99, "number": 1}).status_code == 422
    )


def test_which_team_is_ours_when_the_teams_are_uncertain(
    ready: Any, analyser: TestClient, store: FakeStore, club: dict[str, TestClient], app: FastAPI
) -> None:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    complete(analyser, job, result(job, store, calibrated=False))
    coach = club["coach"]
    with app.state.sessionmaker() as db:
        db.add(Period(id=uuid.uuid4(), video_id=uuid.UUID(ready["id"]), no=1, start_ms=0,
                      end_ms=30_000, source="coach", review="confirmed", version=1))  # fmt: skip
        db.commit()
    q = queue(coach, ready)
    assert q["teams"] is not None and q["teams"]["near_team"] == "us"
    assert {c["card"] for c in q["teams"]["far"]} == {str(cards(app, ready)[4].id)}
    # The coach says ours were on the far side: every card turns round.
    r = coach.post(f"/api/videos/{ready['id']}/players/teams", json={"near_is_ours": False})
    assert r.status_code == 200, r.text
    after = cards(app, ready)
    assert after[4].team == "us" and after[1].team == "them"
    assert r.json()["queue"]["teams"] is None  # answered: the set's side is the coach's now
    with app.state.sessionmaker() as db:
        period = identify.first_set(db, db.get(Video, uuid.UUID(ready["id"])))  # type: ignore[arg-type]
        assert period is not None and period.our_court_side_at_start == "far"


def test_who_may_answer(
    ready: Any, found: Any, club: dict[str, TestClient], as_: Any, app: FastAPI
) -> None:
    """Whoever may tag the match answers (the team's players too, as in the gallery); someone
    of another team sees nothing."""
    ids = {k: str(c.id) for k, c in cards(app, ready).items()}
    url = f"/api/videos/{ready['id']}/players"
    member = as_("player-h3")
    assert member.get(f"{url}/queue").status_code == 200
    outsider = as_("basketball-player")
    assert outsider.get(f"{url}/queue").status_code == 404
    r = outsider.post(f"{url}/clusters/{ids[1]}/answer", json={"kind": "not_player"})
    assert r.status_code == 404


def test_time_on_court_overlaps_and_gaps() -> None:
    a = [(0, 1000), (5000, 6000)]
    assert identify.overlap(a, [(900, 1200)]) and not identify.overlap(a, [(1100, 4000)])
    assert identify.gap_ms(a, [(7000, 8000)]) == 1000
    assert identify.gap_ms(a, [(500, 700)]) is None

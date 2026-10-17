"""The scoreboard through the API (PLAN §10.3, step 5): a coach marks the box, the hall's memory
offers it to the next video in that hall (else the team's), the worker's reading becomes a track,
the scoring follows the board where the serves say nothing, a disagreement is flagged, "no
scoreboard in this video" forgets it."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, complete, make_ready
from tests.test_scoring_api import analysed, match, tagging
from wieplay.core.config import Settings
from wieplay.ml import board as bd

BOX = {"x": 0.0, "y": 0.84, "w": 0.03, "h": 0.05}
SETS = {"x": 0.05, "y": 0.84, "w": 0.02, "h": 0.05}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def claim_board(w: TestClient) -> dict[str, Any]:
    r = w.post(
        "/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": ["scoreboard"]}
    )
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == "scoreboard"
    return job


def board_of(rallies: list[tuple[int, int, str, str]], left_is_us: bool = True) -> list[bd.Segment]:
    """What a board showed: the score before each rally, from its start until 2 s before the
    next one (the scorer turns the card after the point), 0:0 again at every set."""
    out = []
    us = them = 0
    set_no = 1
    for i, (start, end, _, won) in enumerate(rallies):
        nxt = rallies[i + 1][0] if i + 1 < len(rallies) else end + 10_000
        if i and start - rallies[i - 1][1] > 100_000:  # a set break
            us = them = 0
            set_no += 1
        score = (us, them) if left_is_us else (them, us)
        out.append(bd.Segment(start, nxt - 2_000, score[0], score[1], set_no, 0.9, "step"))
        us, them = us + (won == "us"), them + (won == "them")
    final = (us, them) if left_is_us else (them, us)
    last = rallies[-1][1]
    out.append(bd.Segment(last + 3_000, last + 9_000, final[0], final[1], set_no, 0.9, "step"))
    return out


def result(job: dict[str, Any], found: list[bd.Segment]) -> dict[str, Any]:
    return {
        "model": {"name": "wieplay-scoreboard", "version": "abc", "params": {"fps": 2}},
        "region_id": job["params"]["region_id"],
        "artifact": {
            "nas_path": f"{job['params']['out_dir']}/scoreboard-test.npz",
            "sha256": "d" * 64,
            "bytes": 2048,
            "format": "npz",
            "schema_version": 1,
            "stats": {},
        },
        "track": bd.pack(found),
        "summary": {"samples": 1000, "read": 600},
        "timing": {"wall_s": 30.0},
    }


def test_the_box_is_marked_read_and_remembered_for_the_hall(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient, as_: Any
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)  # a home game: Halle Test
    url = f"/api/videos/{video['id']}/scoreboard"
    state = coach.get(url).json()
    assert state["can_edit"] and state["region"] is None and state["suggestion"] is None
    assert as_("player-h3").get(url).json()["can_edit"] is True  # players tag (17.10.2026)
    assert as_("basketball-player").put(url, json={"rect": BOX}).status_code == 404
    assert coach.put(url, json={"rect": {**BOX, "x": 0.99}}).status_code == 422  # off the picture

    saved = coach.put(url, json={"rect": BOX, "sets": SETS, "kind": "flip", "at_ms": 1_000})
    assert saved.status_code == 200, saved.text
    region = saved.json()["region"]
    assert region["kind"] == "flip" and region["hall_name"] == "Halle Test"
    assert saved.json()["job"]["status"] == "queued"
    job = claim_board(analyser)
    params = job["params"]
    assert params["rect"] == BOX and params["sets"] == SETS and params["kind"] == "flip"
    assert params["fps"] == 2.0 and params["inputs"] and params["out_dir"].startswith("videos/")
    assert params["region_id"] == region["id"]

    # The next home game is offered the box (the hall's); an away game the team's last one.
    home = make_ready(app, club, store, k=2)
    offered = coach.get(f"/api/videos/{home['id']}/scoreboard").json()["suggestion"]
    assert offered["source"] == "hall" and offered["region"]["rect"] == BOX
    away = make_ready(app, club, store, k=1)
    assert (
        coach.get(f"/api/videos/{away['id']}/scoreboard").json()["suggestion"]["source"] == "team"
    )
    copied = coach.put(
        f"/api/videos/{home['id']}/scoreboard",
        json={"rect": BOX, "kind": "flip", "copied_from": region["id"]},
    )
    assert copied.json()["region"]["copied_from"] == region["id"]


def test_the_board_wins_over_unsure_serves_and_flags_disagreements(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    rallies, _ = match()
    analysed(analyser, rallies)
    url = f"/api/videos/{video['id']}/scoreboard"
    coach.put(url, json={"rect": BOX, "kind": "led"})
    job = claim_board(analyser)
    # The board, left = them (the side the official scores tell apart).
    complete(analyser, job, result(job, board_of(rallies, left_is_us=False)))

    state = coach.get(url).json()
    assert state["job"]["status"] == "done"
    assert len(state["reading"]["segments"]) == len(rallies) + 1
    scoring = coach.get(f"/api/videos/{video['id']}/analysis").json()["scoring"]
    board = scoring["board"]
    assert board["orientation"] == "official" and board["left_is_us"] is False
    assert board["rallies_told"] >= len(rallies) - 4
    found = [s for s in tagging(coach, video)["segments"] if s["kind"] == "rally"]
    by_start = {s["start_ms"]: s for s in found}
    right = sum(by_start[start]["model_won_by"] == won for start, _, _, won in rallies)
    assert right == len(rallies)
    assert scoring["sets_matching_official"] == 4

    # A misread step: the board says them where it was us: the official score wins in the end,
    # but somewhere the board is overruled or disagrees, and that rally is flagged.
    coach.put(url, json={"rect": BOX, "kind": "led"})
    job = claim_board(analyser)
    wrong = board_of(rallies)
    a = wrong[3]
    wrong[3] = bd.Segment(a.first_ms, a.last_ms, a.left - 1, a.right + 1, a.set_no, 0.9, "step")
    complete(analyser, job, result(job, wrong))
    flagged = [s for s in tagging(coach, video)["segments"] if s.get("score_note") == "board"]
    assert flagged and all(s["score_conflict"] for s in flagged)

    # No scoreboard after all: the box, its reading and its effect go.
    assert coach.delete(url).status_code == 204
    gone = coach.get(url).json()
    assert gone["region"] is None and gone["reading"] is None
    after = coach.get(f"/api/videos/{video['id']}/analysis").json()["scoring"]
    assert after["board"] is None
    assert coach.delete(url).status_code == 404


def test_a_stale_reading_is_dropped(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    url = f"/api/videos/{video['id']}/scoreboard"
    coach.put(url, json={"rect": BOX})
    job = claim_board(analyser)
    coach.delete(url)  # the coach changed their mind while it ran
    coach.put(url, json={"rect": {**BOX, "x": 0.2}})
    r = analyser.post(f"/api/worker/jobs/{job['id']}/complete", json=result(job, []))
    assert r.status_code in (204, 409, 404)
    assert coach.get(url).json()["reading"] is None
    bad = claim_board(analyser)
    body = result(bad, [])
    body["artifact"]["nas_path"] = "elsewhere/x.npz"
    assert analyser.post(f"/api/worker/jobs/{bad['id']}/complete", json=body).status_code == 422

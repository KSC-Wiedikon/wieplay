"""Automatic scoring through the API (PLAN §10.3): the series a worker sends, the model's
server and winner per rally, the sets and gap labels it proposes, the coach's work never
overwritten, "first serve" clicks, rotations, the serve model's training loop."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import (
    ANALYSE_CAPS,
    change,
    claim_kind,
    complete,
    make_ready,
    payload,
)
from wieplay.core.config import Settings
from wieplay.core.models import ModelRun, Segment
from wieplay.ml import tracks

# Game k=0 of the fixtures is a home game: [us, them] per set.
OFFICIAL = [(25, 20), (23, 25), (25, 18), (25, 22)]
RALLY_MS, GAP_MS, BREAK_MS, WARMUP_MS = 5_000, 11_000, 150_000, 60_000


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def winners_of(final: tuple[int, int]) -> list[str]:
    """A valid order of points ending on `final`: level until the loser's total, then the
    winner pulls away."""
    a, b = final
    low = min(a, b)
    out = ["us", "them"] * low
    out += ["us" if a > b else "them"] * (max(a, b) - low)
    return out


def match() -> tuple[list[tuple[int, int, str, str]], int]:
    """(start, end, server, winner) per rally of a 4-set match, our side near in sets 1 and 3,
    we serve first in sets 1 and 3; the end of the last rally."""
    out = []
    t = WARMUP_MS
    for no, final in enumerate(OFFICIAL, start=1):
        server = "us" if no % 2 == 1 else "them"
        for won in winners_of(final):
            out.append((t, t + RALLY_MS, server, won))
            server = won
            t += RALLY_MS + GAP_MS
        t += BREAK_MS
    return out, out[-1][1]


def serve_track(rallies: list[tuple[int, int, str, str]], sure: float = 0.97) -> str:
    steps = [None] * (3_600_000 // tracks.SERVE_STEP_MS)
    set_no, last = 1, rallies[0][0]
    for start, _, server, _ in rallies:
        if start - last > BREAK_MS:
            set_no += 1
        last = start
        near_is_us = set_no % 2 == 1
        near = (server == "us") == near_is_us
        for k in range(start // 500 - 1, start // 500 + 2):
            steps[k] = sure if near else 1 - sure  # type: ignore[call-overload]
    return tracks.encode(tracks.quantise(steps))


def activity(timeouts: list[tuple[int, int]] = ()) -> dict[str, str]:  # type: ignore[assignment]
    n = 3600
    side = [1.0] * n
    court = [1.0] * n
    for a, b in timeouts:
        for k in range(a // 1000, b // 1000):
            side[k], court[k] = 2.6, 0.6
    return {
        "ball": tracks.encode(tracks.quantise([0.0] * n)),
        "court": tracks.encode(tracks.quantise(c / tracks.MOTION_SCALE for c in court)),
        "side": tracks.encode(tracks.quantise(s / tracks.MOTION_SCALE for s in side)),
        "whistle": tracks.encode(bytes(n)),
    }


def analysed(
    analyser: TestClient, rallies: list[tuple[int, int, str, str]], **extra: Any
) -> dict[str, Any]:
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(s, e) for s, e, _, _ in rallies])
    body["rallies"]["proposals"] = [
        {"start_ms": s, "end_ms": e, "confidence": 0.9} for s, e, _, _ in rallies
    ]
    body["tracks"] = {
        "serve": {"step_ms": 500, "finder": "v0", "channels": {"p_near": serve_track(rallies)}},
        "activity": {"step_ms": 1000, "channels": activity(**extra)},
    }
    complete(analyser, job, body)
    return job


def tagging(client: TestClient, video: Any) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/api/videos/{video['id']}/tagging").json()
    return out


def rallies_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (s for s in data["segments"] if s["kind"] == "rally"), key=lambda s: s["start_ms"]
    )


# --- the model's score line -------------------------------------------------------------------


def test_an_analysis_scores_the_match(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, last_end = match()
    analysed(analyser, played)
    data = tagging(club["coach"], ready)
    # Four sets proposed from the breaks and the official points, as suggestions.
    periods = sorted(data["periods"], key=lambda p: p["no"])
    assert [(p["no"], p["source"], p["review"]) for p in periods] == [
        (n, "model", "unreviewed") for n in (1, 2, 3, 4)
    ]
    assert [(p["final_us"], p["final_them"]) for p in periods] == OFFICIAL
    assert [(p["model_court_side"], p["model_first_serve"]) for p in periods] == [
        ("near", "us"), ("far", "them"), ("near", "us"), ("far", "them")
    ]  # fmt: skip
    assert periods[0]["first_serve"] is None  # the coach's field stays empty
    rallies = rallies_of(data)
    assert len(rallies) == len(played)
    for seg, (_, _, server, won) in zip(rallies, played, strict=True):
        assert (seg["model_serving"], seg["model_won_by"]) == (server, won)
        assert seg["won_by"] is None and seg["serving"] is None  # facts only
        assert seg["score_source"] == "model" and seg["score_conflict"] is False
        assert seg["serving_court"] in ("near", "far") and seg["serve_confidence"] > 0.9
    assert (rallies[-1]["score_us_after"], rallies[-1]["score_them_after"]) == OFFICIAL[-1]
    # Gaps: the warm-up, three set breaks, the end; ordinary dead time stays unlabelled.
    gaps = sorted(
        (s for s in data["segments"] if s["kind"] != "rally"), key=lambda s: s["start_ms"]
    )
    assert [g["kind"] for g in gaps] == [
        "warmup", "set_break", "set_break", "set_break", "other_dead"
    ]  # fmt: skip
    assert all(g["source"] == "model" and g["review"] == "unreviewed" for g in gaps)
    assert gaps[0]["end_ms"] == played[0][0] and gaps[-1]["start_ms"] == last_end
    assert data["first_serve_ms"] == played[0][0]
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    scoring = state["scoring"]
    assert scoring["serve_finder"] == "v0" and scoring["flagged"] == 0
    assert (scoring["sets_official"], scoring["sets_matching_official"]) == (4, 4)
    assert scoring["sides_confidence"] > 0.9
    assert scoring["gaps"] == {
        "warmup": 1, "set_break": 3, "timeout": 0, "stoppage": 0, "other_dead": 1
    }  # fmt: skip
    assert [s["official"] for s in scoring["sets"]] == [list(s) for s in OFFICIAL]
    assert scoring["vs_coach"] is None  # nothing a coach decided yet
    # Stats count a model set only once a coach accepts it.
    assert club["coach"].get(f"/api/videos/{ready['id']}/stats").json()["sets"] == []


def test_a_missed_rally_is_flagged_and_the_score_still_ends_right(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    gone = 30
    kept = played[:gone] + played[gone + 1 :]
    analysed(analyser, kept)
    rallies = rallies_of(tagging(club["coach"], ready))
    notes = [(r["start_ms"], r["score_note"]) for r in rallies if r["score_conflict"]]
    assert (played[gone + 1][0], "missing_before") in notes
    set1 = [r for r in rallies if r["start_ms"] < played[45][0]]
    finals = [(r["score_us_after"], r["score_them_after"]) for r in set1]
    assert OFFICIAL[0] in finals


def test_the_coach_is_never_overwritten(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    data = tagging(club["coach"], ready)
    first, second = rallies_of(data)[:2]
    wrong = "them" if played[0][3] == "us" else "us"
    accepted = change(
        club["coach"],
        ready,
        {**_seg_in(first), "review": "confirmed", "won_by": wrong},
        {**_seg_in(second), "serving_manual": "them"},
    )
    by_id = {s["id"]: s for s in accepted["segments"]}
    assert by_id[first["id"]]["won_by"] == wrong  # the coach's winner stays
    assert by_id[second["id"]]["serving_manual"] == "them"
    assert by_id[second["id"]]["serving"] == "them"  # a fact now
    # Calling the serve rescored the model's suggestions in the same request; the coach's
    # winner is kept, the disagreement is flagged.
    assert by_id[first["id"]]["model_won_by"] == wrong
    assert any(s["score_conflict"] for s in accepted["segments"] if s["kind"] == "rally")
    # An edit that leaves serving_manual out keeps it.
    later = change(
        club["coach"], ready, {**_seg_in(by_id[second["id"]]), "end_ms": second["end_ms"] + 500}
    )
    kept = next(s for s in later["segments"] if s["id"] == second["id"])
    assert kept["serving_manual"] == "them"
    # Rescoring on request changes nothing a coach set.
    r = club["coach"].post(f"/api/videos/{ready['id']}/scoring")
    assert r.status_code == 200 and r.json()["scoring"]["vs_coach"] is None
    again = {s["id"]: s for s in tagging(club["coach"], ready)["segments"]}
    assert again[first["id"]]["won_by"] == wrong
    assert again[first["id"]]["review"] == "corrected"  # accepted with another winner
    assert again[second["id"]]["serving_manual"] == "them"


def score_runs(app: FastAPI, video: Any) -> list[str]:
    """The reasons of the video's scoring runs, oldest first."""
    with app.state.sessionmaker() as db:
        runs = db.scalars(
            select(ModelRun)
            .where(ModelRun.video_id == uuid.UUID(video["id"]), ModelRun.kind == "score")
            .order_by(ModelRun.created_at)
        ).all()
        return [str((r.params or {}).get("reason")) for r in runs]


def test_a_winner_set_in_review_is_a_fact_and_scores_again(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    first, second = rallies_of(tagging(club["coach"], ready))[:2]
    right = played[0][3]
    wrong = "them" if right == "us" else "us"
    before = score_runs(app, ready)
    # Key 4 or 3 in the review: the winner, the proposal's state passed through as it is.
    out = change(club["coach"], ready, {**_seg_in(first), "won_by": wrong})
    by_id = {s["id"]: s for s in out["segments"]}
    seg = by_id[first["id"]]
    assert seg["won_by"] == wrong
    assert seg["review"] == "corrected"  # a winner answers the proposal
    # The score line follows the coach in the same answer, as a fact.
    assert (seg["score_us_after"], seg["score_them_after"]) == ((1, 0) if wrong == "us" else (0, 1))
    assert seg["score_source"] == "tags"
    # The model scored again and keeps to it; the next rally's serve follows the new winner.
    assert score_runs(app, ready) == [*before, "changes"]
    assert seg["model_won_by"] == wrong
    assert by_id[second["id"]]["serving"] == wrong
    # Undo (U): the proposal is open again, the model's winner back, scored again.
    back = change(club["coach"], ready, {**_seg_in(seg), "won_by": None, "review": "unreviewed"})
    seg = next(s for s in back["segments"] if s["id"] == first["id"])
    assert (seg["won_by"], seg["review"], seg["model_won_by"]) == (None, "unreviewed", right)
    assert score_runs(app, ready) == [*before, "changes", "changes"]
    # A change that leaves the winner as it is does not score again.
    change(club["coach"], ready, {**_seg_in(seg), "end_ms": seg["end_ms"] + 250})
    assert len(score_runs(app, ready)) == len(before) + 2


def _seg_in(seg: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "kind", "start_ms", "end_ms", "won_by", "review")
    return {**{k: seg[k] for k in keep}, "base_version": seg["version"]}


def _period_in(p: dict[str, Any], **changes: Any) -> dict[str, Any]:
    keep = (
        "id", "no", "start_ms", "end_ms", "start_us", "start_them", "first_serve",
        "our_court_side_at_start", "side_switch_at_score", "start_rotation", "review",
    )  # fmt: skip
    return {**{k: p[k] for k in keep}, "base_version": p["version"], **changes}


def test_first_serve_and_sides_from_the_sets_bar(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    """Without serve evidence the sides are a guess; the coach's click settles the match."""
    played, _ = match()
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(s, e) for s, e, _, _ in played])
    complete(analyser, job, body)  # no series: analysed before step D
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["scoring"]["serve_finder"] is None
    data = tagging(club["coach"], ready)
    set1 = next(p for p in data["periods"] if p["no"] == 1)
    url = f"/api/videos/{ready['id']}/changes"
    told = _period_in(set1, first_serve="them", our_court_side_at_start="far")
    out = club["coach"].post(url, json={"periods": [told]})
    assert out.status_code == 200, out.text
    periods = {p["no"]: p for p in out.json()["periods"]}
    assert periods[1]["first_serve"] == "them" and periods[1]["review"] == "unreviewed"
    # The rules carry the click to the other sets.
    assert (periods[2]["model_first_serve"], periods[2]["model_court_side"]) == ("us", "near")
    rallies = rallies_of(out.json())
    assert rallies[0]["serving"] == "them"  # the set's first server is a fact
    assert rallies[0]["model_serving"] == "them"


def test_model_sets_and_gaps_are_answered_like_proposals(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    data = tagging(club["coach"], ready)
    periods = {p["no"]: p for p in data["periods"]}
    url = f"/api/videos/{ready['id']}/changes"
    body = {
        "periods": [
            _period_in(periods[1], review="confirmed"),
            _period_in(periods[2], start_ms=periods[2]["start_ms"] + 1000),
            _period_in(periods[3], start_rotation=4),
        ]
    }
    out = club["coach"].post(url, json=body)
    assert out.status_code == 200, out.text
    got = {p["no"]: p for p in out.json()["periods"]}
    assert got[1]["review"] == "confirmed" and got[2]["review"] == "corrected"
    assert got[3]["review"] == "unreviewed" and got[3]["start_rotation"] == 4
    # Rejected gap labels are not proposed again; a coach's own timeout blocks the model's.
    gaps = [s for s in out.json()["segments"] if s["kind"] == "set_break"]
    reject = {**_seg_in(gaps[0]), "review": "rejected", "deleted": True}
    change(club["coach"], ready, reject)
    club["coach"].post(f"/api/videos/{ready['id']}/scoring")
    after = tagging(club["coach"], ready)
    assert gaps[0]["start_ms"] not in {s["start_ms"] for s in after["segments"]}
    # A coach's own set replaces the model's open ones.
    mine = {"id": str(uuid.uuid4()), "no": 4, "start_ms": periods[4]["start_ms"], "end_ms": None}
    out = club["coach"].post(url, json={"periods": [mine]})
    assert out.status_code == 200, out.text
    sources = sorted((p["no"], p["source"], p["review"]) for p in out.json()["periods"])
    assert sources == [
        (1, "model", "confirmed"),
        (2, "model", "corrected"),
        (3, "model", "unreviewed"),  # the coach set its starting rotation: kept
        (4, "manual", "unreviewed"),
    ]


def test_timeouts_from_the_benches(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    # A 60 s break inside set 1 with the players at the benches.
    k = 10
    shifted = [
        (s + (60_000 if n > k else 0), e + (60_000 if n > k else 0), srv, w)
        for n, (s, e, srv, w) in enumerate(played)
    ]
    timeout = (shifted[k][1], shifted[k + 1][0])
    analysed(analyser, shifted, timeouts=[timeout])
    kinds = {
        (s["start_ms"], s["end_ms"]): s["kind"]
        for s in tagging(club["coach"], ready)["segments"]
        if s["kind"] != "rally"
    }
    assert kinds[timeout] == "timeout"


# --- rotations ------------------------------------------------------------------------------------


def test_rotation_stats(ready: Any, club: dict[str, TestClient]) -> None:
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "first_serve": "them",
              "start_rotation": 1}  # fmt: skip
    rallies = [
        {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": 1_000 + 20_000 * n,
         "end_ms": 9_000 + 20_000 * n, "won_by": won}
        for n, won in enumerate(["us", "us", "them", "us"])
    ]  # fmt: skip
    r = club["coach"].post(
        f"/api/videos/{ready['id']}/changes", json={"periods": [period], "segments": rallies}
    )
    assert r.status_code == 200, r.text
    body = club["coach"].get(f"/api/videos/{ready['id']}/stats").json()
    lines = {line["rotation"]: line for line in body["match"]["rotations"]}
    assert set(lines) == {1, 2, 3, 4, 5, 6}
    # Rotation 1: side-out won; 6: our serve won, our serve lost, side-out won; then 5.
    assert (lines[1]["side_out_of"], lines[1]["side_out_won"]) == (1, 1)
    assert (lines[6]["rallies"], lines[6]["won"], lines[6]["break_point_of"]) == (3, 2, 2)
    assert body["sets"][0]["rotations"] == body["match"]["rotations"]


# --- the serve model's loop ---------------------------------------------------------------


def test_training_gets_serve_labels_and_stores_the_serve_model(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    analysed(analyser, played[:45])  # set 1, then the recording stops
    data = tagging(club["coach"], video)
    set1 = next(p for p in data["periods"] if p["no"] == 1)
    answers = [{**_seg_in(s), "review": "confirmed", "won_by": w}
               for s, (_, _, _, w) in zip(rallies_of(data), played, strict=False)]  # fmt: skip
    told = _period_in(set1, review="confirmed", first_serve="us", our_court_side_at_start="near")
    r = club["coach"].post(
        f"/api/videos/{video['id']}/changes", json={"segments": answers, "periods": [told]}
    )
    assert r.status_code == 200, r.text
    reviewed = club["coach"].post(f"/api/videos/{video['id']}/rallies-reviewed", json={})
    assert reviewed.status_code == 200, reviewed.text
    vs = club["coach"].get(f"/api/videos/{video['id']}/analysis").json()["scoring"]["vs_coach"]
    assert vs["winner_accuracy"] == 1.0 and vs["winners_known"] == 45
    train = claim_kind(analyser, "train_rallies")
    inputs = analyser.post(f"/api/worker/jobs/{train['id']}/inputs", json={}).json()
    [entry] = inputs["train"]
    assert entry["tracks"] == ["activity", "serve"]
    # Every rally with a known server (the first from the set's first server, the rest from
    # the coach's winners), near when we serve on the near side.
    assert len(entry["serves"]) == 45
    for (start, near), (s, _, server, _) in zip(entry["serves"], played, strict=False):
        assert start == s and near == int(server == "us")
    body = {
        "model": {"name": "rallies-v1-hgb", "version": "abc123"},
        "accepted": False,
        "evaluation": {"summary": {}, "videos": {}},
        "serve": {
            "accepted": True,
            "artifact": {"nas_path": "models/rallies/serve-v1-test.pkl", "sha256": "e" * 64,
                         "bytes": 99, "format": "pickle", "schema_version": 1, "stats": {}},
            "evaluation": {"summary": {"v0": 0.9, "v1": 0.97, "games": 1}, "videos": {}},
        },
        "tracks": {video["id"]: {"serve": {"step_ms": 500, "finder": "v1",
                                            "channels": {"p_near": serve_track(played)}}}},
    }  # fmt: skip
    complete(analyser, train, body)
    state = club["coach"].get(f"/api/videos/{video['id']}/analysis").json()
    assert state["scoring"]["serve_finder"] == "v1"
    club["coach"].post(f"/api/videos/{video['id']}/analyse")
    job = claim_kind(analyser, "analyse")
    found = analyser.post(f"/api/worker/jobs/{job['id']}/inputs", json={}).json()
    assert found["serve_model"]["nas_path"] == "models/rallies/serve-v1-test.pkl"


def test_a_training_redoes_serve_tracks_of_an_older_rule(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    from datetime import timedelta

    from wieplay.core import analysis
    from wieplay.core.models import AnalysisTrack

    played, _ = match()
    analysed(analyser, played)  # an older worker: its serve track names no rule (rule 1)

    def training_entry() -> dict[str, Any]:
        with app.state.sessionmaker() as db:
            analysis.queue_training(db, timedelta(0))
            db.commit()
        train = claim_kind(analyser, "train_rallies")
        inputs = analyser.post(f"/api/worker/jobs/{train['id']}/inputs", json={}).json()
        [entry] = inputs["others"]
        assert entry["tracks"] == ["activity", "serve"]
        return {"job": train, **entry}

    entry = training_entry()
    assert entry["serve_rule"] == 1
    body = {
        "model": {"name": "rallies-v1-hgb", "version": "abc123"},
        "accepted": False,
        "evaluation": {"summary": {}, "videos": {}},
        "tracks": {ready["id"]: {"serve": {"step_ms": 500, "finder": "v0", "rule": 2,
                                            "channels": {"p_near": serve_track(played)}}}},
    }  # fmt: skip
    complete(analyser, entry["job"], body)
    with app.state.sessionmaker() as db:
        rules = db.scalars(
            select(AnalysisTrack.rule)
            .where(AnalysisTrack.kind == "serve")
            .order_by(AnalysisTrack.created_at)
        ).all()
        assert rules == [None, 2]
        run = db.scalars(
            select(ModelRun).where(ModelRun.kind == "score").order_by(ModelRun.created_at.desc())
        ).first()
        assert run is not None and run.params["reason"] == "train"
        assert run.params["serve_rule"] == 2 and run.metrics["serve_rule"] == 2
        # The match's readings are evened out: 54 % of its serves are near ones, read at 97 %,
        # so they are moved a little towards far, and every one keeps its side.
        assert -1.5 < run.metrics["serve_shift"] < 0
        sides = db.scalars(
            select(Segment.serving_court).where(Segment.video_id == uuid.UUID(ready["id"]))
        ).all()
        assert sides.count("near") == 98 and sides.count("far") == 85
    assert training_entry()["serve_rule"] == 2


def test_bad_series_fail_the_job(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(10_000, 20_000)])
    body["tracks"] = {"serve": {"step_ms": 500, "channels": {"p_near": "not a track"}}}
    complete(analyser, job, body, status=422)
    club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(10_000, 20_000)])
    body["tracks"] = {"serve": {"step_ms": 500, "finder": "v1",
                                "channels": {"p_near": tracks.encode(b"\x01")}}}  # fmt: skip
    complete(analyser, job, body, status=422)  # v1 without its training


def test_scoring_permissions(ready: Any, club: dict[str, TestClient], as_: Any) -> None:
    url = f"/api/videos/{ready['id']}/scoring"
    assert as_("basketball-player").post(url).status_code == 404  # another team
    r = club["coach"].post(url)
    assert r.status_code == 200 and r.json()["scoring"]["rallies"] == 0


def test_score_from_the_command_line(
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.conftest import TEST_DB
    from wieplay import cli
    from wieplay.core.config import get_settings

    played, _ = match()
    analysed(analyser, played)
    monkeypatch.setenv("WIEPLAY_DATABASE_URL", TEST_DB)
    get_settings.cache_clear()
    try:
        assert cli.main(["score", "--video", ready["id"]]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert f"{len(played)} rallies, 0 to check, serve v0" in out
    assert "1:25-20 (official 25-20)" in out

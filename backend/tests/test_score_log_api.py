"""Score logs from the club scoreboard through the API (PLAN §10.10): the analysis imports the
game's log through the bridge, the scoring lines it up and follows it where the set ends on the
official score, a coach's fact is kept and the disagreement flagged, the setup's scoreboard step
is done without a box; no log is quiet and tried again a day later."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, claim_kind, complete, make_ready, payload
from tests.test_scoring_api import OFFICIAL, activity, match, rallies_of, serve_track, tagging
from wieplay.api.dev_bridge import FixtureBridgeClient
from wieplay.core import scorelog
from wieplay.core.config import Settings
from wieplay.core.models import AnalysisTrack, ModelRun, Video
from wieplay.core.sync import BridgeError

BOARD_T0 = 1_790_961_000_000
SHIFT = 1_234_567  # board ms = video ms + SHIFT


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def board_log(
    rallies: Sequence[tuple[int, int, str, str]],
    finals: Sequence[tuple[int, int]] = OFFICIAL,
    game: int = 0,
) -> dict[str, Any]:
    """The bridge's answer for a home game (a = home = us): a press 3 s after each rally's end,
    each set's end; `finals` what the board ended each set on."""
    events: list[dict[str, Any]] = []
    set_no, a, b, last = 1, 0, 0, rallies[0][0]
    sets = [0, 0]
    for start, end, _, won in rallies:
        if start - last > 100_000:  # a set break
            sets[0 if a > b else 1] += 1
            events.append(_ev("set_end", events[-1]["board_ms"], set_no, finals[set_no - 1], sets))
            set_no, a, b = set_no + 1, 0, 0
        last = end
        a, b = (a + 1, b) if won == "us" else (a, b + 1)
        events.append(
            _ev(
                "point",
                end + 3_000 + BOARD_T0 + SHIFT,
                set_no,
                (a, b),
                sets,
                side="a" if won == "us" else "b",
            )
        )
    sets[0 if a > b else 1] += 1
    events.append(_ev("set_end", events[-1]["board_ms"], set_no, finals[set_no - 1], sets))
    return {
        "version": 1,
        "game": {"id": game},
        "source": "live_scores_revisions",
        "channel": "kscw",
        "board_names": {"a": "KSCW H3", "b": "GUESTS"},
        "events": events,
        "sets": [{"no": i, "a": f[0], "b": f[1]} for i, f in enumerate(finals, start=1)],
    }


def _ev(kind: str, board: int, no: int, score: Sequence[int], sets: list[int], **extra: Any) -> Any:
    return {
        "kind": kind, "board_ms": board, "server_ms": board + 200, "period": no,
        "points_a": score[0], "points_b": score[1], "sets_a": sets[0], "sets_b": sets[1], **extra,
    }  # fmt: skip


class LogBridge(FixtureBridgeClient):
    def __init__(self, answer: Mapping[str, Any] | Exception) -> None:
        self.answer = answer
        self.asked: list[int] = []

    def score_log(self, game_id: int) -> Mapping[str, Any]:
        self.asked.append(game_id)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {**self.answer, "game": {"id": game_id}}


def analyse(analyser: TestClient, rallies: list[tuple[int, int, str, str]], sure: float) -> None:
    """The analysis with serve evidence this sure (0.5: it says nothing)."""
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(s, e) for s, e, _, _ in rallies])
    body["rallies"]["proposals"] = [
        {"start_ms": s, "end_ms": e, "confidence": 0.9} for s, e, _, _ in rallies
    ]
    body["tracks"] = {
        "serve": {
            "step_ms": 500,
            "finder": "v0",
            "channels": {"p_near": serve_track(rallies, sure)},
        },
        "activity": {"step_ms": 1000, "channels": activity()},
    }
    complete(analyser, job, body)


def test_the_analysis_imports_the_log_and_the_score_follows_it(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    bridge = LogBridge(board_log(played))
    app.state.bridge_client_factory = lambda: bridge
    analyse(analyser, played, sure=0.5)  # the serves say nothing

    assert bridge.asked == [video["game_id"]]
    rallies = rallies_of(tagging(coach, video))
    assert len(rallies) == len(played)
    for seg, (_, _, _, won) in zip(rallies, played, strict=True):
        assert seg["log_won_by"] == won
        assert seg["model_won_by"] == won  # the log decided
        assert abs(seg["log_at_ms"] - (seg["end_ms"] + 3_000)) <= 3_000
    last = rallies[len(rallies) - 1]
    assert (last["log_score_us"], last["log_score_them"]) == OFFICIAL[-1]
    assert (last["score_us_after"], last["score_them_after"]) == OFFICIAL[-1]

    log = coach.get(f"/api/videos/{video['id']}/analysis").json()["scoring"]["log"]
    assert log["trusted"] and log["sets_trusted"] == 4 and log["us"] == "a"
    assert log["matched"] == log["points"] == len(played)
    assert log["shift_ms"] is not None and abs(log["shift_ms"] + SHIFT + BOARD_T0) <= 3_000
    assert log["path_agrees"] == log["rallies_told"] == len(played)
    assert log["sets"]["2"]["final"] == list(OFFICIAL[1])
    board = coach.get(f"/api/videos/{video['id']}/scoreboard").json()
    assert board["region"] is None and board["log"]["trusted"] is True
    assert [s["trusted"] for s in board["log"]["sets"]] == [True] * 4

    with app.state.sessionmaker() as db:
        runs = db.scalars(select(ModelRun).where(ModelRun.kind == "score_log")).all()
        assert [r.params["points"] for r in runs] == [len(played)]
        assert runs[0].metrics["trusted"] is True
        # Already there: the next analysis asks no more.
        row = db.get(Video, video["id"])
        assert row is not None and not scorelog.needs_log(db, row)


def test_a_coach_fact_is_kept_and_the_disagreement_flagged(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    app.state.bridge_client_factory = lambda: LogBridge(board_log(played))
    analyse(analyser, played, sure=0.97)
    first = rallies_of(tagging(coach, video))[0]
    wrong = "them" if played[0][3] == "us" else "us"
    keep = ("id", "kind", "start_ms", "end_ms", "won_by", "review")
    r = coach.post(
        f"/api/videos/{video['id']}/changes",
        json={
            "segments": [
                {**{k: first[k] for k in keep}, "base_version": first["version"], "won_by": wrong}
            ]
        },
    )
    assert r.status_code == 200, r.text
    seg = next(s for s in rallies_of(tagging(coach, video)) if s["id"] == first["id"])
    assert seg["won_by"] == wrong  # never changed
    assert seg["log_won_by"] == played[0][3]
    assert seg["score_conflict"] is True and seg["score_note"] in ("log", "coach")
    log = coach.get(f"/api/videos/{video['id']}/analysis").json()["scoring"]["log"]
    assert log["vs_coach"] == {"winner_accuracy": 0.0, "winners_known": 1}


def test_a_set_off_the_official_score_says_nothing(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    finals = [OFFICIAL[0], (25, 23), *OFFICIAL[2:]]  # the board ended set 2 elsewhere
    app.state.bridge_client_factory = lambda: LogBridge(board_log(played, finals))
    analyse(analyser, played, sure=0.97)
    data = tagging(coach, video)
    set2 = next(p for p in data["periods"] if p["no"] == 2)
    in_set2 = [s for s in rallies_of(data) if s["period_id"] == set2["id"]]
    assert in_set2 and all(s["log_won_by"] is None for s in in_set2)
    board = coach.get(f"/api/videos/{video['id']}/scoreboard").json()["log"]
    assert board["trusted"] is False
    assert [s["trusted"] for s in board["sets"]] == [True, False, True, True]
    assert board["sets"][1]["official"] == list(OFFICIAL[1])


def test_no_log_is_quiet_and_tried_again_a_day_later(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    missing = LogBridge(BridgeError("bridge answered 404 for /score-log"))
    app.state.bridge_client_factory = lambda: missing
    analyse(analyser, played, sure=0.97)  # the analysis goes through all the same
    assert club["coach"].get(f"/api/videos/{video['id']}/scoreboard").json()["log"] is None
    scorelog._tried.clear()
    now = datetime.now(UTC)
    with app.state.sessionmaker() as db:
        assert scorelog.import_missing(db, missing, now) == 0
        assert scorelog.import_missing(db, missing, now + timedelta(hours=2)) == 0
        assert missing.asked == [video["game_id"]] * 2  # analysis, first sweep; not the second
        # A day later, Wiedisync has it.
        found = LogBridge(board_log(played))
        assert scorelog.import_missing(db, found, now + timedelta(hours=25)) == 1
        assert db.scalars(select(AnalysisTrack).where(AnalysisTrack.kind == "score_log")).all()
    # A game without a log: "none", nothing stored.
    with app.state.sessionmaker() as db:
        row = db.get(Video, video["id"])
        assert row is not None
        empty = LogBridge({"version": 1, "source": None, "events": [], "sets": []})
        assert scorelog.fetch(db, row, empty).status == "none"
        assert scorelog.fetch(db, row, LogBridge({"version": 7})).status == "unavailable"


def test_sets_from_the_log_split_at_the_longest_break() -> None:
    from types import SimpleNamespace

    from wieplay.ml import scorelog as sl

    starts = [0, 30, 60, 90, 120, 400, 430, 460, 490, 520]  # s; a set break before rally 5
    rallies = [
        SimpleNamespace(id=f"r{i}", start_ms=s * 1000, end_ms=s * 1000 + 8000)
        for i, s in enumerate(starts)
    ]
    told = {1: 1, 2: 1, 3: 1, 6: 2, 7: 2, 8: 2}
    verdicts = {f"r{i}": sl.Verdict(f"r{i}", "a", 1.0, no) for i, no in told.items()}
    alignment = sl.Alignment(0, [], verdicts, {}, {}, 6, 6)
    ev = scorelog.Evidence(None, alignment, {}, "a")  # type: ignore[arg-type]
    assert scorelog.set_spans(ev, rallies) == [(1, 0, 4), (2, 5, 9)]  # type: ignore[arg-type]
    # Sets out of order: no spans from the log.
    verdicts["r9"] = sl.Verdict("r9", "a", 1.0, 1)
    assert scorelog.set_spans(ev, rallies) is None  # type: ignore[arg-type]

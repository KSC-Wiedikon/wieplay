"""Official scoresheets through the API (PLAN §10.3a): a coach types the sheet, every set is
decoded and checked against the official score, the sets get their first server and lineups,
the scoring lines the sheet up with the rallies (its winners in the score line, its timeouts as
breaks, a coach's answer kept and flagged), repairs for a typo, who may see and type it."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, make_ready
from tests.test_scoring_api import OFFICIAL, analysed, match, rallies_of, tagging
from wieplay.core.config import Settings
from wieplay.core.models import AuditLog, ModelRun, Scoresheet, ScoresheetRevision, Segment
from wieplay.ml import sheet as sh

LINEUP_US = [7, 3, 11, 5, 9, 1]  # #3 is the roster's setter (position II)
LINEUP_THEM = [4, 8, 12, 14, 2, 6]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)  # a home game, official sets OFFICIAL


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def sheet_sets(played: list[tuple[int, int, str, str]]) -> list[dict[str, Any]]:
    """The sheet a scorer writes for the fixtures' match: per set the boxes of its points."""
    out: list[dict[str, Any]] = []
    for no in range(1, len(OFFICIAL) + 1):
        first = "us" if no % 2 == 1 else "them"
        n = sum(OFFICIAL[no - 1])
        start = sum(sum(s) for s in OFFICIAL[: no - 1])
        winners = [w for _, _, _, w in played[start : start + n]]
        grids, circled = sh.encode(first, winners)  # type: ignore[arg-type]
        out.append(
            {
                "no": no,
                "first_serve": first,
                "lineup_us": LINEUP_US,
                "lineup_them": LINEUP_THEM,
                "boxes_us": grids["us"],
                "boxes_them": grids["them"],
                "circled_us": list(circled["us"] or []) or None,
                "circled_them": list(circled["them"] or []) or None,
            }
        )
    return out


def put(client: TestClient, video: Any, sets: list[dict[str, Any]], **body: Any) -> Any:
    return client.put(
        f"/api/videos/{video['id']}/scoresheet", json={"sets": sets, "base_version": 0, **body}
    )


def test_a_typed_sheet_lines_up_the_whole_match(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, db_engine: Any
) -> None:
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    empty = coach.get(f"/api/videos/{ready['id']}/scoresheet").json()
    assert empty["exists"] is False and empty["can_edit"] is True and empty["checks"] == []
    assert empty["official_sets"] == [list(s) for s in OFFICIAL]
    assert {"number": 3, "setter": True, "libero": False} in empty["roster"]

    sets = sheet_sets(played)
    sets[0]["timeouts"] = [{"side": "them", "own": 4, "opp": 5}]  # them at 5:4 (us:them)
    r = put(coach, ready, sets)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["exists"] and out["version"] == 1 and out["trusted"] is True
    assert [c["status"] for c in out["checks"]] == ["consistent"] * 4
    assert [c["use"] for c in out["checks"]] == ["path"] * 4
    assert [c["final"] for c in out["checks"]] == [list(s) for s in OFFICIAL]
    assert out["entered_by"] and out["entered_at"]
    # The sheet's own numbers: every point of set 1 counted for both teams.
    set1 = out["checks"][0]["stats"]
    assert set1["us"]["team"]["points"] == 25 and set1["them"]["team"]["points"] == 20
    assert out["match"]["us"]["team"]["points"] == sum(a for a, _ in OFFICIAL)
    assert out["match_by_setter"] is True  # #3 is the roster's setter

    data = tagging(coach, ready)
    periods = sorted(data["periods"], key=lambda p: p["no"])
    assert [p["first_serve"] for p in periods] == ["us", "them", "us", "them"]
    assert all(p["start_lineup"] == LINEUP_US for p in periods)
    assert all(p["start_rotation"] == 2 for p in periods)  # the setter in zone II
    rallies = rallies_of(data)
    assert all(r["score_source"] == "sheet" for r in rallies)
    assert [r["sheet_won_by"] for r in rallies] == [w for _, _, _, w in played]
    assert [r["sheet_point"] for r in rallies[:3]] == [1, 2, 3]
    assert (rallies[-1]["score_us_after"], rallies[-1]["score_them_after"]) == OFFICIAL[-1]
    # Their timeout at 5:4 (us:them): a break with their name, after the point that made it.
    [timeout] = [s for s in data["segments"] if s["kind"] == "timeout"]
    assert timeout["source"] == "import" and timeout["called_by"] == "them"
    k = next(i for i, r in enumerate(rallies) if (r["score_us_after"], r["score_them_after"])
             == (5, 4))  # fmt: skip
    assert timeout["start_ms"] == rallies[k]["end_ms"]
    assert timeout["end_ms"] == rallies[k + 1]["start_ms"]
    again = coach.get(f"/api/videos/{ready['id']}/scoresheet").json()
    assert again["checks"][0]["timeouts"][0]["at_ms"] == rallies[k]["end_ms"]
    assert again["checks"][0]["scoring"]["aligned"] == 45
    analysis = coach.get(f"/api/videos/{ready['id']}/analysis").json()
    assert analysis["scoring"]["sheet"]["sets_lined_up"] == 4
    assert [s["sheet"] for s in analysis["scoring"]["sets"]] == ["path"] * 4
    # Kept with its revision and in the audit log; no names in the stored sheet.
    with Session(db_engine) as db:
        row = db.scalars(select(Scoresheet)).one()
        assert row.sets[0]["lineup_us"] == LINEUP_US
        assert db.scalars(select(ScoresheetRevision)).one().action == "create"
        assert db.scalars(select(AuditLog).where(AuditLog.action == "scoresheet_saved")).one()
        # The score run says it used the sheet (written, not only set on the object).
        run = db.scalars(
            select(ModelRun).where(ModelRun.kind == "score").order_by(ModelRun.created_at.desc())
        ).first()
        assert run is not None and run.params["scoresheet"] is True
        assert run.params["reason"] == "scoresheet"


def test_a_coachs_winner_wins_and_the_disagreement_is_flagged(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    assert put(coach, ready, sheet_sets(played)).status_code == 200
    rally = rallies_of(tagging(coach, ready))[5]
    wrong = "them" if played[5][3] == "us" else "us"
    change = {**{k: rally[k] for k in ("id", "kind", "start_ms", "end_ms")}, "won_by": wrong,
              "base_version": rally["version"], "review": rally["review"]}  # fmt: skip
    r = coach.post(f"/api/videos/{ready['id']}/changes", json={"segments": [change]})
    assert r.status_code == 200, r.text
    after = rallies_of(tagging(coach, ready))[5]
    # The coach's winner counts; the score before it came from the sheet.
    assert after["won_by"] == wrong and after["score_source"] == "sheet"
    assert after["sheet_won_by"] == played[5][3]
    assert after["score_conflict"] is True and after["score_note"] == "sheet"


def test_a_typo_is_named_and_repaired(ready: Any, club: dict[str, TestClient]) -> None:
    played, _ = match()
    sets = sheet_sets(played)
    # Set 2, their first box of position III: a pair of digits swapped.
    grid = sets[1]["boxes_them"]
    value = next(v for col in grid for v in col if v is not None and v >= 12 and v % 11)
    p, b = next((p, b) for p, col in enumerate(grid) for b, v in enumerate(col) if v == value)
    grid[p][b] = int(str(value)[::-1])
    checked = club["coach"].post(f"/api/videos/{ready['id']}/scoresheet/check", json={"sets": sets})
    assert checked.status_code == 200, checked.text
    c = checked.json()["checks"][1]
    assert c["status"] == "inconsistent" and c["use"] == "hint"
    assert c["issues"][0]["severity"] == "error"
    assert c["repair"]["box"] == {"side": "them", "position": p + 1, "box": b + 1}
    assert (c["repair"]["old"], c["repair"]["new"]) == (int(str(value)[::-1]), value)
    assert checked.json()["trusted"] is False
    # Nothing was saved.
    assert club["coach"].get(f"/api/videos/{ready['id']}/scoresheet").json()["exists"] is False


def test_the_official_score_is_checked(ready: Any, club: dict[str, TestClient]) -> None:
    played, _ = match()
    sets = sheet_sets(played)[:1]
    # A sheet for 25:20 checked against an official 25:18 in set 3's place: renumbered.
    sets[0]["no"] = 3
    out = put(club["coach"], ready, sets).json()
    [c] = out["checks"]
    assert c["status"] == "inconsistent"
    assert any(i["code"] == "official" for i in c["issues"])
    assert out["trusted"] is False


def test_stale_saves_and_removal(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, db_engine: Any
) -> None:
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    sets = sheet_sets(played)
    assert put(coach, ready, sets).json()["version"] == 1
    assert put(coach, ready, sets).status_code == 200  # the same again: a resend
    sets[0]["lineup_them"] = [4, 8, 12, 14, 2, 10]
    assert put(coach, ready, sets).status_code == 409  # based on version 0, now 1
    assert put(coach, ready, sets, base_version=1).json()["version"] == 2
    r = coach.delete(f"/api/videos/{ready['id']}/scoresheet")
    assert r.status_code == 204
    assert coach.get(f"/api/videos/{ready['id']}/scoresheet").json()["exists"] is False
    rallies = rallies_of(tagging(coach, ready))
    assert all(r["sheet_point"] is None and r["score_source"] == "model" for r in rallies)
    assert coach.delete(f"/api/videos/{ready['id']}/scoresheet").status_code == 404
    # Typed again later: the same row comes back.
    assert put(coach, ready, sets).json()["version"] == 4
    with Session(db_engine) as db:
        actions = [r.action for r in db.scalars(select(ScoresheetRevision).order_by(
            ScoresheetRevision.id))]  # fmt: skip
        assert actions == ["create", "update", "delete", "restore"]
        assert db.scalars(select(Segment).where(Segment.sheet_point.is_not(None))).first()


def test_a_set_switched_off_and_the_whole_sheet_off(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    sets = sheet_sets(played)
    sets[2]["use"] = False
    out = put(club["coach"], ready, sets).json()
    assert [c["use"] for c in out["checks"]] == ["path", "path", None, "path"]
    assert out["trusted"] is False  # set 3 is not used
    off = put(club["coach"], ready, sets, use_in_scoring=False, base_version=1).json()
    assert all(c["use"] is None for c in off["checks"])
    assert all(r["sheet_point"] is None for r in rallies_of(tagging(club["coach"], ready)))


def test_who_sees_and_types_the_sheet(ready: Any, club: dict[str, TestClient], as_: Any) -> None:
    played, _ = match()
    sets = sheet_sets(played)
    other = as_("player-d1")
    assert other.get(f"/api/videos/{ready['id']}/scoresheet").status_code == 404
    assert put(other, ready, sets).status_code == 404
    player = as_("player-h3")  # players tag their games, so they type the sheet too
    assert player.get(f"/api/videos/{ready['id']}/scoresheet").json()["can_edit"] is True
    assert put(player, ready, sets).status_code == 200
    basketball = as_("basketball-player")
    assert basketball.get(f"/api/videos/{ready['id']}/scoresheet").status_code == 404


def test_bad_input_is_refused(ready: Any, club: dict[str, TestClient]) -> None:
    coach = club["coach"]
    bad = [
        [{"no": 1, "boxes_us": [[100]]}],  # a score over 99
        [{"no": 1, "boxes_us": [[1] * 9]}],  # nine boxes in a position
        [{"no": 5, "boxes_us": [[1] * 7]}],  # seven in a deciding set
        [{"no": 1}, {"no": 1}],  # the same set twice
        [{"no": 6}],
        [{"no": 1, "lineup_us": [1, 2, 3]}],
        [{"no": 1, "start": "25:00"}],
        [{"no": 1, "subs": [{"side": "us", "position": 7}]}],
        [{"no": 1, "timeouts": [{"side": "us", "own": 1, "opp": 2, "name": "x"}]}],
    ]
    for sets in bad:
        r = put(coach, ready, sets)
        assert r.status_code == 422, (sets, r.text)


def test_no_scoresheet_is_remembered(ready: Any, club: dict[str, TestClient]) -> None:
    r = club["coach"].patch(f"/api/videos/{ready['id']}/setup", json={"no_scoresheet": True})
    assert r.status_code == 200 and r.json()["no_scoresheet"] is True
    played, _ = match()
    assert put(club["coach"], ready, sheet_sets(played)).status_code == 200
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["no_scoresheet"] is False

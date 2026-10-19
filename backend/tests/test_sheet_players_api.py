"""The official scoresheet for the players and the stats, through the API (PLAN §10.3a, §10.5,
§9.7): who was on court per rally, the players job's inputs, the serves' players from the
sheet's servers (a coach's number always wins), the numbers per player, and the sheet's
timeouts, substitutions and cards at their breaks. Made-up data only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, make_ready
from tests.test_players_api import event, model_event, tag
from tests.test_scoresheet_api import LINEUP_THEM, LINEUP_US, put, sheet_sets
from tests.test_scoring_api import OFFICIAL, analysed, match, rallies_of, tagging
from wieplay.core import lineups
from wieplay.core import players as core_players
from wieplay.core.config import Settings
from wieplay.core.models import Video


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)  # a home game, official sets OFFICIAL


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def with_sheet(ready: Any, club: dict[str, TestClient], analyser: TestClient, **extra: Any) -> Any:
    played, _ = match()
    analysed(analyser, played)
    sets = sheet_sets(played)
    for k, v in extra.items():
        sets[0][k] = v
    r = put(club["coach"], ready, sets)
    assert r.status_code == 200, r.text
    return played


def test_every_rally_has_its_lineup_and_the_notes_their_breaks(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient
) -> None:
    winners = [w for _, _, _, w in match()[0][: sum(OFFICIAL[0])]]
    score = [(winners[:k].count("us"), winners[:k].count("them")) for k in range(len(winners))]
    sub_at, card_at = score[12], score[20]  # us:them after 12 and 20 points of set 1
    played = with_sheet(
        ready, club, analyser,
        timeouts=[{"side": "them", "own": 4, "opp": 5}],
        subs=[{"side": "us", "position": 2, "number": 14, "at": list(sub_at)}],
        sanctions=[{"side": "them", "kind": "W", "own": card_at[1], "opp": card_at[0]}],
    )  # fmt: skip
    coach = club["coach"]
    court = coach.get(f"/api/videos/{ready['id']}/scoresheet/court")
    assert court.status_code == 200, court.text
    body = court.json()
    assert len(body["rallies"]) == len(played) == sum(sum(s) for s in OFFICIAL)
    first = body["rallies"][0]
    assert (first["set"], first["point"], first["serving"]) == (1, 1, "us")
    assert first["server"] == LINEUP_US[0] and first["us"] == LINEUP_US
    assert first["them"] == LINEUP_THEM
    for r in body["rallies"]:
        zones = r[r["serving"]]
        assert r["server"] == zones[0]  # zone I serves
    by_kind = {n["kind"]: n for n in body["notes"]}
    assert set(by_kind) == {"timeout", "sub", "sanction"}
    timeout = by_kind["timeout"]
    assert timeout["side"] == "them" and timeout["score"] == [5, 4]
    assert timeout["start_ms"] is not None and timeout["line"] == [5, 4]
    # The made-up match has 11 s between all rallies: too short for a timeout, flagged.
    assert timeout["issues"] == ["short_break"]
    sub = by_kind["sub"]
    assert sub["number_in"] == 14 and sub["position"] == 2 and sub["score"] == list(sub_at)
    assert sub["at_points"] == 12
    # From the substitution on, #14 is on court instead of #3.
    after = [r for r in body["rallies"] if r["set"] == 1 and r["point"] > 12]
    assert after and all(14 in r["us"] and 3 not in r["us"] for r in after)
    card = by_kind["sanction"]
    assert card["sanction"] == "W" and card["side"] == "them" and card["score"] == list(card_at)
    assert "number" not in card and card["number_in"] is None  # a card names no person
    assert body["liberos"] == {"us": [1]}

    # The players job gets each rally's lineup and the liberos, by number.
    with app.state.sessionmaker() as db:
        video = db.get(Video, uuid.UUID(ready["id"]))
        assert video is not None
        found = lineups.for_video(db, video)
        assert found is not None
        rallies = core_players.rallies_with_sides(db, video, found)
        assert all(r["sheet"]["server"] is not None for r in rallies)
        assert rallies[0]["sheet"] == {
            "set": 1, "point": 1, "serving": "us", "server": 7, "us": LINEUP_US,
            "them": LINEUP_THEM,
        }  # fmt: skip
        extra = lineups.job_extra(found)
        assert extra["liberos"] == {"us": [1]} and extra["middles"] == {"us": [11]}
        assert 14 in extra["numbers"]["us"]
    # The scoring's metrics count the notes.
    analysis = coach.get(f"/api/videos/{ready['id']}/analysis").json()
    notes = analysis["scoring"]["sheet"]["notes"]
    assert notes["timeout"]["placed"] == 1 and notes["timeout"]["flagged"] == 1
    assert notes["sub"]["line_checked"] == 1 and notes["sub"]["line_differs"] == 0


def test_a_video_without_a_sheet_has_no_lineups(ready: Any, club: dict[str, TestClient]) -> None:
    body = club["coach"].get(f"/api/videos/{ready['id']}/scoresheet/court").json()
    assert body == {"rallies": [], "notes": [], "liberos": {}}


def test_the_sheet_s_server_is_the_serve_s_player_and_a_coach_s_number_wins(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played = with_sheet(ready, club, analyser)
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}"
    rallies = rallies_of(tagging(coach, ready))
    # Rally 0: we serve (#7). The first rally they serve: their zone I.
    k_them = next(i for i, (_, _, server, _) in enumerate(played) if server == "them")
    k_us = [i for i, (_, _, server, _) in enumerate(played) if server == "us"]
    assert k_us[0] == 0
    loose = tag(rallies[0]["start_ms"] + 500, code="S")  # the coach's serve, no number
    named = tag(rallies[k_us[1]]["start_ms"] + 500, number=9, code="S")  # the coach says #9
    wrong_side = tag(rallies[k_us[2]]["start_ms"] + 500, side="them", code="S")  # we served
    r = coach.post(f"{url}/changes", json={"events": [loose, named, wrong_side]})
    assert r.status_code == 200, r.text
    theirs = model_event(app, ready, rallies[k_them]["start_ms"] + 300, "them", code="S")

    state = coach.get(f"{url}/players").json()  # attributes what is new (no players run)
    assert state["open_suggestions"] == 1
    them_server = coach.get(f"{url}/scoresheet/court").json()["rallies"][k_them]["server"]
    row = event(app, theirs)
    assert row.number == them_server and row.track_ref is not None
    assert row.track_ref.startswith("sheet:1.")
    tags = {e["id"]: e for e in coach.get(f"{url}/tagging").json()["events"]}
    assert tags[loose["id"]]["number"] is None  # a suggestion beside it, not written
    assert tags[named["id"]]["number"] == 9  # the coach's number stays
    assert tags[wrong_side["id"]]["number"] is None

    r = coach.post(f"{url}/players/apply", json={})
    assert r.status_code == 200 and r.json()["applied"] == 1
    tags = {e["id"]: e for e in coach.get(f"{url}/tagging").json()["events"]}
    assert tags[loose["id"]]["number"] == 7 and tags[loose["id"]]["member_id"] == 101
    assert event(app, loose["id"]).track_ref == "sheet"


def test_the_stats_count_serves_and_plus_minus_per_player(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played = with_sheet(ready, club, analyser)
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}"
    rallies = rallies_of(tagging(coach, ready))
    ace = {**tag(rallies[0]["start_ms"] + 500, code="S"), "attributes": {"grade": "#"}}
    assert coach.post(f"{url}/changes", json={"events": [ace]}).status_code == 200
    body = coach.get(f"{url}/stats").json()
    sheet = body["sheet_players"]
    assert sheet is not None and sheet["sets"] == [1, 2, 3, 4] and sheet["by_setter"] is True
    rows = {(p["side"], p["number"]): p for p in sheet["players"]}
    assert sum(p["served"] for p in rows.values()) == len(played)
    seven = rows[("us", 7)]
    assert seven["name"] and seven["member_id"] == 101
    assert seven["aces"] == 1 and seven["tagged"] == 1
    us_points = sum(a for a, _ in OFFICIAL)
    them_points = sum(b for _, b in OFFICIAL)
    assert seven["on_court"] == len(played)
    assert seven["plus_minus"] == us_points - them_points == sum(seven["by_rotation"])
    assert rows[("them", LINEUP_THEM[0])]["plus_minus"] == them_points - us_points
    assert seven["won_on_serve"] <= seven["served"] and seven["longest_run"] >= 1

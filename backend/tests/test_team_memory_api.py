"""What WiePlay knows about a team (PLAN §10.5b), through the API: the numbers and rotations
learnt from a game's sheet, the team page's section, a coach's fix of a number ↔ player link
(the season's games follow), a second game without a sheet getting its servers from the
team's usual rotation, and nothing of the opponent remembered. Made-up data only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeStore
from tests.media_helpers import H3, worker_client
from tests.test_analysis_api import ANALYSE_CAPS, make_ready
from tests.test_players_api import event, model_event, tag
from tests.test_scoresheet_api import LINEUP_THEM, LINEUP_US, put, sheet_sets
from tests.test_scoring_api import analysed, match, rallies_of, tagging
from wieplay.core import team_memory
from wieplay.core.config import Settings
from wieplay.core.models import AuditLog, Video, VideoRoster
from wieplay.core.players import EventPlayer
from wieplay.core.team_memory import TeamPlayer

# LINEUP_US turned to start at the roster's setter (#3).
ORDER = [3, 11, 5, 9, 1, 7]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def game_with_sheet(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> Any:
    video = make_ready(app, club, store, k=0)
    played, _ = match()
    analysed(analyser, played)
    r = put(club["coach"], video, sheet_sets(played))
    assert r.status_code == 200, r.text
    return video


def memory(client: TestClient) -> dict[str, Any]:
    r = client.get(f"/api/teams/{H3}/memory")
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_the_team_page_shows_what_a_game_named(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    game_with_sheet(app, club, store, analyser)
    body = memory(club["coach"])
    assert body["available"] and body["games"] == 1 and body["can_fix"]
    named = {p["number"]: p for p in body["players"] if p["games"]}
    assert set(named) == set(LINEUP_US)
    assert all(p["sets"] == 4 for p in named.values())  # the same six in all four sets
    assert named[3]["name"] and named[3]["source"] == "roster"
    # The opponent's numbers are not remembered (only our roster's and our sheet's are).
    learned = {p["number"] for p in body["players"] if p["source"] == "learned"}
    assert not learned & (set(LINEUP_THEM) - set(LINEUP_US))
    rot = body["rotations"]
    assert rot["sets"] == 4
    assert rot["orders"][0]["order"] == ORDER  # the service order, the setter first
    assert rot["start_zones"] == {"2": 1.0}  # the setter in zone II at every first serve
    assert rot["setters"] == [3] and rot["liberos"] == [1]
    assert rot["check"]["serves"] == 0  # one game: nothing to check it against yet


def test_a_coach_fixes_who_wears_a_number_and_the_season_follows(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient, as_: Any
) -> None:
    video = game_with_sheet(app, club, store, analyser)
    coach = club["coach"]
    # A tag of ours with #5 (it takes the roster snapshot).
    t = tag(rallies_of(tagging(coach, video))[0]["start_ms"] + 2000, number=5)
    assert coach.post(f"/api/videos/{video['id']}/changes", json={"events": [t]}).status_code == 200
    body = memory(coach)
    five = next(p for p in body["players"] if p["number"] == 5)
    other = next(m for m in body["members"] if m["id"] != five["member_id"])

    r = coach.put(f"/api/teams/{H3}/memory/players/5", json={"member_id": other["id"]})
    assert r.status_code == 200, r.text
    fixed = next(p for p in r.json()["players"] if p["number"] == 5)
    assert fixed["member_id"] == other["id"] and fixed["fixed"] and fixed["source"] == "coach"
    with app.state.sessionmaker() as db:
        snap = db.scalars(
            select(VideoRoster).where(
                VideoRoster.video_id == uuid.UUID(video["id"]), VideoRoster.number == 5
            )
        ).one()
        assert snap.member_id == other["id"]
        assert event(app, t["id"]).member_id == other["id"]
        assert db.scalars(select(AuditLog).where(AuditLog.action == "team_number_fixed")).one()
    # A number that is none of ours.
    r = coach.put(f"/api/teams/{H3}/memory/players/42", json={"hidden": True})
    assert r.status_code == 200
    assert next(p for p in r.json()["players"] if p["number"] == 42)["hidden"]
    # Undone: back to the roster's member.
    r = coach.delete(f"/api/teams/{H3}/memory/players/5")
    assert r.status_code == 200
    again = next(p for p in r.json()["players"] if p["number"] == 5)
    assert again["member_id"] == five["member_id"] and not again["fixed"]
    # Players see the section but do not fix it; nobody picks a stranger.
    player = as_("player-h3")
    seen = memory(player)
    assert seen["can_fix"] is False and seen["members"] == []
    assert player.put(f"/api/teams/{H3}/memory/players/5", json={"member_id": 1}).status_code == 403
    r = coach.put(f"/api/teams/{H3}/memory/players/5", json={"member_id": 999_999})
    assert r.status_code == 422


def test_a_second_game_without_a_sheet_takes_its_servers_from_the_memory(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    from wieplay.core import lineups

    game_with_sheet(app, club, store, analyser)
    second = make_ready(app, club, store, k=1)
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    rallies = rallies_of(tagging(coach, second))
    # The coach names the first server of set 1 (#7, zone I): one fact pins the rotation.
    first = tag(rallies[0]["start_ms"] + 300, number=7, code="S")
    r = coach.post(f"/api/videos/{second['id']}/changes", json={"events": [first]})
    assert r.status_code == 200, r.text
    with app.state.sessionmaker() as db:
        video = db.get(Video, uuid.UUID(second["id"]))
        assert video is not None
        prior = lineups.prior_for_video(db, video)
        assert prior is not None and prior.sets[1]["seen"] == 1
        assert prior.sets[1]["start"] == LINEUP_US and prior.sets[1]["p"] > 0.95
        # Without a fact the other sets' rotations stay a guess (the usual one, not sure).
        assert prior.sets[2]["p"] < lineups.MEMORY_APPLY
        sure = [
            (uuid.UUID(x["id"]), prior.servers[uuid.UUID(x["id"])])
            for x in rallies[1:]
            if uuid.UUID(x["id"]) in prior.servers
            and prior.rotation[uuid.UUID(x["id"])][0] == 1
            and prior.servers[uuid.UUID(x["id"])][1] >= lineups.MEMORY_APPLY
        ][:2]
        assert len(sure) == 2
        for rid, (number, _) in sure:
            _, rot = prior.rotation[rid]
            assert number == LINEUP_US[rot % 6]  # the usual order, walked
    starts = {uuid.UUID(x["id"]): x["start_ms"] for x in rallies}
    # WiePlay's serve suggestions there, nobody's number: the memory names the server.
    later = [model_event(app, second, starts[rid] + 300, "us", code="S") for rid, _ in sure]
    coach.get(f"/api/videos/{second['id']}/players")  # attributes what is new
    for eid, (_, (want, _)) in zip(later, sure, strict=True):
        ev = event(app, eid)
        assert ev.number == want and (ev.track_ref or "").startswith("memory:")
        with app.state.sessionmaker() as db:
            ep = db.get(EventPlayer, uuid.UUID(eid))
            assert ep is not None and ep.method == "memory" and (ep.confidence or 0) >= 0.8


def test_nothing_of_the_opponent_and_no_appearance_is_kept(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    game_with_sheet(app, club, store, analyser)
    memory(club["coach"])
    with app.state.sessionmaker() as db:
        rows = db.scalars(select(TeamPlayer)).all()
        assert rows and {r.team_id for r in rows} == {H3}
        mem = team_memory.get(db, H3, refresh_stale=False)
        assert mem is not None
        text = str(mem.rotations) + str(mem.models)
        for theirs in set(LINEUP_THEM) - set(LINEUP_US):
            assert f"'number': {theirs}," not in text
        # Numbers, counts and rotations only: no colour, crop or embedding fields.
        assert set(TeamPlayer.__table__.columns.keys()) <= {
            "id", "team_id", "lineage_id", "number", "member_id", "libero", "games", "answers",
            "sets", "last_on", "source", "ambiguous", "fixed", "hidden", "fixed_by", "fixed_at",
            "updated_at",
        }  # fmt: skip

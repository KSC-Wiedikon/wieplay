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
from wieplay.core.models import AuditLog, Event, Video, VideoRoster
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


def test_a_fix_keeps_a_game_that_named_someone_else_for_the_number(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    video = game_with_sheet(app, club, store, analyser)
    coach = club["coach"]
    t = tag(rallies_of(tagging(coach, video))[0]["start_ms"] + 2000, number=5)
    assert coach.post(f"/api/videos/{video['id']}/changes", json={"events": [t]}).status_code == 200
    body = memory(coach)
    five = next(p for p in body["players"] if p["number"] == 5)
    guest, other = [m["id"] for m in body["members"] if m["id"] != five["member_id"]][:2]
    # That day #5 was worn by someone else (the coach's answer for this game).
    with app.state.sessionmaker() as db:
        snap = db.scalars(
            select(VideoRoster).where(
                VideoRoster.video_id == uuid.UUID(video["id"]),
                VideoRoster.side == "us",
                VideoRoster.number == 5,
            )
        ).first()
        if snap is None:
            db.add(
                VideoRoster(video_id=uuid.UUID(video["id"]), side="us", number=5, member_id=guest)
            )
        else:
            snap.member_id = guest
        ev = db.get(Event, uuid.UUID(t["id"]))
        assert ev is not None
        ev.member_id = guest
        db.commit()

    r = coach.put(f"/api/teams/{H3}/memory/players/5", json={"member_id": other})
    assert r.status_code == 200, r.text
    with app.state.sessionmaker() as db:
        snap = db.scalars(
            select(VideoRoster).where(
                VideoRoster.video_id == uuid.UUID(video["id"]), VideoRoster.number == 5
            )
        ).one()
        assert snap.member_id == guest
    assert event(app, t["id"]).member_id == guest


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
            "sets", "last_on", "videos", "source", "ambiguous", "fixed", "hidden", "fixed_by",
            "fixed_at", "updated_at",
        }  # fmt: skip


def put_cards(app: FastAPI, video: Any, cards: list[dict[str, Any]]) -> dict[int, uuid.UUID]:
    """A players run's cards as the job leaves them (no pictures needed here)."""
    from wieplay.core.models import ModelRun
    from wieplay.core.players import PlayerCluster

    out = {}
    with app.state.sessionmaker() as db:
        run = ModelRun(
            id=uuid.uuid4(), kind="players", video_id=uuid.UUID(video["id"]),
            model_name="wieplay-players", model_version="test",
        )  # fmt: skip
        db.add(run)
        db.flush()
        for k, c in enumerate(cards, start=1):
            row = PlayerCluster(
                id=uuid.uuid4(), video_id=run.video_id, model_run_id=run.id, key=k,
                team=c["team"], kind=c.get("kind", "person"),
                tracks=[{"id": k, "t0": 1000 * k, "t1": 1000 * k + 500, "n": c["n"],
                         "side": "near", "number": c.get("number"), "number_conf": 0.0,
                         "crop": [0, k], "h": 100.0, "foot": None}],
                crops=[[0, k]], detections=c["n"], ocr_number=c.get("number"),
                ocr_confidence=c.get("conf"), confidence=c.get("sure", 0.3), source="model",
            )  # fmt: skip
            db.add(row)
            out[k] = row.id
        db.commit()
    return out


def test_a_second_game_s_cards_the_memory_recognises_are_named_in_one_step(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    from wieplay.core.players import PlayerCluster

    game_with_sheet(app, club, store, analyser)  # #7 and #11 start every set of the first game
    second = make_ready(app, club, store, k=1)
    ids = put_cards(
        app,
        second,
        [
            {"team": "us", "n": 400, "number": 7, "conf": 0.8},  # recognised
            {"team": "us", "n": 300, "number": 11, "conf": 0.4},  # read too weakly
            {"team": "us", "n": 200, "number": 2, "conf": 0.9},  # a number no game named
            {"team": "them", "n": 500, "number": 7, "conf": 0.9},  # the opponent: never
            {"team": "us", "n": 900, "kind": "pieces"},
        ],
    )
    coach = club["coach"]
    r = coach.get(f"/api/videos/{second['id']}/players/queue")
    assert r.status_code == 200, r.text
    found = r.json()
    [rec] = found["recognised"]
    assert rec["card"] == str(ids[1]) and rec["number"] == 7 and rec["games"] == 1
    assert rec["confidence"] >= 0.95 and rec["name"]
    assert str(ids[1]) not in {i["card"] for i in found["items"]}
    weak = next(i for i in found["items"] if i["card"] == str(ids[2]))
    assert weak["memory"]["number"] == 11 and weak["memory"]["sure"] is False
    assert weak["on_court"][0] == 11  # offered first
    state = coach.get(f"/api/videos/{second['id']}/players").json()
    by_id = {c["id"]: c for c in state["clusters"]}
    assert by_id[str(ids[3])]["memory"]["sure"] is False  # #2: no game named it
    assert by_id[str(ids[4])]["memory"] is None and by_id[str(ids[5])]["memory"] is None

    r = coach.post(f"/api/videos/{second['id']}/players/accept-recognised", json={})
    assert r.status_code == 200, r.text
    assert r.json()["queue"]["recognised"] == []
    with app.state.sessionmaker() as db:
        named = db.get(PlayerCluster, ids[1])
        assert named is not None and named.number == 7 and named.assigned_by is not None
    undo = coach.post(
        f"/api/videos/{second['id']}/players/restore", json={"cards": r.json()["undo"]}
    )
    assert undo.status_code == 200, undo.text
    with app.state.sessionmaker() as db:
        back = db.get(PlayerCluster, ids[1])
        assert back is not None and back.number is None


def test_the_memory_measured_on_a_game_with_a_sheet(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    from wieplay.core.models import Period

    # The earlier game: no sheet, the coach typed the starting six of each set (Sets bar).
    earlier = make_ready(app, club, store, k=1)
    played, _ = match()
    analysed(analyser, played)
    with app.state.sessionmaker() as db:
        for p in db.scalars(select(Period).where(Period.video_id == uuid.UUID(earlier["id"]))):
            p.start_lineup = LINEUP_US
        db.commit()
    # The game measured: its typed sheet is the truth, hidden from the memory.
    measured = game_with_sheet(app, club, store, analyser)
    with app.state.sessionmaker() as db:
        video = db.get(Video, uuid.UUID(measured["id"]))
        assert video is not None
        found = team_memory.evaluate(db, video)
        db.rollback()
    assert found["memory"]["games"] == 1 and found["memory"]["sets"] == 4
    zone = found["servers"]["setter zone"]
    assert zone["serves"] > 0 and zone["right"] == zone["serves"]  # the same six every set
    assert found["servers"]["memory only"]["serves"] == zone["serves"]
    assert found["picks"]["serves_with_a_pick"] == 0  # no players run
    assert found["recognised"]["cards"] == 0
    with app.state.sessionmaker() as db:
        other = db.get(Video, uuid.UUID(earlier["id"]))
        assert other is not None
        assert "error" in team_memory.evaluate(db, other)  # no sheet: nothing to check against


def test_the_cli_learns_and_measures_the_memory(
    app: FastAPI,
    club: dict[str, TestClient],
    store: FakeStore,
    analyser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.conftest import TEST_DB
    from wieplay import cli
    from wieplay.core.config import get_settings

    video = game_with_sheet(app, club, store, analyser)
    monkeypatch.setenv("WIEPLAY_DATABASE_URL", TEST_DB)
    get_settings.cache_clear()
    try:
        assert cli.main(["team-memory", "--all"]) == 0
        out = capsys.readouterr().out
        assert "1 games named numbers" in out and "4.0 sets" in out
        assert cli.main(["team-memory", "--eval", video["id"]]) == 0
        assert '"servers"' in capsys.readouterr().out
    finally:
        get_settings.cache_clear()

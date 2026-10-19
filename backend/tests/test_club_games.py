"""A video's Wiedisync game after the upload, also another club team's game of the same day:
cup teams are shared, and Wiedisync files the cup game H3 filmed under H1 (core/club_games.py).
The sync keeps such a "borrowed" game and refreshes it (core/sync.py, refresh_borrowed)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.media_helpers import D1, DU23, H3, HERREN1, TODAY, game_id, upload_all
from tests.test_imports import allow_luca
from tests.test_scoring_api import analysed, analyser, match, ready, score_runs, settings
from wieplay.api.dev_bridge import FixtureBridgeClient
from wieplay.api.dev_fixtures import CUP_DAYS_AGO, CUP_GAME_ID
from wieplay.core import batch, club_games, guests
from wieplay.core.models import AuditLog, ModelRun, WdGame, WdTeam
from wieplay.core.sync import BridgeError

__all__ = ["analyser", "ready", "settings"]  # fixtures of the scoring tests, used below

H1 = 9105  # not enabled: the club fixture enables H3, D1, DU23 and Herren 1
CUP_DAY = TODAY - timedelta(days=CUP_DAYS_AGO)
CUP_SETS = [[13, 25], [28, 30], [21, 25]]  # Volley S9 (home) vs KSC Wiedikon H1 (away)


class NoClubGames(FixtureBridgeClient):
    """A Wiedisync from before /club-games (prod until its release): 404."""

    def club_games(
        self, *, on: date | None = None, ids: Sequence[int] | None = None, sport: str | None = None
    ) -> Mapping[str, Any]:
        raise BridgeError("bridge answered 404 for /club-games")


def cup_video(coach: TestClient, store: FakeStore) -> dict[str, Any]:
    """H3's video of the cup game: no game of H3's own that day, so a date and a title."""
    created = coach.post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "match",
            "recorded_on": CUP_DAY.isoformat(),
            "title": "Mobiliarcup: KSCW H3 vs S.9",
            "files": [{"name": "C0001.MP4", "size": 1000, "type": "video/mp4"}],
        },
    )
    assert created.status_code == 201, created.text
    video: dict[str, Any] = upload_all(coach, store, created.json())
    assert video["game_id"] is None and video["game"] is None
    return video


def link(client: TestClient, video: dict[str, Any], game: int | None) -> Any:
    return client.put(f"/api/videos/{video['id']}/game", json={"game_id": game})


def test_a_cup_video_links_to_the_game_filed_under_another_team(
    club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    video = cup_video(coach, store)
    assert video["can_link_game"] is True

    choices = coach.get(f"/api/videos/{video['id']}/game-choices").json()
    assert choices["on"] == CUP_DAY.isoformat() and choices["current"] is None
    assert choices["own"] == [] and choices["club_state"] == "ok"
    [cup] = choices["club"]
    assert (cup["id"], cup["team_id"], cup["team_name"], cup["other_team"]) == (
        CUP_GAME_ID, H1, "H1", True
    )  # fmt: skip
    assert cup["league"].startswith("Mobiliar Volley Cup") and cup["sets"] == CUP_SETS

    linked = link(coach, video, CUP_GAME_ID)
    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert body["game_id"] == CUP_GAME_ID
    assert body["game"]["team_name"] == "H1" and body["game"]["other_team"] is True
    assert (body["game"]["home_score"], body["game"]["away_score"]) == (0, 3)
    # The names follow the game: ours as the game has it, theirs from the game.
    assert body["names"]["us"] == "KSCW H3"
    assert body["names"]["us_full"] == "KSC Wiedikon H1"
    assert (body["names"]["them"], body["names"]["them_from"]) == ("Volley S9", "game")
    assert body["title"] == "Mobiliarcup: KSCW H3 vs S.9"  # the coach's title stays

    with Session(db_engine) as db:
        game = db.get(WdGame, CUP_GAME_ID)
        assert game is not None and game.team_id == H1 and game.sets == CUP_SETS
        team = db.get(WdTeam, H1)
        assert team is not None and team.listed and not team.lineage.enabled
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "video_game")).one()
        assert entry.actor_member_id == 106 and entry.object_id == video["id"]
        assert entry.details == {
            "before": None, "after": CUP_GAME_ID, "game_team": H1, "other_team": True
        }  # fmt: skip
    again = coach.get(f"/api/videos/{video['id']}/game-choices").json()
    assert again["current"] == CUP_GAME_ID

    # No game again: the borrowed game leaves the mirror (no other video links to it).
    unlinked = link(coach, video, None)
    assert unlinked.status_code == 200 and unlinked.json()["game"] is None
    assert unlinked.json()["names"]["them_from"] == "title"
    with Session(db_engine) as db:
        assert db.get(WdGame, CUP_GAME_ID) is None
        assert len(db.scalars(select(AuditLog).where(AuditLog.action == "video_game")).all()) == 2


def test_the_sync_keeps_and_refreshes_a_borrowed_game(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    video = cup_video(club["coach"], store)
    assert link(club["coach"], video, CUP_GAME_ID).status_code == 200
    with Session(db_engine) as db:
        game = db.get(WdGame, CUP_GAME_ID)
        assert game is not None
        game.sets, game.home_score, game.away_score = None, None, None  # not known yet
        db.commit()

    synced = club["admin"].post("/api/admin/sync")
    assert synced.status_code == 200, synced.text
    assert club["admin"].get("/api/admin/sync").json()["counts"]["borrowed"] == 1
    with Session(db_engine) as db:
        game = db.get(WdGame, CUP_GAME_ID)
        assert game is not None and game.team_id == H1  # kept, and the result arrived
        assert (game.sets, game.home_score, game.away_score) == (CUP_SETS, 0, 3)

    # Without the bridge's route (an older Wiedisync) the sync still succeeds and keeps it.
    app.state.bridge_client_factory = NoClubGames
    assert club["admin"].post("/api/admin/sync").status_code == 200
    assert club["admin"].get("/api/admin/sync").json()["counts"]["borrowed"] == 0
    with Session(db_engine) as db:
        assert db.get(WdGame, CUP_GAME_ID) is not None


class MovedCupGame(FixtureBridgeClient):
    """Wiedisync has since filed the cup game under H3 (an enabled team)."""

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]:
        snap = dict(super().snapshot(team_ids))
        cup = next(g for g in self.club_games(ids=[CUP_GAME_ID])["games"])
        moved = {k: v for k, v in cup.items() if k != "team_name"} | {"team": H3}
        snap["games"] = [*snap["games"], moved]
        return snap


def test_a_borrowed_game_moved_to_an_enabled_team_does_not_break_the_sync(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    video = cup_video(club["coach"], store)
    assert link(club["coach"], video, CUP_GAME_ID).status_code == 200
    app.state.bridge_client_factory = MovedCupGame
    synced = club["admin"].post("/api/admin/sync")
    assert synced.status_code == 200, synced.text
    with Session(db_engine) as db:
        game = db.get(WdGame, CUP_GAME_ID)
        assert game is not None and game.team_id == H3
    shown = club["coach"].get(f"/api/videos/{video['id']}").json()["game"]
    assert shown["other_team"] is False and shown["team_name"] == "H3"


def test_a_game_day_offers_the_teams_own_game_and_the_other_teams_of_its_sport(
    club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    created = coach.post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "match",
            "game_id": game_id(H3, 1),
            "files": [{"name": "C0001.MP4", "size": 1000, "type": "video/mp4"}],
        },
    )
    assert created.status_code == 201, created.text
    video = created.json()
    assert video["game"]["other_team"] is False and video["game"]["team_name"] == "H3"
    choices = coach.get(f"/api/videos/{video['id']}/game-choices").json()
    assert choices["current"] == game_id(H3, 1)
    assert [g["id"] for g in choices["own"]] == [game_id(H3, 1)]
    # Volleyball only: Herren 1 (basketball) played that day too.
    assert sorted(g["team_id"] for g in choices["club"]) == [D1, DU23, H1]
    assert all(g["other_team"] for g in choices["club"])

    moved = link(coach, video, game_id(D1, 1))
    assert moved.status_code == 200, moved.text
    assert moved.json()["game"]["team_name"] == "D1"
    # Refused: another day, another sport, unknown.
    assert link(coach, video, game_id(D1, 2)).status_code == 422
    assert link(coach, video, game_id(HERREN1, 1)).status_code == 422
    assert link(coach, video, 999_999).status_code == 422
    back = link(coach, video, game_id(H3, 1))
    assert back.status_code == 200 and back.json()["game"]["other_team"] is False
    assert link(coach, video, 0).status_code == 422  # not an id


def test_who_changes_a_videos_game(club: dict[str, TestClient], as_: Any, store: FakeStore) -> None:
    video = cup_video(club["coach"], store)
    player = as_("player-h3")
    shown = player.get(f"/api/videos/{video['id']}")
    assert shown.status_code == 404  # players see ready videos only
    assert player.get(f"/api/videos/{video['id']}/game-choices").status_code == 403
    assert link(player, video, CUP_GAME_ID).status_code == 403
    sport_admin = as_("vb-sport-admin")
    assert sport_admin.get(f"/api/videos/{video['id']}/game-choices").status_code == 200
    assert link(sport_admin, video, CUP_GAME_ID).status_code == 200
    assert link(club["admin"], video, None).status_code == 200
    assert as_("basketball-player").get(f"/api/videos/{video['id']}/game-choices").status_code == (
        404
    )


def test_without_the_bridge_route_only_the_teams_own_games(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    video = cup_video(coach, store)
    app.state.bridge_client_factory = NoClubGames
    choices = coach.get(f"/api/videos/{video['id']}/game-choices")
    assert choices.status_code == 200
    assert choices.json() | {"on": None} == {
        "on": None, "current": None, "own": [], "club": [], "club_state": "unavailable"
    }  # fmt: skip
    refused = link(coach, video, CUP_GAME_ID)
    assert refused.status_code == 503
    assert coach.get(f"/api/videos/{video['id']}").json()["game_id"] is None
    # A game already mirrored still links, and stays offered.
    app.state.bridge_client_factory = FixtureBridgeClient
    assert link(coach, video, CUP_GAME_ID).status_code == 200
    app.state.bridge_client_factory = NoClubGames
    assert link(coach, video, None).status_code == 200
    with app.state.sessionmaker() as db:
        assert db.get(WdGame, CUP_GAME_ID) is None  # forgotten on unlinking
    shared = cup_video(coach, store)
    app.state.bridge_client_factory = FixtureBridgeClient
    assert link(coach, shared, CUP_GAME_ID).status_code == 200
    app.state.bridge_client_factory = NoClubGames
    second = cup_video(coach, store)
    assert link(coach, second, CUP_GAME_ID).status_code == 200  # from the mirror
    listed = coach.get(f"/api/videos/{second['id']}/game-choices").json()
    assert [g["id"] for g in listed["club"]] == [CUP_GAME_ID]
    # Two videos link to it: unlinking one keeps the game.
    assert link(coach, shared, None).status_code == 200
    with app.state.sessionmaker() as db:
        assert db.get(WdGame, CUP_GAME_ID) is not None


def test_the_upload_page_learns_of_the_other_teams_games(
    club: dict[str, TestClient], as_: Any
) -> None:
    found = club["coach"].get(f"/api/teams/{H3}/game-choices", params={"on": CUP_DAY.isoformat()})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["own"] == [] and [g["id"] for g in body["club"]] == [CUP_GAME_ID]
    assert as_("player-h3").get(
        f"/api/teams/{H3}/game-choices", params={"on": CUP_DAY.isoformat()}
    ).status_code == 403  # fmt: skip


def test_guest_teams_never_see_the_clubs_games(db_engine: Engine) -> None:
    with Session(db_engine) as db:
        team = guests.create_team(
            db, club="VBC Gast", name="H1", sport="volleyball", league=None,
            season=f"{CUP_DAY.year}/{str(CUP_DAY.year + 1)[2:]}", video_limit=None,
            training_ok=False, actor=109,
        )  # fmt: skip
        db.flush()
        found = club_games.choices(db, FixtureBridgeClient(), team, CUP_DAY)
        assert (found.own, found.club, found.club_state) == ([], [], "none")
        video = type("V", (), {"recorded_on": CUP_DAY})()
        with pytest.raises(club_games.LinkRefused) as refused:
            club_games._game_for(db, FixtureBridgeClient(), video, team, CUP_GAME_ID)  # type: ignore[arg-type]
        assert refused.value.status == 422


def test_changing_the_game_scores_again(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    assert score_runs(app, ready)[-1] != "game"
    other = game_id(D1, 0)  # the same day, another team's: its official result counts
    assert link(club["coach"], ready, other).status_code == 200
    assert score_runs(app, ready)[-1] == "game"
    with app.state.sessionmaker() as db:
        last = db.scalars(
            select(ModelRun)
            .where(ModelRun.video_id == uuid.UUID(ready["id"]), ModelRun.kind == "score")
            .order_by(ModelRun.created_at.desc())
        ).first()
        assert last is not None and last.params["official_sets"] is not None
    runs = len(score_runs(app, ready))
    assert link(club["coach"], ready, other).status_code == 200  # unchanged: no new run
    assert len(score_runs(app, ready)) == runs
    assert link(club["coach"], ready, None).status_code == 200
    assert score_runs(app, ready)[-1] == "game"
    with app.state.sessionmaker() as db:
        last = db.scalars(
            select(ModelRun)
            .where(ModelRun.video_id == uuid.UUID(ready["id"]), ModelRun.kind == "score")
            .order_by(ModelRun.created_at.desc())
        ).first()
        assert last is not None and last.params["official_sets"] is None


def test_a_batch_line_without_a_game_of_its_own_names_the_other_teams_games(
    club: dict[str, TestClient], db_engine: Engine
) -> None:
    allow_luca(club["admin"])
    day = CUP_DAY.isoformat()
    text = "\n".join(
        [
            f"{H3}\t{day}\thttps://youtu.be/NXNsZ_UNM6w\tMobiliarcup: KSCW H3 vs S.9",
            f"{H3}\t{day}\thttps://youtu.be/ztugf8Dr4M8",  # no title
            f"{H3}\t{(CUP_DAY - timedelta(days=1)).isoformat()}\thttps://youtu.be/s8eZwM4WP48\tX",
        ]
    )
    with Session(db_engine) as db:
        planned = batch.plan(db, FixtureBridgeClient(), batch.parse(text))
    assert planned[0].problem is None and planned[0].game_id is None
    assert planned[0].note is not None and "H1 · Mobiliar Volley Cup" in planned[0].note
    assert str(CUP_GAME_ID) in planned[0].note and "video page" in planned[0].note
    assert "add a title" in (planned[1].problem or "") and "H1" in (planned[1].problem or "")
    assert planned[1].note is None
    assert planned[2].problem is None and planned[2].note is None

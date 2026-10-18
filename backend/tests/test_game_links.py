"""Links from Wiedisync's game cards: a game's videos, by its Wiedisync id (PLAN §4.4)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeStore
from tests.media_helpers import (
    D1,
    H3,
    MEDIA_CAPS,
    create_match,
    game_id,
    run_pipeline,
    upload_all,
    worker_client,
)

GAME = game_id(H3, 1)


def _ready_video(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    ready: dict[str, Any] = club["coach"].get(f"/api/videos/{video['id']}").json()
    assert ready["status"] == "ready"
    return ready


def test_a_player_finds_the_video_of_their_game(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    video = _ready_video(app, club, store)
    player = as_("player-h3")

    found = player.get(f"/api/games/{GAME}/videos")
    assert found.status_code == 200, found.text
    body = found.json()
    assert [v["id"] for v in body["videos"]] == [video["id"]]
    assert body["videos"][0]["game_id"] == GAME
    assert body["videos"][0]["files"] == []  # a player's view: no internals
    game = body["game"]
    assert game["id"] == GAME and game["date"] == video["recorded_on"]
    assert game["home_name"] and game["away_name"]
    assert game["team"] == {
        "id": H3,
        "name": "H3",
        "season": video["team_season"],
        "sport": "volleyball",
        "can_upload": False,
    }
    coach_view = club["coach"].get(f"/api/games/{GAME}/videos").json()
    assert coach_view["game"]["team"]["can_upload"] is True


def test_a_game_with_two_cameras_lists_both_in_upload_order(
    club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    first = upload_all(coach, store, create_match(coach).json())
    second = upload_all(coach, store, create_match(coach).json())

    body = coach.get(f"/api/games/{GAME}/videos").json()
    assert [v["id"] for v in body["videos"]] == [first["id"], second["id"]]


def test_players_see_ready_videos_only_and_deleted_ones_never(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    coach, player = club["coach"], as_("player-h3")
    video = upload_all(coach, store, create_match(coach).json())

    # Still processing: the coach sees it, the player sees the game without videos.
    assert [v["id"] for v in coach.get(f"/api/games/{GAME}/videos").json()["videos"]] == [
        video["id"]
    ]
    waiting = player.get(f"/api/games/{GAME}/videos").json()
    assert waiting["videos"] == [] and waiting["game"]["id"] == GAME

    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    assert len(player.get(f"/api/games/{GAME}/videos").json()["videos"]) == 1

    assert coach.delete(f"/api/videos/{video['id']}").status_code == 204
    for viewer in (coach, player, club["admin"]):
        assert viewer.get(f"/api/games/{GAME}/videos").json()["videos"] == []


def test_a_game_the_viewer_cannot_see_answers_like_an_unknown_game(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    _ready_video(app, club, store)
    outsider = as_("player-d1")  # plays in D1, not in H3

    hidden = outsider.get(f"/api/games/{GAME}/videos")
    unknown = outsider.get("/api/games/999999/videos")
    assert hidden.status_code == unknown.status_code == 200
    assert hidden.json() == unknown.json() == {"game": None, "videos": []}

    # Her own team's game: known, without videos.
    own = outsider.get(f"/api/games/{game_id(D1, 1)}/videos").json()
    assert own["videos"] == [] and own["game"]["team"]["id"] == D1

    # Admins see every team's games.
    assert len(club["admin"].get(f"/api/games/{GAME}/videos").json()["videos"]) == 1


def test_a_switched_off_team_hides_its_game(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    _ready_video(app, club, store)
    admin = club["admin"]
    assert admin.put(f"/api/admin/teams/{H3}", json={"enabled": False}).status_code == 200
    for viewer in (club["coach"], admin):
        assert viewer.get(f"/api/games/{GAME}/videos").json() == {"game": None, "videos": []}


@pytest.mark.parametrize("bad", ["0", "-1", "abc", str(2**31)])
def test_only_wiedisync_game_ids_are_accepted(club: dict[str, TestClient], bad: str) -> None:
    assert club["coach"].get(f"/api/games/{bad}/videos").status_code == 422


def test_logged_out_is_refused(client: TestClient) -> None:
    assert client.get(f"/api/games/{GAME}/videos").status_code == 401

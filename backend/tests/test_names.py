"""The teams' names instead of us and them (nomal/core/names.py): ours from the club and the
team, the opponent from the Wiedisync game or the video's title, a coach's names per video
(PATCH /api/videos/{id}/setup), the setup's "no scoreboard", the stats CSV and the recap."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.core import names
from nomal.core.models import WdGame
from tests.fakes import FakeStore
from tests.media_helpers import H3
from tests.test_analysis_api import make_ready
from tests.test_videos import friendly

MARKS = names.markers("KSCW", "KSC Wiedikon")


# --- the words ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("team", "out"),
    [
        ("H3", "KSCW H3"),
        ("Herren 1", "KSCW Herren 1"),
        ("KSCW D1", "KSCW D1"),
        ("KSC Wiedikon H3", "KSCW H3"),
        ("  DU23-1 ", "KSCW DU23-1"),
    ],
)
def test_our_name_is_the_club_and_the_team(team: str, out: str) -> None:
    assert names.ours(team, "KSCW", "KSC Wiedikon") == out


@pytest.mark.parametrize(
    ("full", "short"),
    [
        ("VBC Swiss", "VBC Swiss"),  # fits: as it is
        ("Volley Näfels", "Volley Näfels"),
        ("VBC Züri Unterland", "Züri Unterland"),  # the club word goes only when too long
        ("Volleyball Club Glaronia 2", "Glaronia 2"),
        ("VC Kanti Schaffhausen", "Kanti Schaffhau…"),
        ("Volley Smash 05 Laufenburg-Kaisten", "Smash 05 Laufen…"),
        ("Glaronia Volley Club Näfels", "Glaronia Volley…"),
        ("Supercalifragilisticexpialidocious", "Supercalifragil…"),
    ],
)
def test_long_names_are_shortened(full: str, short: str) -> None:
    out = names.shorten(full)
    assert out == short
    assert len(out) <= names.MAX_SHORT


@pytest.mark.parametrize(
    ("title", "opponent"),
    [
        ("Friendly vs Volero", "Volero"),
        ("Friendly vs. VBC Zürich", "VBC Zürich"),
        ("Testspiel gegen Volero Zürich", "Volero Zürich"),
        ("KSCW H3 \N{EN DASH} Smash 05", "Smash 05"),
        ("Cup: KSCW H3 - Volley Näfels (Achtelfinal)", "Volley Näfels"),
        ("Turnier Bülach vs Seen Volley, Spiel 2", "Seen Volley"),
        ("Friendly", None),
        ("Training match", None),
        ("KSCW H3 \N{EN DASH} KSCW H2", None),  # both ours
        ("Testclub A \N{EN DASH} Testclub B", None),  # neither: no telling which
    ],
)
def test_the_opponent_from_a_title(title: str, opponent: str | None) -> None:
    assert names.opponent_in_title(title, MARKS) == opponent


def test_the_opponent_from_a_game() -> None:
    home = WdGame(home_name="KSC Wiedikon H3", away_name="VBC Swiss", our_side="home")
    away = WdGame(home_name="VBC Swiss", away_name="KSC Wiedikon H3", our_side="away")
    unknown = WdGame(home_name="VBC Swiss", away_name="KSC Wiedikon H3", our_side=None)
    assert names.opponent_in_game(home, MARKS) == "VBC Swiss"
    assert names.opponent_in_game(away, MARKS) == "VBC Swiss"
    assert names.opponent_in_game(unknown, MARKS) == "VBC Swiss"
    assert names.opponent_in_game(None, MARKS) is None


def test_a_coach_s_name_is_one_trimmed_line() -> None:
    assert names.clean("  Volero \n Zürich ") == "Volero Zürich"
    assert names.clean("   ") is None and names.clean(None) is None
    assert len(names.clean("x" * 80) or "") == names.MAX_NAME


# --- through the API -----------------------------------------------------------------------------


def test_a_game_names_both_teams(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = make_ready(app, club, store, k=1)  # an away game against Testclub B
    out = club["coach"].get(f"/api/videos/{video['id']}").json()
    assert out["names"] == {
        "us": "KSCW H3",
        "them": "Testclub B",
        "us_full": "KSC Wiedikon H3",
        "them_full": "Testclub B",
        "them_from": "game",
        "us_set": None,
        "them_set": None,
    }
    assert out["no_scoreboard"] is False
    # The team's list carries them too.
    listed = club["coach"].get(f"/api/teams/{H3}/videos").json()
    assert next(v for v in listed if v["id"] == video["id"])["names"]["them"] == "Testclub B"


def test_a_friendly_names_the_opponent_from_its_title(club: dict[str, TestClient]) -> None:
    coach = club["coach"]
    named = friendly(coach, title="Friendly vs Volero").json()
    assert named["names"]["us"] == "KSCW H3"
    assert (named["names"]["them"], named["names"]["them_from"]) == ("Volero", "title")
    unnamed = friendly(coach, title="Friendly").json()
    assert (unnamed["names"]["them"], unnamed["names"]["them_from"]) == (None, None)


def test_a_coach_sets_the_names_for_a_video(club: dict[str, TestClient], as_: Any) -> None:
    coach = club["coach"]
    video = friendly(coach, title="Friendly").json()
    url = f"/api/videos/{video['id']}/setup"
    r = coach.patch(url, json={"name_them": "  Volero Zürich ", "name_us": "H3 Wiedikon"})
    assert r.status_code == 200, r.text
    got = r.json()["names"]
    assert (got["us"], got["them"], got["them_from"]) == ("H3 Wiedikon", "Volero Zürich", "set")
    assert (got["us_set"], got["them_set"]) == ("H3 Wiedikon", "Volero Zürich")
    # Left out: kept; "" or null: back to the derived one.
    kept = coach.patch(url, json={"no_scoreboard": True}).json()
    assert kept["names"]["them"] == "Volero Zürich" and kept["no_scoreboard"] is True
    back = coach.patch(url, json={"name_us": "", "name_them": None}).json()["names"]
    assert (back["us"], back["them"], back["us_set"], back["them_set"]) == (
        "KSCW H3",
        None,
        None,
        None,
    )
    # Players set it up too (they tag since 17.10.2026); another team's people don't see it.
    assert as_("basketball-player").patch(url, json={"name_them": "x"}).status_code == 404
    assert as_("player-h3").patch(url, json={"name_them": "Näfels"}).status_code == 200


def test_a_coach_s_name_wins_over_the_game_and_the_official_one_stays(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = make_ready(app, club, store, k=0)  # at home against Testclub A
    url = f"/api/videos/{video['id']}/setup"
    got = club["coach"].patch(url, json={"name_them": "Club A"}).json()["names"]
    assert (got["them"], got["them_full"], got["them_from"]) == ("Club A", "Testclub A", "set")


def test_no_scoreboard_in_the_video(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    box = {"rect": {"x": 0.1, "y": 0.05, "w": 0.12, "h": 0.06}, "kind": "flip"}
    board = f"/api/videos/{video['id']}/scoreboard"
    assert coach.put(board, json=box).status_code == 200
    # Saying so in the setup forgets the box (like the dialog's "No scoreboard in this video").
    out = coach.patch(f"/api/videos/{video['id']}/setup", json={"no_scoreboard": True}).json()
    assert out["no_scoreboard"] is True
    assert coach.get(board).json()["region"] is None
    # Drawing a box again: there is one after all.
    assert coach.put(board, json=box).status_code == 200
    assert coach.get(f"/api/videos/{video['id']}").json()["no_scoreboard"] is False
    # The dialog's button says it too.
    assert coach.delete(board).status_code == 204
    assert coach.get(f"/api/videos/{video['id']}").json()["no_scoreboard"] is True


def test_the_match_csv_names_the_teams(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    video = make_ready(app, club, store, k=0)
    coach.patch(f"/api/videos/{video['id']}/setup", json={"name_them": "Club A"})
    body = coach.get(f"/api/videos/{video['id']}/stats.csv?lang=de").text
    lines = body.lstrip("﻿").splitlines()
    assert any(",KSCW H3," in line for line in lines)
    assert any(",Club A," in line for line in lines)
    assert not any(",Gegner," in line for line in lines)

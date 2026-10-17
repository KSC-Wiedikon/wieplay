"""The teams' names where the app would say us and them: who served and won a rally, the score
line, the sets, the stats, the recap, the CSV.

Ours: the club's short name and the team ("KSCW H3"). The opponent: from the video's Wiedisync
game (the side that is not ours), else from the video's title for a friendly or a tournament
("Friendly vs Volero" → "Volero"); unknown otherwise (the app then says "Opponent"). Long
names are shortened to fit a column ("VBC Züri Unterland" → "Züri Unterland"); the full name
stays for tooltips. A coach can set both names per video (the video page's setup).

The keyboard and the data stay us and them; only the words change.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from nomal.core.models import Video, WdGame

MAX_SHORT = 16  # characters: a rally list column, a button, the recap's score bug
MAX_NAME = 40  # what a coach may set (video.name_us, video.name_them)
# Club words in front of a team's name that can go when a name is too long ("VBC Züri
# Unterland" → "Züri Unterland"). Longest first.
PREFIXES = (
    "Volleyballclub",
    "Volleyball-Club",
    "Volleyball Club",
    "Volleyball",
    "Volley",
    "VBC",
    "VBG",
    "VC",
)
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"

_PREFIX = re.compile(r"^(?:" + "|".join(re.escape(p) for p in PREFIXES) + r")\b[\s.-]*", re.I)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_VERSUS = re.compile(r"(?:^|\s)(?:vs\.?|v\.|versus|gegen|against|contre|contro)\s+", re.I)
_DASH = re.compile(r"\s+[\N{EN DASH}\N{EM DASH}-]\s+")
_AFTER = re.compile(r"\s*(?:[,|/(\[]|\s[\N{EN DASH}\N{EM DASH}-]\s)")


@dataclass(frozen=True)
class Names:
    """What the app writes for us and them in one video."""

    us: str  # short ("KSCW H3")
    them: str | None  # short; None: not known (the app says "Opponent")
    us_full: str  # the official name (the game's), for tooltips
    them_full: str | None
    # Where the opponent's name came from: a coach set it, the game, the video's title.
    them_from: Literal["set", "game", "title"] | None
    us_set: str | None  # what a coach set (the setup's fields)
    them_set: str | None

    def name(self, side: str, fallback: str) -> str:
        if side == "us":
            return self.us
        return self.them or fallback


def clean(value: str | None) -> str | None:
    """A name as a coach typed it: one line, trimmed, at most MAX_NAME characters; empty is
    none."""
    if value is None:
        return None
    text = " ".join(value.split())[:MAX_NAME].strip()
    return text or None


def ours(team_name: str, club_short: str, club_name: str = "") -> str:
    """ "KSCW H3" from the team "H3"; a team named with the club already keeps its name, with
    the club's long name shortened ("KSC Wiedikon H3" → "KSCW H3")."""
    name = " ".join(team_name.split())
    club = club_short.strip()
    if club_name and name.lower().startswith(club_name.lower()):
        name = f"{club} {name[len(club_name) :].strip()}".strip()
    if not club or name.lower() == club.lower() or name.lower().startswith(club.lower() + " "):
        return name
    return f"{club} {name}".strip()


def is_ours(name: str, markers: Iterable[str]) -> bool:
    """The name is one of the club's teams ("KSC Wiedikon H3", "KSCW D1")."""
    low = f" {name.lower()} "
    return any(m and f" {m.lower()} " in low for m in markers)


def shorten(name: str, limit: int = MAX_SHORT) -> str:
    """A short form that fits a column: the name as it is when it fits; else without a club
    word in front ("VBC", "VC", "Volley"…); else cut, with "…"."""
    text = " ".join(name.split())
    if len(text) <= limit:
        return text
    stripped = _PREFIX.sub("", text).strip()
    if stripped and stripped != text:
        text = stripped
        if len(text) <= limit:
            return text
    cut = text[: limit - 1].rstrip()
    if text[limit - 1] != " " and " " in cut:
        # At a word, when that keeps most of it ("Glaronia Volley…", not "Kanti…").
        word = cut.rsplit(" ", 1)[0]
        if len(word) >= limit * 3 // 5:
            cut = word
    return cut.rstrip(" -,.") + ELLIPSIS


def opponent_in_game(game: WdGame | None, markers: Iterable[str]) -> str | None:
    """The other team of a Wiedisync game: the away team when we play at home, and so on;
    without our side, the name that is not ours."""
    if game is None:
        return None
    home, away = (game.home_name or "").strip(), (game.away_name or "").strip()
    if game.our_side == "home":
        return away or None
    if game.our_side == "away":
        return home or None
    marks = list(markers)
    if home and is_ours(home, marks) and away and not is_ours(away, marks):
        return away
    if away and is_ours(away, marks) and home and not is_ours(home, marks):
        return home
    return None


def _tidy(candidate: str, markers: list[str]) -> str | None:
    text = _AFTER.split(candidate, maxsplit=1)[0]
    text = text.strip(" \t:;,.-\N{EN DASH}\N{EM DASH}")
    if not text or len(text) > 60 or not any(c.isalpha() for c in text) or is_ours(text, markers):
        return None
    return text


def opponent_in_title(title: str, markers: Iterable[str]) -> str | None:
    """The opponent named in a video's title, for a match that is not in Wiedisync:
    "Friendly vs Volero" → "Volero", "Testspiel gegen Volero Zürich" → "Volero Zürich",
    "KSCW H3 - Smash 05" → "Smash 05". None when the title names no other team."""
    marks = list(markers)
    text = _BRACKETS.sub("", " ".join(title.split()))
    versus = list(_VERSUS.finditer(text))
    if versus:
        return _tidy(text[versus[-1].end() :], marks)
    parts = [p for p in _DASH.split(text) if p.strip()]
    if len(parts) < 2:
        return None
    others = [p for p in parts if not is_ours(p, marks)]
    if len(others) != 1:
        return None
    return _tidy(others[0], marks)


def markers(club_short: str, club_name: str) -> list[str]:
    """How the club's own teams show up in game names and titles."""
    return [m for m in (club_short.strip(), club_name.strip()) if m]


def for_video(db: Session, video: Video, team_name: str, club_short: str, club_name: str) -> Names:
    """The names of a video's two teams (module docstring)."""
    marks = markers(club_short, club_name)
    game = db.get(WdGame, video.game_id) if video.game_id is not None else None
    ours_short = ours(team_name, club_short, club_name)
    ours_official = None
    if game is not None:
        ours_official = {"home": game.home_name, "away": game.away_name}.get(game.our_side or "")
    them_game = opponent_in_game(game, marks)
    them_title = None if them_game else opponent_in_title(video.title, marks)
    us_set, them_set = clean(video.name_us), clean(video.name_them)
    them_from: Literal["set", "game", "title"] | None
    if them_set:
        them, them_from = them_set, "set"
    elif them_game:
        them, them_from = shorten(them_game), "game"
    elif them_title:
        them, them_from = shorten(them_title), "title"
    else:
        them, them_from = None, None
    return Names(
        us=us_set or ours_short,
        them=them,
        us_full=(ours_official or "").strip() or us_set or ours_short,
        them_full=them_game or them_set or them_title,
        them_from=them_from,
        us_set=us_set,
        them_set=them_set,
    )

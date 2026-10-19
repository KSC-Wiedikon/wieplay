"""A video's Wiedisync game, set or changed after the upload (the video page's "Game").

The choices are the games of the recording day: the video's team's own, and those of the club's
other teams of the same sport. Cup teams are shared between club teams, and Wiedisync files a
cup game under one of them: the Mobiliar Cup video H3 filmed belongs to a game filed under H1.
Such a "borrowed" game is kept in the mirror like the team's own (wd_game, under its team) and
refreshed by the hourly sync (wieplay/core/sync.py, refresh_borrowed), so its official result
feeds the scoring like any other.

Guest clubs' teams (PLAN §4.5) have no Wiedisync games and never see the club's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wieplay.core.models import ModelRun, Video, WdGame, WdTeam
from wieplay.core.sync import (
    BridgeClient,
    BridgeError,
    ClubGame,
    parse_club_games,
    store_club_games,
)

log = logging.getLogger(__name__)

ClubState = Literal["ok", "unavailable", "none"]


@dataclass(frozen=True)
class Choice:
    """A game a video can be linked to."""

    id: int
    team_id: int
    team_name: str  # the team Wiedisync files it under ("H1")
    other_team: bool  # not the video's team: a shared cup team's game
    date: date | None
    time: time | None
    home_name: str | None
    away_name: str | None
    our_side: str | None
    league: str | None
    hall_name: str | None
    home_score: int | None
    away_score: int | None
    sets: list[list[int]] | None


@dataclass(frozen=True)
class Choices:
    own: list[Choice]
    club: list[Choice]
    # "ok": the other teams' games are listed; "unavailable": Wiedisync could not list them (an
    # older bridge without /club-games, unreachable); "none": not offered (a guest club's team).
    club_state: ClubState


class LinkRefused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def choice_of(row: WdGame, team_name: str, other_team: bool) -> Choice:
    return Choice(
        id=row.id,
        team_id=row.team_id,
        team_name=team_name,
        other_team=other_team,
        date=row.date,
        time=row.time,
        home_name=row.home_name,
        away_name=row.away_name,
        our_side=row.our_side,
        league=row.league,
        hall_name=row.hall_name,
        home_score=row.home_score,
        away_score=row.away_score,
        sets=row.sets,
    )


def _club_choice(game: ClubGame, other_team: bool) -> Choice:
    values: dict[str, Any] = dict(game.values)
    return Choice(
        id=game.id, team_id=game.team.id, team_name=game.team.name, other_team=other_team, **values
    )


def _order(c: Choice) -> tuple[time, str, int]:
    return (c.time or time.max, c.team_name, c.id)


def choices(db: Session, bridge: BridgeClient | None, team: WdTeam, on: date) -> Choices:
    """The games of `on` a video of `team` can be linked to: the team's own (the mirror's, and
    the bridge's when the mirror has not caught up yet) and, for a KSCW team, every other club
    team's of the same sport."""
    own = {
        row.id: choice_of(row, team.name, False)
        for row in db.scalars(select(WdGame).where(WdGame.team_id == team.id, WdGame.date == on))
    }
    if team.guest:
        return Choices(sorted(own.values(), key=_order), [], "none")
    club: list[Choice] = []
    state: ClubState = "ok"
    try:
        if bridge is None:
            raise BridgeError("the bridge is not configured")
        found = parse_club_games(bridge.club_games(on=on, sport=team.sport))
    except BridgeError as exc:
        log.info("club games of %s not listed: %s", on, exc)
        found, state = [], "unavailable"
    for game in found:
        if game.date != on or game.team.sport != team.sport:
            continue
        if game.team.id == team.id:
            own.setdefault(game.id, _club_choice(game, False))
        else:
            club.append(_club_choice(game, True))
    return Choices(sorted(own.values(), key=_order), sorted(club, key=_order), state)


def _game_for(
    db: Session, bridge: BridgeClient | None, video: Video, team: WdTeam, game_id: int
) -> WdGame:
    """The game to link, checked: of the recording day, of the team's sport, the team's own or
    (for a KSCW team) another club team's. Another team's game is fetched fresh and kept in
    the mirror; when Wiedisync cannot answer, one already mirrored still counts."""
    row = db.get(WdGame, game_id)
    if row is not None and row.team_id == team.id:
        if row.date != video.recorded_on:
            raise LinkRefused(422, "That game was not played on the day of this video")
        return row
    if team.guest:
        raise LinkRefused(422, "Only this team's own games can be linked")
    try:
        if bridge is None:
            raise BridgeError("the bridge is not configured")
        found = [g for g in parse_club_games(bridge.club_games(ids=[game_id])) if g.id == game_id]
    except BridgeError as exc:
        log.info("club game %s not fetched: %s", game_id, exc)
        owner = db.get(WdTeam, row.team_id) if row is not None else None
        if (
            row is not None
            and owner is not None
            and not owner.guest
            and owner.sport == team.sport
            and row.date == video.recorded_on
        ):
            return row
        raise LinkRefused(
            503, "Wiedisync cannot list the other teams' games right now; try again later"
        ) from exc
    if not found:
        raise LinkRefused(422, "No KSCW game with this id")
    game = found[0]
    if game.team.sport != team.sport:
        raise LinkRefused(422, "That game is of another sport")
    if game.date != video.recorded_on:
        raise LinkRefused(422, "That game was not played on the day of this video")
    # The team's own game the mirror has not caught up with yet is kept the same way.
    stored = store_club_games(db, [game], datetime.now(UTC))
    if not stored:
        raise LinkRefused(422, "No KSCW game with this id")
    return stored[0]


def _forget(db: Session, game_id: int | None) -> None:
    """A borrowed game no video links to any more leaves the mirror. A game of an enabled
    team stays (the sync keeps those), and so does one another video still links to."""
    if game_id is None:
        return
    row = db.get(WdGame, game_id)
    if row is None:
        return
    team = db.get(WdTeam, row.team_id)
    if team is None or team.guest or team.lineage.enabled:
        return
    linked = db.scalar(
        select(func.count())
        .select_from(Video)
        .where(Video.game_id == game_id, Video.status != "deleted")
    )
    if not linked:
        db.delete(row)
        db.flush()


def _was_scored(db: Session, video: Video) -> bool:
    return (
        db.scalar(
            select(ModelRun.id)
            .where(ModelRun.video_id == video.id, ModelRun.kind == "score")
            .limit(1)
        )
        is not None
    )


def set_game(
    db: Session, bridge: BridgeClient | None, video: Video, team: WdTeam, game_id: int | None
) -> tuple[bool, int | None]:
    """Link the video to `game_id` (None: no game). Returns (changed, the game before). A ready
    video gets its new game's scoreboard log, and is scored again when it was scored before
    (the official result is what the scoring stands on); one never scored is left to its
    analysis. Does not commit."""
    from wieplay.core import scorelog, scoring

    before = video.game_id
    row = _game_for(db, bridge, video, team, game_id) if game_id is not None else None
    after = row.id if row is not None else None
    if after == before:
        return False, before
    video.game_id = after
    db.flush()
    _forget(db, before)
    if video.status != "ready":
        return True, before
    if bridge is not None and scorelog.needs_log(db, video):
        try:
            with db.begin_nested():
                scorelog.fetch(db, video, bridge, rescore=False)
        except Exception:
            log.exception("score log of video %s not fetched", video.id)
    if _was_scored(db, video):
        scoring.score_video(db, video, reason="game")
    return True, before

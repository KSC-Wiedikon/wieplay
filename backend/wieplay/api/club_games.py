"""A video's Wiedisync game after the upload (wieplay/core/club_games.py).

GET  /api/videos/{id}/game-choices   the games of the recording day: the team's own and the
                                     club's other teams' (coaches, sport admins, admins)
PUT  /api/videos/{id}/game           link one, or none; audited, scored again
GET  /api/teams/{id}/game-choices    the same for a day, for the upload page's hint (uploaders)
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from wieplay.api.access import visible_team, visible_video
from wieplay.api.deps import BridgeDep, DbDep, SettingsDep, ViewerDep, audit
from wieplay.api.videos import MAX_INT4, VideoGameOut, VideoOut, _video_out
from wieplay.core import club_games
from wieplay.core.models import WdGame, WdTeam
from wieplay.core.permissions import can_link_game, can_upload

router = APIRouter(prefix="/api", tags=["videos"])


class GameChoicesOut(BaseModel):
    on: date
    # The video's game now (the video page); None on the upload page.
    current: int | None = None
    own: list[VideoGameOut]
    club: list[VideoGameOut]
    # "ok": the other KSCW teams' games are listed; "unavailable": Wiedisync could not list them
    # (an older bridge, unreachable), so only the team's own are offered; "none": not offered
    # (a guest club's team).
    club_state: Literal["ok", "unavailable", "none"]


class VideoGameIn(BaseModel):
    """The game to link; null: none (a friendly, a tournament, a match not in Wiedisync)."""

    game_id: Annotated[int, Field(ge=1, le=MAX_INT4)] | None


def _out(on: date, found: club_games.Choices, current: int | None = None) -> GameChoicesOut:
    return GameChoicesOut(
        on=on,
        current=current,
        own=[VideoGameOut.of(c) for c in found.own],
        club=[VideoGameOut.of(c) for c in found.club],
        club_state=found.club_state,
    )


@router.get("/videos/{video_id}/game-choices", response_model=GameChoicesOut)
def video_game_choices(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, bridge: BridgeDep
) -> GameChoicesOut:
    video, access = visible_video(db, viewer, video_id)
    if not can_link_game(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins change the game")
    found = club_games.choices(db, bridge, access.team, video.recorded_on)
    out = _out(video.recorded_on, found, video.game_id)
    # The game linked now is always offered, even when the bridge could not list it again.
    listed = {c.id for c in found.own} | {c.id for c in found.club}
    game = db.get(WdGame, video.game_id) if video.game_id is not None else None
    if game is not None and game.id not in listed:
        other = game.team_id != access.team.id
        owner = db.get(WdTeam, game.team_id) if other else access.team
        choice = VideoGameOut.of(club_games.choice_of(game, owner.name if owner else "", other))
        (out.club if other else out.own).append(choice)
    return out


@router.put("/videos/{video_id}/game", response_model=VideoOut)
def set_video_game(
    video_id: uuid.UUID,
    body: VideoGameIn,
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
    settings: SettingsDep,
) -> VideoOut:
    """Link the video to a game of its recording day, the team's own or another KSCW team's
    (a shared cup team's), or to none. The names on the title card and the recap follow the
    game unless a coach set them (the setup's names)."""
    video, access = visible_video(db, viewer, video_id)
    if not can_link_game(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins change the game")
    try:
        changed, before = club_games.set_game(db, bridge, video, access.team, body.game_id)
    except club_games.LinkRefused as exc:
        db.rollback()
        raise HTTPException(exc.status, exc.detail) from exc
    if changed:
        game = db.get(WdGame, video.game_id) if video.game_id is not None else None
        audit(
            db,
            viewer.member_id,
            "video_game",
            "video",
            str(video.id),
            before=before,
            after=video.game_id,
            game_team=game.team_id if game is not None else None,
            other_team=game is not None and game.team_id != video.team_id,
        )
    db.commit()
    return _video_out(db, video, access, viewer, settings)


@router.get("/teams/{team_id}/game-choices", response_model=GameChoicesOut)
def team_game_choices(
    team_id: int,
    on: Annotated[date, Query()],
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
) -> GameChoicesOut:
    """The games of a day for a new video (the upload page): with none of its own, a video of
    a shared cup team's game is linked on the video page after the upload."""
    access = visible_team(db, viewer, team_id)
    if not can_upload(viewer, access.ref):
        raise HTTPException(403, "Only the team's coaches and sport admins upload videos")
    return _out(on, club_games.choices(db, bridge, access.team, on))

"""Statistics (PLAN §9.7): a match's and a season's box score, as JSON and as CSV.

GET /api/videos/{id}/stats        the match: per player, team, opponent; per set too; per
                                  rotation and the setter analysis
GET /api/videos/{id}/stats.csv    the same as a spreadsheet (?lang=de for German headers;
                                  ?part=rotations or ?part=setter for those tables)
GET /api/teams/{id}/stats         the season of that team row: all its ready matches
GET /api/teams/{id}/stats.csv

Whoever may watch the video, or see the team row, may read its numbers. The numbers are
computed on read from the tags (nomal/core/stats.py); each one carries what is needed to
open its plays: the row's `filter`, the column's `codes` and `grades`, and the set.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nomal.api.access import visible_team
from nomal.api.analysis import ActionGateOut, gate_out
from nomal.api.deps import DbDep, SettingsDep, ViewerDep
from nomal.api.tagging import _readable
from nomal.core import guests, names, stats, tagging
from nomal.core.models import (
    Event,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdGame,
    WdMember,
    WdTeam,
)
from nomal.core.permissions import Viewer
from nomal.core.taxonomy import load

router = APIRouter(prefix="/api", tags=["stats"])

Lang = Literal["en", "de"]
Part = Literal["box", "rotations", "setter"]
Number = int | float | None


class ColumnOut(BaseModel):
    key: str
    group: str | None
    format: str
    # The plays behind a number: tags of these codes with one of these grades (null: any
    # grade; a null inside the list: an ungraded tag).
    codes: list[str]
    grades: list[str | None] | None


class RowOut(BaseModel):
    kind: str
    side: str
    number: int | None
    member_id: int | None
    name: str | None
    # Which tags are this row's; a key that is present must match (null: must be empty).
    filter: dict[str, Any]
    values: dict[str, Number]
    matches: int | None = None  # season: matches with a tag of this row


class TeamLineOut(BaseModel):
    rallies: int
    won: int
    lost: int
    side_out_won: int
    side_out_of: int
    side_out_pct: float | None
    break_point_won: int
    break_point_of: int
    break_point_pct: float | None


class RotationRallyOut(BaseModel):
    id: uuid.UUID
    # The server and winner the rotation walk used (a winner nobody entered is the next
    # rally's server): what the web filters with the team_filters to play a number's rallies.
    serving: str
    won_by: str


class RotationOut(TeamLineOut):
    rotation: int  # our setter's zone, 1-6
    # Our tags in its rallies: the numbers of `rotation_columns` (serve, reception, attack).
    values: dict[str, Number] = Field(default_factory=dict)
    # Its rallies (match and sets; the season: none, its numbers are not played).
    plays: list[RotationRallyOut] = Field(default_factory=list)
    # Our six in zones I-VI in this rotation, from the sets' starting lineups (the most common
    # one when the sets differ); null without a lineup.
    lineup: list[int | None] | None = None
    lineup_varies: bool = False


class SetterLineOut(BaseModel):
    key: str
    # The plays behind the line: the sets whose values match every key present (see
    # SetPlayOut; "attacked": followed by our attack).
    filter: dict[str, Any]
    number: int | None = None
    member_id: int | None = None
    name: str | None = None
    sets: int
    attacks: int
    share: float | None  # of the attacks after a set in this scope
    zones: dict[str, int]  # attacks per zone: left, middle, right, back, unknown
    kills: int
    errors: int
    blocked: int
    kill_pct: float | None
    hit_pct: float | None


class SetPlayOut(BaseModel):
    """One of our sets: what the setter numbers of a match are made of."""

    set_id: uuid.UUID  # the set tag
    attack_id: uuid.UUID | None
    rally_id: uuid.UUID | None
    set_no: int | None  # the set (period) of the match it was played in
    t_ms: int
    attacker: int | None
    attacker_member: int | None
    setter: int | None
    setter_member: int | None
    zone: str | None
    rotation: int | None
    quality: str
    outcome: str | None
    attacked: bool


class SetterOut(BaseModel):
    sets: int  # our set tags
    attacks: int  # of them followed by our attack
    by_attacker: list[SetterLineOut]
    by_zone: list[SetterLineOut]
    by_rotation: list[SetterLineOut]
    by_reception: list[SetterLineOut]
    by_setter: list[SetterLineOut]
    # The match only (each set and the season: null): one per set tag, in time order.
    plays: list[SetPlayOut] | None = None


class BoxOut(BaseModel):
    players: list[RowOut]
    team: RowOut
    opponent: RowOut
    summary: TeamLineOut
    # Per rotation (sets with a starting rotation only); null when no set has one.
    rotations: list[RotationOut] | None = None
    # Our sets and the attacks after them; null for older servers.
    setter: SetterOut | None = None


class SetOut(BoxOut):
    no: int
    score_us: int | None
    score_them: int | None
    # Our setter's zone and our six in zones I-VI at the set's first serve.
    start_rotation: int | None = None
    start_lineup: list[int | None] | None = None


class VideoStatsOut(BaseModel):
    video_id: uuid.UUID
    team_id: int
    title: str
    recorded_on: date
    tags: int
    rallies: int
    columns: list[ColumnOut]
    # The rallies behind each team number (see core/stats.py TEAM_FILTERS).
    team_filters: dict[str, dict[str, str]]
    match: BoxOut
    sets: list[SetOut]
    # The numbers of our tags shown per rotation (`RotationOut.values`).
    rotation_columns: list[ColumnOut] = Field(default_factory=list)
    # Suggested actions nobody has accepted or rejected yet: not counted above.
    suggested: int = 0
    # Whether Nomal suggests actions yet (PLAN §10.4a); null for older servers.
    actions_gate: ActionGateOut | None = None


class MatchOut(BaseModel):
    video_id: uuid.UUID
    title: str
    recorded_on: date
    result_us: int | None
    result_them: int | None
    tagged: bool
    summary: TeamLineOut


class TeamStatsOut(BaseModel):
    team_id: int
    team_name: str
    season: str
    lineage_id: uuid.UUID
    videos: int  # ready matches
    tagged: int  # of those, with tags
    columns: list[ColumnOut]
    season_box: BoxOut
    matches: list[MatchOut]
    rotation_columns: list[ColumnOut] = Field(default_factory=list)
    # Suggested actions nobody has accepted or rejected yet, over the matches: not counted.
    suggested: int = 0
    actions_gate: ActionGateOut | None = None


# --- loading --------------------------------------------------------------------------------


def _check_sport(sport: str) -> None:
    if sport != "volleyball":
        raise HTTPException(409, "Stats are for volleyball so far")


def _zone(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tag(e: Event) -> stats.TagIn:
    side: stats.Side = "them" if e.side == "them" else "us"
    attributes = e.attributes or {}
    grade = attributes.get("grade")
    return stats.TagIn(
        code=e.code,
        side=side,
        number=e.number,
        member_id=e.member_id if side == "us" else None,
        grade=grade if isinstance(grade, str) else None,
        t_ms=e.t_ms,
        segment_id=e.segment_id,
        id=e.id,
        zone_from=_zone(attributes.get("zone_from")),
        zone_to=_zone(attributes.get("zone_to")),
    )


def _side(value: str | None) -> stats.Side | None:
    if value == "us":
        return "us"
    return "them" if value == "them" else None


def _rally(s: Segment) -> stats.RallyIn:
    return stats.RallyIn(s.id, s.start_ms, s.end_ms, s.period_id, _side(s.serving), _side(s.won_by))


def _lineup(value: Any) -> tuple[int | None, ...] | None:
    if not isinstance(value, list) or len(value) != 6:
        return None
    return tuple(_zone(n) for n in value)


def _set(p: Period) -> stats.SetIn:
    return stats.SetIn(
        p.id, p.no, p.start_ms, p.end_ms, p.final_us, p.final_them, p.start_rotation,
        _lineup(p.start_lineup),
    )  # fmt: skip


def _rotations(
    lines: stats.Rotations | None, cols: list[stats.Column], plays: bool = True
) -> list[RotationOut] | None:
    if lines is None:
        return None
    out = []
    for r in stats.ROTATIONS:
        line = lines[r]
        lineup = line.lineup
        out.append(
            RotationOut(
                rotation=r,
                **_line(line.team).model_dump(),
                values=stats.values(cols, line.counts),
                plays=[
                    RotationRallyOut(id=w.rally.id, serving=w.serving, won_by=w.won_by)
                    for w in line.rallies
                ]
                if plays
                else [],
                lineup=list(lineup) if lineup is not None else None,
                lineup_varies=len(line.lineups) > 1,
            )
        )
    return out


def _setter_line(line: stats.SetterLine, attacks: int) -> SetterLineOut:
    return SetterLineOut(
        key=line.key,
        filter=line.filter,
        number=line.number,
        member_id=line.member_id,
        name=line.name,
        sets=line.sets,
        attacks=line.attacks,
        share=line.attacks / attacks if attacks else None,
        zones=dict(line.zones),
        kills=line.kills,
        errors=line.errors,
        blocked=line.blocked,
        kill_pct=line.kill_pct,
        hit_pct=line.hit_pct,
    )


def _setter(
    setter: stats.SetterStats | None, set_no: dict[uuid.UUID, int] | None = None
) -> SetterOut | None:
    """The setter analysis; with `set_no` (the match) also its plays."""
    if setter is None:
        return None
    lines = {
        group: [_setter_line(line, setter.attacks) for line in getattr(setter, group)]
        for group in stats.SETTER_GROUPS
    }
    plays = None
    if set_no is not None:
        plays = [
            SetPlayOut(
                set_id=p.set.id,
                attack_id=p.attack.id if p.attack else None,
                rally_id=p.rally_id,
                set_no=set_no.get(p.set_id) if p.set_id else None,
                t_ms=p.set.t_ms,
                quality=p.quality,
                **{k: v for k, v in p.filter_values.items() if k != "quality"},
            )
            for p in setter.plays
            if p.set.id is not None
        ]
    return SetterOut(sets=setter.sets, attacks=setter.attacks, plays=plays, **lines)


def _events(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.TagIn]]:
    out: dict[uuid.UUID, list[stats.TagIn]] = defaultdict(list)
    rows = db.scalars(
        select(Event)
        .where(
            Event.video_id.in_(video_ids),
            Event.deleted_at.is_(None),
            tagging.event_stands(),  # facts only: a suggested action counts once accepted
        )
        .order_by(Event.t_ms, Event.id)
    )
    for e in rows:
        out[e.video_id].append(_tag(e))
    return out


def _suggested(db: Session, video_ids: list[uuid.UUID]) -> int:
    """Suggested actions nobody has answered yet: not in the numbers (PLAN §10.4a)."""
    if not video_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.video_id.in_(video_ids),
                Event.source == "model",
                Event.review == "unreviewed",
                Event.deleted_at.is_(None),
            )
        )
        or 0
    )


def _rallies(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.RallyIn]]:
    out: dict[uuid.UUID, list[stats.RallyIn]] = defaultdict(list)
    rows = db.scalars(
        select(Segment)
        .where(
            Segment.video_id.in_(video_ids),
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
            tagging.stands(),
        )
        .order_by(Segment.start_ms)
    )
    for s in rows:
        out[s.video_id].append(_rally(s))
    return out


def _sets(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.SetIn]]:
    out: dict[uuid.UUID, list[stats.SetIn]] = defaultdict(list)
    rows = db.scalars(
        select(Period).where(
            Period.video_id.in_(video_ids),
            Period.deleted_at.is_(None),
            # A set the scoring proposed counts once a coach accepted it.
            ~((Period.source == "model") & (Period.review == "unreviewed")),
        )
    )
    for p in rows:
        out[p.video_id].append(_set(p))
    return out


def _names(db: Session, member_ids: set[int]) -> dict[int, str]:
    if not member_ids:
        return {}
    rows = db.execute(select(WdMember.id, WdMember.display_name).where(WdMember.id.in_(member_ids)))
    return {mid: name for mid, name in rows}


def _roster(db: Session, video: Video) -> dict[int, tuple[int | None, str | None]]:
    """Our shirt numbers in the video's roster snapshot: number -> (member, name)."""
    rows = list(
        db.scalars(
            select(VideoRoster).where(VideoRoster.video_id == video.id, VideoRoster.side == "us")
        )
    )
    names = _names(db, {r.member_id for r in rows if r.member_id})
    return {r.number: (r.member_id, names.get(r.member_id) if r.member_id else None) for r in rows}


def _result(game: WdGame | None) -> tuple[int, int] | None:
    if game is None or game.home_score is None or game.away_score is None:
        return None
    if game.our_side == "away":
        return game.away_score, game.home_score
    return game.home_score, game.away_score


# --- shaping --------------------------------------------------------------------------------


def _columns(cols: list[stats.Column]) -> list[ColumnOut]:
    return [
        ColumnOut(
            key=c.key,
            group=c.group,
            format=c.format,
            codes=list(c.codes),
            grades=None if c.grades is None else list(c.grades),
        )
        for c in cols
    ]


def _row(row: stats.Row, cols: list[stats.Column], season: bool) -> RowOut:
    return RowOut(
        kind=row.kind,
        side=row.side,
        number=row.number,
        member_id=row.member_id,
        name=row.name,
        filter=row.filter,
        values=stats.values(cols, row.counts),
        matches=len(row.matches) if season else None,
    )


def _line(line: stats.TeamLine) -> TeamLineOut:
    return TeamLineOut(
        **vars(line), side_out_pct=line.side_out_pct, break_point_pct=line.break_point_pct
    )


def _box(box: stats.BoxScore, team: stats.TeamLine, cols: list[stats.Column]) -> BoxOut:
    return BoxOut(
        players=[_row(r, cols, False) for r in box.players],
        team=_row(box.team, cols, False),
        opponent=_row(box.opponent, cols, False),
        summary=_line(team),
    )


def _season_box(box: stats.BoxScore, team: stats.TeamLine, cols: list[stats.Column]) -> BoxOut:
    return BoxOut(
        players=[_row(r, cols, True) for r in box.players],
        team=_row(box.team, cols, True),
        opponent=_row(box.opponent, cols, True),
        summary=_line(team),
    )


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:60] or "match"


def _csv(body: str, filename: str) -> Response:
    # A byte order mark, so spreadsheet programs read the names as UTF-8.
    return Response(
        content=("﻿" + body).encode(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- a match --------------------------------------------------------------------------------


def _video_stats(
    db: Session, viewer: Viewer, video_id: uuid.UUID
) -> tuple[Video, stats.VideoStats, list[stats.Column]]:
    video, _ = _readable(db, viewer, video_id)
    _check_sport(video.sport)
    tags = _events(db, [video.id])[video.id]
    rallies = _rallies(db, [video.id])[video.id]
    sets = _sets(db, [video.id])[video.id]
    tax = load("volleyball")
    result = stats.video_stats(tags, rallies, sets, _roster(db, video), tax)
    return video, result, stats.columns(tax)


@router.get("/videos/{video_id}/stats", response_model=VideoStatsOut)
def video_stats(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> VideoStatsOut:
    video, result, cols = _video_stats(db, viewer, video_id)
    rot_cols = stats.rotation_columns(load("volleyball"))
    set_no = {s.set.id: s.set.no for s in result.sets}
    return VideoStatsOut(
        video_id=video.id,
        team_id=video.team_id,
        title=video.title,
        recorded_on=video.recorded_on,
        tags=result.tags,
        rallies=result.rallies,
        columns=_columns(cols),
        team_filters=stats.TEAM_FILTERS,
        match=_box(result.box, result.team, cols).model_copy(
            update={
                "rotations": _rotations(result.rotations, rot_cols),
                "setter": _setter(result.setter, set_no),
            }
        ),
        sets=[
            SetOut(
                no=s.set.no,
                score_us=s.set.final_us,
                score_them=s.set.final_them,
                **_box(s.box, s.team, cols).model_dump(exclude={"rotations", "setter"}),
                rotations=_rotations(s.rotations, rot_cols),
                setter=_setter(s.setter),
                start_rotation=s.set.start_rotation,
                start_lineup=list(s.set.lineup) if s.set.lineup is not None else None,
            )
            for s in result.sets
        ],
        rotation_columns=_columns(rot_cols),
        suggested=_suggested(db, [video.id]),
        actions_gate=gate_out(db, settings),
    )


@router.get("/videos/{video_id}/stats.csv")
def video_stats_csv(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
    lang: Lang = "en",
    part: Part = "box",
) -> Response:
    video, result, cols = _video_stats(db, viewer, video_id)
    words = stats.HEADERS[lang]
    match, sets = words["all"], [(str(s.set.no), s) for s in result.sets]
    if part == "rotations":
        rot_cols = stats.rotation_columns(load("volleyball"))
        rotations = [(match, result.rotations), *((no, s.rotations) for no, s in sets)]
        body = stats.rotations_csv(rot_cols, rotations, lang)
    elif part == "setter":
        body = stats.setter_csv([(match, result.setter), *((no, s.setter) for no, s in sets)], lang)
    else:
        scopes = [(match, result.box, result.team), *((no, s.box, s.team) for no, s in sets)]
        team = db.get(WdTeam, video.team_id)
        assert team is not None
        found = names.for_video(
            db,
            video,
            team.name,
            *guests.club_names(team, settings.club_short_name, settings.club_name),
        )
        # The team's and the opponent's rows carry their names ("KSCW H3", "Volero").
        labels = {"team": found.us, "opponent": found.name("them", words["opponent"])}
        body = stats.to_csv(cols, scopes, lang, labels=labels)
    name = f"nomal-{_PART_NAME[part]}-{video.recorded_on.isoformat()}-{_slug(video.title)}.csv"
    return _csv(body, name)


_PART_NAME: dict[str, str] = {"box": "stats", "rotations": "rotations", "setter": "setter"}


# --- a season -------------------------------------------------------------------------------


def _season(
    db: Session, viewer: Viewer, team_id: int
) -> tuple[WdTeam, stats.SeasonStats, list[stats.Column], list[uuid.UUID]]:
    access = visible_team(db, viewer, team_id)
    team = access.team
    _check_sport(team.sport)
    videos = list(
        db.scalars(
            select(Video).where(
                Video.team_id == team.id, Video.status == "ready", Video.sport == "volleyball"
            )
        )
    )
    ids = [v.id for v in videos]
    tags = _events(db, ids) if ids else {}
    rallies = _rallies(db, ids) if ids else {}
    sets = _sets(db, ids) if ids else {}
    game_ids = [v.game_id for v in videos if v.game_id is not None]
    games = (
        {g.id: g for g in db.scalars(select(WdGame).where(WdGame.id.in_(game_ids)))}
        if game_ids
        else {}
    )
    matches = [
        stats.MatchIn(
            video_id=v.id,
            title=v.title,
            recorded_on=v.recorded_on,
            tags=tags.get(v.id, []),
            rallies=rallies.get(v.id, []),
            result=_result(games.get(v.game_id)) if v.game_id is not None else None,
            sets=sets.get(v.id, []),
        )
        for v in videos
    ]
    members = {t.member_id for m in matches for t in m.tags if t.member_id is not None}
    tax = load("volleyball")
    result = stats.season_stats(matches, _names(db, members), tax)
    return team, result, stats.columns(tax), ids


@router.get("/teams/{team_id}/stats", response_model=TeamStatsOut)
def team_stats(team_id: int, viewer: ViewerDep, db: DbDep, settings: SettingsDep) -> TeamStatsOut:
    team, result, cols, ids = _season(db, viewer, team_id)
    rot_cols = stats.rotation_columns(load("volleyball"))
    return TeamStatsOut(
        team_id=team.id,
        team_name=team.name,
        season=team.season,
        lineage_id=team.lineage_id,
        videos=len(ids),
        tagged=result.tagged,
        columns=_columns(cols),
        season_box=_season_box(result.box, result.team, cols).model_copy(
            update={
                "rotations": _rotations(result.rotations, rot_cols, plays=False),
                "setter": _setter(result.setter),
            }
        ),
        rotation_columns=_columns(rot_cols),
        matches=[
            MatchOut(
                video_id=m.match.video_id,
                title=m.match.title,
                recorded_on=m.match.recorded_on,
                result_us=m.match.result[0] if m.match.result else None,
                result_them=m.match.result[1] if m.match.result else None,
                tagged=bool(m.match.tags),
                summary=_line(m.team),
            )
            for m in result.matches
        ],
        suggested=_suggested(db, ids),
        actions_gate=gate_out(db, settings),
    )


@router.get("/teams/{team_id}/stats.csv")
def team_stats_csv(
    team_id: int, viewer: ViewerDep, db: DbDep, lang: Lang = "en", part: Part = "box"
) -> Response:
    team, result, cols, _ = _season(db, viewer, team_id)
    words = stats.HEADERS[lang]
    scope = words["season"]
    if part == "rotations":
        rot_cols = stats.rotation_columns(load("volleyball"))
        body = stats.rotations_csv(rot_cols, [(scope, result.rotations)], lang)
    elif part == "setter":
        body = stats.setter_csv([(scope, result.setter)], lang)
    else:
        body = stats.to_csv(cols, [(scope, result.box, result.team)], lang, True)
    season = team.season.replace("/", "-")
    return _csv(body, f"nomal-{_PART_NAME[part]}-{_slug(team.name)}-{season}.csv")

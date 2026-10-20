"""Statistics (PLAN §9.7): a match's and a season's box score, as JSON and as CSV.

GET /api/videos/{id}/stats        the match: per player, team, opponent; per set too; per
                                  rotation and the setter analysis
GET /api/videos/{id}/stats/zones  the zone maps of serves and attacks, per row and set
GET /api/videos/{id}/stats.csv    the same as a spreadsheet (?lang=de for German headers;
                                  ?part=rotations, ?part=setter or ?part=zones for those tables)
GET /api/teams/{id}/stats         the season of that team row: all its ready matches
GET /api/teams/{id}/stats/zones
GET /api/teams/{id}/stats.csv

Each takes `?search=` (a TagSearch as JSON, wieplay/core/search.py): only what it keeps counts.

Whoever may watch the video, or see the team row, may read its numbers. The numbers are
computed on read from the tags (wieplay/core/stats.py); each one carries what is needed to
open its plays: the row's `filter`, the column's `codes` and `grades`, and the set.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.api.access import visible_team
from wieplay.api.analysis import ActionGateOut, gate_out
from wieplay.api.deps import DbDep, SettingsDep, ViewerDep
from wieplay.api.tagging import _readable
from wieplay.core import guests, lineups, names, stats, stats_input, tagging
from wieplay.core.config import Settings
from wieplay.core.models import (
    Video,
    VideoRoster,
    WdGame,
    WdMember,
    WdTeam,
)
from wieplay.core.permissions import Viewer
from wieplay.core.search import TagSearch, selector
from wieplay.core.taxonomy import load

router = APIRouter(prefix="/api", tags=["stats"])

Lang = Literal["en", "de"]
Part = Literal["box", "rotations", "setter", "zones"]
Number = int | float | None
# ?search= : a TagSearch as JSON (the web's search bar, saved filters).
SearchParam = Query(default=None, max_length=4000)
# ?sure=true : "Sure only", without what is still to check (PLAN §10, wieplay/core/stats_input.py).
SureParam = Query(default=False)


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
    to_check: int = 0  # of the row's tags, WiePlay's still to check


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


class SheetPlayerOut(BaseModel):
    """One shirt number's numbers from the official scoresheet (PLAN §10.3a, §9.7): its
    serves, the points won on them, its longest run on one service turn, aces and errors
    (from the serve tags of the rallies lined up with the sheet), points on court and the
    plus/minus, also per rotation (ours by the setter's zone when known)."""

    side: str
    number: int
    member_id: int | None = None
    name: str | None = None
    served: int
    won_on_serve: int
    won_on_serve_pct: float | None
    longest_run: int
    aces: int
    errors: int
    tagged: int
    on_court: int
    plus_minus: int
    by_rotation: list[int]


class SheetPlayersOut(BaseModel):
    sets: list[int]  # the sets the sheet was used for
    by_setter: bool  # our rotations are the setter's zone (P1-P6); else 1 = the starting six
    players: list[SheetPlayerOut]


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
    # What is still to check (PLAN §10: rallies, breaks, sets and actions WiePlay is unsure
    # of): counted above, marked; with ?sure=true left out of the numbers.
    to_check: int = 0
    sure_only: bool = False
    # How WiePlay finds the actions (PLAN §10.4a); null for older servers.
    actions_gate: ActionGateOut | None = None
    # With ?search=: the tags it kept (`tags` stays the match's count); null without one.
    selected: int | None = None
    # From the official scoresheet, per player; null without a sheet lined up with the video.
    sheet_players: SheetPlayersOut | None = None


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
    # What is still to check over the matches (counted above; left out with ?sure=true).
    to_check: int = 0
    sure_only: bool = False
    actions_gate: ActionGateOut | None = None
    # With ?search=: the tags it kept over the matches; null without one.
    selected: int | None = None


class ZoneCellOut(BaseModel):
    """A row's serves or attacks from (or to) one zone; zone null: no zone tagged."""

    zone: int | None
    ta: int
    points: int  # aces or kills
    errors: int
    blocked: int  # attacks only
    in_play: int
    point_pct: float | None
    eff: float | None  # serve: (aces - errors) / TA; attack: hitting %


class ZoneRowOut(BaseModel):
    kind: str
    side: str
    number: int | None
    member_id: int | None
    name: str | None
    filter: dict[str, Any]
    # Per skill and end: "serve_from", "serve_to", "attack_from", "attack_to".
    zones: dict[str, list[ZoneCellOut]]


class ZoneScopeOut(BaseModel):
    set_no: int | None  # null: the match (or the season)
    rows: list[ZoneRowOut]


class ZonesOut(BaseModel):
    # The taxonomy's codes of the skills drawn (serve: S, attack: A), for playing a zone.
    codes: dict[str, str]
    scopes: list[ZoneScopeOut]


# --- loading --------------------------------------------------------------------------------


def _check_sport(sport: str) -> None:
    if sport != "volleyball":
        raise HTTPException(409, "Stats are for volleyball so far")


def _search(text: str | None) -> TagSearch | None:
    """?search= as a TagSearch; 422 when it is not one."""
    if not text:
        return None
    try:
        found = TagSearch.model_validate_json(text)
    except ValidationError as exc:
        raise HTTPException(422, "The search is not one WiePlay reads") from exc
    return None if found.is_empty() else found


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
        to_check=row.to_check,
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


# --- zones ----------------------------------------------------------------------------------


def _zone_rows(box: stats.BoxScore, codes: dict[str, str]) -> list[ZoneRowOut]:
    """The rows with serves or attacks, each with its cells per skill and end."""
    out = []
    for row in box.rows:
        if not stats.has_zone_tags(row, codes):
            continue
        zones = {
            f"{category}_{end}": [
                ZoneCellOut(
                    zone=cell.zone,
                    ta=cell.ta,
                    points=cell.points,
                    errors=cell.errors,
                    blocked=cell.blocked,
                    in_play=cell.in_play,
                    point_pct=cell.point_pct,
                    eff=cell.eff,
                )
                for cell in stats.zone_cells(row.places, code, category, end)
            ]
            for category, code in codes.items()
            for end in stats.ZONE_ENDS
        }
        out.append(
            ZoneRowOut(
                kind=row.kind,
                side=row.side,
                number=row.number,
                member_id=row.member_id,
                name=row.name,
                filter=row.filter,
                zones=zones,
            )
        )
    return out


# --- a match --------------------------------------------------------------------------------


def _video_stats(
    db: Session,
    viewer: Viewer,
    video_id: uuid.UUID,
    search: TagSearch | None = None,
    sure_only: bool = False,
) -> tuple[Video, stats.VideoStats, list[stats.Column], int]:
    """The match's numbers (with a search: what it keeps; "sure only": without what is still
    to check) and its count of tags."""
    video, _ = _readable(db, viewer, video_id)
    _check_sport(video.sport)
    tags = stats_input.load_tags(db, [video.id], sure_only)[video.id]
    rallies = stats_input.load_rallies(db, [video.id], sure_only)[video.id]
    sets = stats_input.load_sets(db, [video.id], sure_only)[video.id]
    tax = load("volleyball")
    result = stats.video_stats(
        tags, rallies, sets, _roster(db, video), tax, select=selector(search, tax)
    )
    return video, result, stats.columns(tax), len(tags)


@router.get("/videos/{video_id}/stats", response_model=VideoStatsOut)
def video_stats(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> VideoStatsOut:
    query = _search(search)
    video, result, cols, total = _video_stats(db, viewer, video_id, query, sure)
    rot_cols = stats.rotation_columns(load("volleyball"))
    set_no = {s.set.id: s.set.no for s in result.sets}
    return VideoStatsOut(
        video_id=video.id,
        team_id=video.team_id,
        title=video.title,
        recorded_on=video.recorded_on,
        tags=total,
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
        to_check=tagging.to_check_total(db, [video.id]),
        sure_only=sure,
        actions_gate=gate_out(db, settings),
        selected=result.tags if query is not None else None,
        sheet_players=_sheet_players(db, video),
    )


def _sheet_players(db: Session, video: Video) -> SheetPlayersOut | None:
    found = lineups.for_video(db, video)
    if found is None:
        return None
    roster = _roster(db, video)
    rows = []
    for side, number, line in lineups.player_stats(db, video, found):
        member, name = roster.get(number, (None, None)) if side == "us" else (None, None)
        rows.append(
            SheetPlayerOut(side=side, number=number, member_id=member, name=name, **line.as_json())
        )
    return SheetPlayersOut(
        sets=sorted(found.sets), by_setter=lineups.by_setter(db, video, found), players=rows
    )


@router.get("/videos/{video_id}/stats/zones", response_model=ZonesOut)
def video_zones(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> ZonesOut:
    """The match's serves and attacks per zone: the match and each set, per row."""
    _, result, _, _ = _video_stats(db, viewer, video_id, _search(search), sure)
    codes = stats.zone_codes(load("volleyball"))
    return ZonesOut(
        codes=codes,
        scopes=[
            ZoneScopeOut(set_no=None, rows=_zone_rows(result.box, codes)),
            *(ZoneScopeOut(set_no=s.set.no, rows=_zone_rows(s.box, codes)) for s in result.sets),
        ],
    )


def _labels(db: Session, video: Video, settings: Settings, words: dict[str, str]) -> dict[str, str]:
    """The team's and the opponent's rows by their names ("KSCW H3", "Volero")."""
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    found = names.for_video(
        db, video, team.name, *guests.club_names(team, settings.club_short_name, settings.club_name)
    )
    return {"team": found.us, "opponent": found.name("them", words["opponent"])}


@router.get("/videos/{video_id}/stats.csv")
def video_stats_csv(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
    lang: Lang = "en",
    part: Part = "box",
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> Response:
    video, result, cols, _ = _video_stats(db, viewer, video_id, _search(search), sure)
    words = stats.HEADERS[lang]
    match, sets = words["all"], [(str(s.set.no), s) for s in result.sets]
    if part == "rotations":
        rot_cols = stats.rotation_columns(load("volleyball"))
        rotations = [(match, result.rotations), *((no, s.rotations) for no, s in sets)]
        body = stats.rotations_csv(rot_cols, rotations, lang)
    elif part == "setter":
        body = stats.setter_csv([(match, result.setter), *((no, s.setter) for no, s in sets)], lang)
    elif part == "zones":
        boxes = [(match, result.box), *((no, s.box) for no, s in sets)]
        codes = stats.zone_codes(load("volleyball"))
        body = stats.zones_csv(boxes, codes, lang, _labels(db, video, settings, words))
    else:
        scopes = [(match, result.box, result.team), *((no, s.box, s.team) for no, s in sets)]
        body = stats.to_csv(cols, scopes, lang, labels=_labels(db, video, settings, words))
    name = f"wieplay-{_PART_NAME[part]}-{video.recorded_on.isoformat()}-{_slug(video.title)}"
    return _csv(body, f"{name}{SURE_SUFFIX if sure else ''}.csv")


# A file of the numbers without what is still to check says so in its name.
SURE_SUFFIX = "-sure-only"


_PART_NAME: dict[str, str] = {
    "box": "stats", "rotations": "rotations", "setter": "setter", "zones": "zones",
}  # fmt: skip


# --- a season -------------------------------------------------------------------------------


def _season(
    db: Session,
    viewer: Viewer,
    team_id: int,
    search: TagSearch | None = None,
    sure_only: bool = False,
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
    tags = stats_input.load_tags(db, ids, sure_only)
    rallies = stats_input.load_rallies(db, ids, sure_only)
    sets = stats_input.load_sets(db, ids, sure_only)
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
    result = stats.season_stats(matches, _names(db, members), tax, select=selector(search, tax))
    return team, result, stats.columns(tax), ids


@router.get("/teams/{team_id}/stats", response_model=TeamStatsOut)
def team_stats(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> TeamStatsOut:
    query = _search(search)
    team, result, cols, ids = _season(db, viewer, team_id, query, sure)
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
        to_check=tagging.to_check_total(db, ids),
        sure_only=sure,
        actions_gate=gate_out(db, settings),
        selected=sum(result.box.team.counts.values()) + sum(result.box.opponent.counts.values())
        if query is not None
        else None,
    )


@router.get("/teams/{team_id}/stats/zones", response_model=ZonesOut)
def team_zones(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> ZonesOut:
    """The season's serves and attacks per zone, per row."""
    _, result, _, _ = _season(db, viewer, team_id, _search(search), sure)
    codes = stats.zone_codes(load("volleyball"))
    scope = ZoneScopeOut(set_no=None, rows=_zone_rows(result.box, codes))
    return ZonesOut(codes=codes, scopes=[scope])


@router.get("/teams/{team_id}/stats.csv")
def team_stats_csv(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    lang: Lang = "en",
    part: Part = "box",
    search: str | None = SearchParam,
    sure: bool = SureParam,
) -> Response:
    team, result, cols, _ = _season(db, viewer, team_id, _search(search), sure)
    words = stats.HEADERS[lang]
    scope = words["season"]
    if part == "rotations":
        rot_cols = stats.rotation_columns(load("volleyball"))
        body = stats.rotations_csv(rot_cols, [(scope, result.rotations)], lang)
    elif part == "setter":
        body = stats.setter_csv([(scope, result.setter)], lang)
    elif part == "zones":
        codes = stats.zone_codes(load("volleyball"))
        body = stats.zones_csv([(scope, result.box)], codes, lang)
    else:
        body = stats.to_csv(cols, [(scope, result.box, result.team)], lang, True)
    season = team.season.replace("/", "-")
    name = f"wieplay-{_PART_NAME[part]}-{_slug(team.name)}-{season}"
    return _csv(body, f"{name}{SURE_SUFFIX if sure else ''}.csv")

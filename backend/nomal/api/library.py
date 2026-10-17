"""Clips, reels, comments and exports (PLAN §6.8, §7.7, §5.1, §5.2).

- A clip is a time range; anyone who may watch a video can keep private clips. Sharing a
  clip or reel with the team is for coaches, team managers and sport admins.
- A smart reel is a saved query evaluated per viewer: it only ever lists moments of videos
  that viewer may watch.
- Comments are pinned to a moment, mention team members, and go to the team, its coaches,
  or one person.
- Exports are watermarked MP4s for coaches, rendered by the media worker, downloadable for
  30 days through a 5-minute link.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nomal.api.access import TeamAccess, team_refs, visible_team, visible_video
from nomal.api.deps import DbDep, SettingsDep, StoreDep, ViewerDep, audit
from nomal.core import pipeline, recap, reels, tagging
from nomal.core.models import (
    Clip,
    Comment,
    Event,
    Job,
    Reel,
    ReelItem,
    Segment,
    Video,
    VideoExport,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from nomal.core.permissions import (
    Viewer,
    can_export,
    can_share_with_team,
    is_team_manager,
    visible_team_ids,
)
from nomal.core.storage import ObjectStore

router = APIRouter(prefix="/api", tags=["library"])

Visibility = Literal["private", "team"]
MAX_EXPORT_MS = 90 * 60 * 1000  # longer than a condensed match needs; keeps one PUT < 5 GB
MAX_EXPORT_PIECES = 300
DOWNLOAD_TTL = 300


# --- schemas -------------------------------------------------------------------------------


class ClipIn(BaseModel):
    video_id: uuid.UUID
    start_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    end_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    event_id: uuid.UUID | None = None
    visibility: Visibility = "private"

    @model_validator(mode="after")
    def _order(self) -> ClipIn:
        if self.end_ms <= self.start_ms:
            raise ValueError("a clip ends after it starts")
        if self.end_ms - self.start_ms > 10 * 60 * 1000:
            raise ValueError("a clip is at most 10 minutes")
        return self


class ClipOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    recorded_on: date
    start_ms: int
    end_ms: int
    title: str
    note: str | None
    event_id: uuid.UUID | None
    owner: int
    owner_name: str | None
    mine: bool
    visibility: str
    created_at: datetime


class ReelItemIn(BaseModel):
    video_id: uuid.UUID
    start_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    end_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    event_id: uuid.UUID | None = None
    clip_id: uuid.UUID | None = None
    caption: str | None = Field(default=None, max_length=200)


class ReelIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["smart", "manual"]
    query: reels.ReelQuery | None = None
    items: list[ReelItemIn] = Field(default_factory=list, max_length=500)
    visibility: Visibility = "private"
    lineage_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _shape(self) -> ReelIn:
        if self.kind == "smart" and self.query is None:
            raise ValueError("a smart reel needs a query")
        if self.kind == "manual" and self.query is not None:
            raise ValueError("a manual reel has items, not a query")
        return self


class ReelOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    query: dict[str, Any] | None
    visibility: str
    lineage_id: uuid.UUID | None
    owner: int
    mine: bool
    created_at: datetime
    updated_at: datetime


class PieceOut(BaseModel):
    video_id: uuid.UUID
    start_ms: int
    end_ms: int
    event_id: uuid.UUID | None
    clip_id: uuid.UUID | None
    caption: str | None
    video_title: str | None
    recorded_on: date | None
    code: str | None
    grade: str | None
    side: str | None
    number: int | None
    member_id: int | None
    player_name: str | None
    # The score after the rally the moment belongs to, when tagged.
    score_us: int | None
    score_them: int | None


class ReelDetailOut(BaseModel):
    reel: ReelOut
    item_count: int
    total_ms: int


class ReelItemsIn(BaseModel):
    items: list[ReelItemIn] = Field(min_length=1, max_length=100)


class PersonOut(BaseModel):
    member_id: int
    name: str
    number: int | None
    roles: list[str]


class CommentIn(BaseModel):
    id: uuid.UUID
    target_type: Literal["video", "event", "segment", "clip"] = "video"
    target_id: uuid.UUID | None = None
    t_ms: int | None = Field(default=None, ge=0, le=24 * 3600 * 1000)
    body: str = Field(min_length=1, max_length=4000)
    mentions: list[int] = Field(default_factory=list, max_length=30)
    audience: Literal["team", "coaches", "person"] = "team"
    recipient: int | None = None

    @model_validator(mode="after")
    def _shape(self) -> CommentIn:
        if (self.audience == "person") != (self.recipient is not None):
            raise ValueError("a comment to one person names exactly that person")
        if (self.target_type == "video") != (self.target_id is None):
            raise ValueError("comments on a tag, rally or clip name it")
        return self


class CommentEdit(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class CommentOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID | None
    t_ms: int | None
    author: int
    author_name: str | None
    body: str
    mentions: list[int]
    audience: str
    recipient: int | None
    recipient_name: str | None
    created_at: datetime
    edited_at: datetime | None
    mine: bool
    can_delete: bool


class ExportIn(BaseModel):
    kind: Literal["clip", "reel", "condensed", "recap"]
    clip_id: uuid.UUID | None = None
    reel_id: uuid.UUID | None = None
    video_id: uuid.UUID | None = None  # condensed match, game recap
    title: str | None = Field(default=None, max_length=200)
    # The language of what a game recap writes on the picture (the viewer's).
    lang: Literal["de", "en"] = "en"


class ExportOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    duration_ms: int
    status: str
    bytes: int | None
    progress: float | None
    error: str | None
    created_at: datetime
    expires_at: datetime | None


# --- helpers -------------------------------------------------------------------------------


def _visible_ids(db: Session, viewer: Viewer) -> set[int]:
    return visible_team_ids(viewer, team_refs(db))


def _watchable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = visible_video(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(404, "Video not found")
    return video, access


def _names(db: Session, member_ids: set[int]) -> dict[int, str]:
    if not member_ids:
        return {}
    return {
        m.id: m.display_name
        for m in db.scalars(select(WdMember).where(WdMember.id.in_(member_ids)))
    }


def _pieces_out(db: Session, pieces: list[reels.Piece]) -> list[PieceOut]:
    """Pieces with their tag (a manual item names only the event), the player's name and
    the score after the rally. An event counts only inside the piece's own video, which
    the caller already checked the viewer may watch."""
    wanted = {(p.video_id, p.event_id) for p in pieces if p.event_id}
    tags: dict[uuid.UUID, tuple[Event, int | None, int | None]] = {}
    if wanted:
        rows = db.execute(
            select(Event, Segment.score_us_after, Segment.score_them_after)
            .outerjoin(Segment, Segment.id == Event.segment_id)
            .where(Event.id.in_({e for _, e in wanted}), Event.deleted_at.is_(None))
        ).all()
        tags = {e.id: (e, us, them) for e, us, them in rows if (e.video_id, e.id) in wanted}
        # Basketball has no rallies: the score is the running score right after the tag.
        for video_id in {e.video_id for e, _, _ in tags.values() if e.sport == "basketball"}:
            running, _ = tagging.basketball_running_score(db, video_id)
            for event_id, (event, _, _) in list(tags.items()):
                if event.video_id == video_id and event_id in running:
                    tags[event_id] = (event, *running[event_id])
    filled = []
    for p in pieces:
        tag = tags.get(p.event_id) if p.event_id else None
        if tag is not None and p.code is None:
            event = tag[0]
            p = replace(
                p,
                code=event.code,
                grade=(event.attributes or {}).get("grade"),
                side=event.side,
                number=event.number,
                member_id=event.member_id,
            )
        filled.append((p, tag))
    names = _names(db, {p.member_id for p, _ in filled if p.member_id})
    out = []
    for p, tag in filled:
        out.append(
            PieceOut(
                video_id=p.video_id,
                start_ms=p.start_ms,
                end_ms=p.end_ms,
                event_id=p.event_id,
                clip_id=p.clip_id,
                caption=p.caption,
                video_title=p.video_title,
                recorded_on=p.recorded_on,
                code=p.code,
                grade=p.grade,
                side=p.side,
                number=p.number,
                member_id=p.member_id,
                player_name=names.get(p.member_id) if p.member_id else None,
                score_us=tag[1] if tag else None,
                score_them=tag[2] if tag else None,
            )
        )
    return out


def _clip_out(clip: Clip, video: Video, viewer: Viewer, owner_name: str | None = None) -> ClipOut:
    return ClipOut(
        id=clip.id,
        video_id=clip.video_id,
        video_title=video.title,
        recorded_on=video.recorded_on,
        start_ms=clip.start_ms,
        end_ms=clip.end_ms,
        title=clip.title,
        note=clip.note,
        event_id=clip.event_id,
        owner=clip.owner,
        owner_name=owner_name,
        mine=clip.owner == viewer.member_id,
        visibility=clip.visibility,
        created_at=clip.created_at,
    )


def _team_member_ids(db: Session, video: Video) -> set[int]:
    """People of the video's team (any season): who can be mentioned or addressed."""
    return set(
        db.scalars(
            select(WdTeamMember.member_id)
            .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
            .where(WdTeam.lineage_id == video.lineage_id)
        )
    )


# --- clips ---------------------------------------------------------------------------------


@router.get("/videos/{video_id}/clips", response_model=list[ClipOut])
def video_clips(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[ClipOut]:
    video, _ = _watchable(db, viewer, video_id)
    rows = db.scalars(
        select(Clip)
        .where(
            Clip.video_id == video.id,
            Clip.deleted_at.is_(None),
            or_(Clip.owner == viewer.member_id, Clip.visibility == "team"),
        )
        .order_by(Clip.start_ms)
    )
    clips = list(rows)
    names = _names(db, {c.owner for c in clips})
    return [_clip_out(c, video, viewer, names.get(c.owner)) for c in clips]


@router.get("/clips/mine", response_model=list[ClipOut])
def my_clips(viewer: ViewerDep, db: DbDep) -> list[ClipOut]:
    """My clips across videos ("my clips" on a phone)."""
    ids = _visible_ids(db, viewer)
    rows = db.execute(
        select(Clip, Video)
        .join(Video, Video.id == Clip.video_id)
        .where(
            Clip.owner == viewer.member_id,
            Clip.deleted_at.is_(None),
            Video.status == "ready",
            Video.team_id.in_(ids),
        )
        .order_by(Video.recorded_on.desc(), Clip.start_ms)
    ).all()
    return [_clip_out(c, v, viewer, viewer.name) for c, v in rows]


@router.put("/clips/{clip_id}", response_model=ClipOut)
def put_clip(clip_id: uuid.UUID, body: ClipIn, viewer: ViewerDep, db: DbDep) -> ClipOut:
    video, access = _watchable(db, viewer, body.video_id)
    if video.duration_ms is not None and body.start_ms > video.duration_ms:
        raise HTTPException(422, "The clip starts after the video ends")
    if body.visibility == "team" and not can_share_with_team(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only coaches share clips with the team")
    clip = db.get(Clip, clip_id)
    if clip is not None and (clip.owner != viewer.member_id or clip.video_id != video.id):
        raise HTTPException(409, "This id is already in use")
    if clip is None:
        clip = Clip(id=clip_id, owner=viewer.member_id, video_id=video.id)
        db.add(clip)
    clip.start_ms, clip.end_ms = body.start_ms, min(body.end_ms, video.duration_ms or body.end_ms)
    clip.title, clip.note, clip.event_id = body.title.strip(), body.note, body.event_id
    clip.visibility = body.visibility
    clip.deleted_at = None
    db.commit()
    return _clip_out(clip, video, viewer, viewer.name if clip.owner == viewer.member_id else None)


@router.delete("/clips/{clip_id}", status_code=204)
def delete_clip(clip_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> None:
    clip = db.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None:
        raise HTTPException(404, "Clip not found")
    _, access = visible_video(db, viewer, clip.video_id)
    shared_by_coach = clip.visibility == "team" and is_team_manager(
        viewer, access.ref, access.leads
    )
    if clip.owner != viewer.member_id and not shared_by_coach:
        raise HTTPException(404, "Clip not found")
    clip.deleted_at = datetime.now(UTC)
    db.commit()


# --- reels ---------------------------------------------------------------------------------


def _reel_out(reel: Reel, viewer: Viewer) -> ReelOut:
    return ReelOut(
        id=reel.id,
        title=reel.title,
        kind=reel.kind,
        query=reel.query,
        visibility=reel.visibility,
        lineage_id=reel.lineage_id,
        owner=reel.owner,
        mine=reel.owner == viewer.member_id,
        created_at=reel.created_at,
        updated_at=reel.updated_at,
    )


def _visible_lineages(db: Session, viewer: Viewer) -> set[uuid.UUID]:
    ids = _visible_ids(db, viewer)
    return set(db.scalars(select(WdTeam.lineage_id).where(WdTeam.id.in_(ids))))


def _readable_reel(db: Session, viewer: Viewer, reel_id: uuid.UUID) -> Reel:
    reel = db.get(Reel, reel_id)
    if reel is None or reel.deleted_at is not None:
        raise HTTPException(404, "Reel not found")
    if reel.owner == viewer.member_id:
        return reel
    if reel.visibility == "team" and reel.lineage_id in _visible_lineages(db, viewer):
        return reel
    raise HTTPException(404, "Reel not found")


def _check_team_share(db: Session, viewer: Viewer, body: ReelIn) -> None:
    if body.visibility != "team":
        return
    if body.lineage_id is None:
        raise HTTPException(422, "A team reel names its team")
    refs = [r for r in team_refs(db) if r.lineage_id == body.lineage_id]
    leads = {r.lineage_id for r in refs if viewer.team_rels.get(r.id, frozenset()) & {
        "coach", "team_responsible"}}  # fmt: skip
    if not refs or not any(can_share_with_team(viewer, r, leads) for r in refs):
        raise HTTPException(403, "Only coaches share reels with the team")


def _check_items(db: Session, viewer: Viewer, items: list[ReelItemIn]) -> None:
    ids = _visible_ids(db, viewer)
    videos = {
        v.id: v for v in db.scalars(select(Video).where(Video.id.in_({i.video_id for i in items})))
    }
    for item in items:
        video = videos.get(item.video_id)
        if video is None or video.status != "ready" or video.team_id not in ids:
            raise HTTPException(422, "A reel item names a video you cannot watch")
        if item.end_ms <= item.start_ms:
            raise HTTPException(422, "A reel item ends after it starts")
    # A named tag or clip belongs to the item's own video.
    event_ids = {i.event_id for i in items if i.event_id}
    if event_ids:
        found = {
            (r[0], r[1])
            for r in db.execute(select(Event.id, Event.video_id).where(Event.id.in_(event_ids)))
        }
        if any(i.event_id and (i.event_id, i.video_id) not in found for i in items):
            raise HTTPException(422, "A reel item names a tag of another video")
    clip_ids = {i.clip_id for i in items if i.clip_id}
    if clip_ids:
        found = set(
            db.execute(select(Clip.id, Clip.video_id).where(Clip.id.in_(clip_ids))).tuples()
        )
        if any(i.clip_id and (i.clip_id, i.video_id) not in found for i in items):
            raise HTTPException(422, "A reel item names a clip of another video")


def _store_items(db: Session, viewer: Viewer, reel: Reel, items: list[ReelItemIn]) -> None:
    _check_items(db, viewer, items)
    reel.items.clear()
    for position, item in enumerate(items):
        reel.items.append(ReelItem(position=position, **item.model_dump()))


@router.get("/reels", response_model=list[ReelOut])
def list_reels(viewer: ViewerDep, db: DbDep) -> list[ReelOut]:
    lineages = _visible_lineages(db, viewer)
    rows = db.scalars(
        select(Reel)
        .where(
            Reel.deleted_at.is_(None),
            or_(
                Reel.owner == viewer.member_id,
                (Reel.visibility == "team") & Reel.lineage_id.in_(lineages),
            ),
        )
        .order_by(Reel.updated_at.desc())
    )
    return [_reel_out(r, viewer) for r in rows]


@router.post("/reels", response_model=ReelOut, status_code=201)
def create_reel(body: ReelIn, viewer: ViewerDep, db: DbDep) -> ReelOut:
    _check_team_share(db, viewer, body)
    reel = Reel(
        id=uuid.uuid4(),
        owner=viewer.member_id,
        title=body.title.strip(),
        kind=body.kind,
        query=body.query.model_dump(mode="json") if body.query else None,
        visibility=body.visibility,
        lineage_id=body.lineage_id,
    )
    db.add(reel)
    if body.kind == "manual":
        _store_items(db, viewer, reel, body.items)
    db.commit()
    return _reel_out(reel, viewer)


@router.put("/reels/{reel_id}", response_model=ReelOut)
def update_reel(reel_id: uuid.UUID, body: ReelIn, viewer: ViewerDep, db: DbDep) -> ReelOut:
    reel = db.get(Reel, reel_id)
    if reel is None or reel.deleted_at is not None or reel.owner != viewer.member_id:
        raise HTTPException(404, "Reel not found")
    _check_team_share(db, viewer, body)
    reel.title, reel.kind, reel.visibility = body.title.strip(), body.kind, body.visibility
    reel.lineage_id = body.lineage_id
    reel.query = body.query.model_dump(mode="json") if body.query else None
    if body.kind == "manual":
        _store_items(db, viewer, reel, body.items)
    else:
        reel.items.clear()
    db.commit()
    return _reel_out(reel, viewer)


@router.delete("/reels/{reel_id}", status_code=204)
def delete_reel(reel_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> None:
    reel = db.get(Reel, reel_id)
    if reel is None or reel.deleted_at is not None or reel.owner != viewer.member_id:
        raise HTTPException(404, "Reel not found")
    reel.deleted_at = datetime.now(UTC)
    db.commit()


def _reel_pieces(db: Session, viewer: Viewer, reel: Reel) -> list[reels.Piece]:
    ids = _visible_ids(db, viewer)
    if reel.kind == "smart":
        return reels.evaluate(db, ids, reels.ReelQuery.model_validate(reel.query or {}))
    videos = {
        v.id: v
        for v in db.scalars(select(Video).where(Video.id.in_({i.video_id for i in reel.items})))
    }
    pieces = []
    for item in reel.items:
        video = videos.get(item.video_id)
        if video is None or video.status != "ready" or video.team_id not in ids:
            continue  # gone, or not for this viewer
        pieces.append(
            reels.Piece(
                video_id=video.id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                event_id=item.event_id,
                clip_id=item.clip_id,
                caption=item.caption,
                video_title=video.title,
                recorded_on=video.recorded_on,
            )
        )
    return pieces


@router.get("/reels/{reel_id}/items", response_model=list[PieceOut])
def reel_items(reel_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[PieceOut]:
    return _pieces_out(db, _reel_pieces(db, viewer, _readable_reel(db, viewer, reel_id)))


@router.post("/reels/preview", response_model=list[PieceOut])
def preview_reel(query: reels.ReelQuery, viewer: ViewerDep, db: DbDep) -> list[PieceOut]:
    """Evaluate a query without saving it: "my attacks this season", the highlight builder."""
    return _pieces_out(db, reels.evaluate(db, _visible_ids(db, viewer), query))


@router.get("/reels/{reel_id}", response_model=ReelDetailOut)
def get_reel(reel_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> ReelDetailOut:
    reel = _readable_reel(db, viewer, reel_id)
    pieces = _reel_pieces(db, viewer, reel)
    return ReelDetailOut(
        reel=_reel_out(reel, viewer), item_count=len(pieces), total_ms=reels.total_ms(pieces)
    )


@router.post("/reels/{reel_id}/items", response_model=ReelDetailOut)
def append_reel_items(
    reel_id: uuid.UUID, body: ReelItemsIn, viewer: ViewerDep, db: DbDep
) -> ReelDetailOut:
    """Add items at the end of a manual reel, under a row lock (two adds never lose one)."""
    reel = db.get(Reel, reel_id, with_for_update=True)
    if reel is None or reel.deleted_at is not None or reel.owner != viewer.member_id:
        raise HTTPException(404, "Reel not found")
    if reel.kind != "manual":
        raise HTTPException(409, "A smart reel takes no items")
    if len(reel.items) + len(body.items) > 500:
        raise HTTPException(413, "A reel holds at most 500 items")
    # Only the new items are checked: an old one whose video went away is skipped on
    # playback and must not block adding more.
    _check_items(db, viewer, body.items)
    start = max((i.position for i in reel.items), default=-1) + 1
    for offset, item in enumerate(body.items):
        reel.items.append(ReelItem(position=start + offset, **item.model_dump()))
    reel.updated_at = datetime.now(UTC)
    db.commit()
    return get_reel(reel_id, viewer, db)


@router.get("/videos/{video_id}/people", response_model=list[PersonOut])
def video_people(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[PersonOut]:
    """The people of the video's team (any season): who can be mentioned or addressed."""
    video, _ = _watchable(db, viewer, video_id)
    rows = db.execute(
        select(WdTeamMember.member_id, WdTeamMember.rel)
        .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
        .where(WdTeam.lineage_id == video.lineage_id)
    ).all()
    roles: dict[int, set[str]] = {}
    for member_id, rel in rows:
        roles.setdefault(member_id, set()).add(rel)
    members = {m.id: m for m in db.scalars(select(WdMember).where(WdMember.id.in_(roles)))}
    out = [
        PersonOut(member_id=mid, name=m.display_name, number=m.number, roles=sorted(roles[mid]))
        for mid, m in members.items()
    ]
    return sorted(out, key=lambda p: (p.number is None, p.number or 0, p.name))


@router.get("/videos/{video_id}/condensed", response_model=list[PieceOut])
def condensed(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[PieceOut]:
    video, _ = _watchable(db, viewer, video_id)
    return _pieces_out(db, reels.condensed(db, video))


# --- comments ------------------------------------------------------------------------------


def _comment_visible(comment: Comment, viewer: Viewer, manager: bool) -> bool:
    if comment.deleted_at is not None:
        return False
    if comment.author == viewer.member_id or comment.audience == "team":
        return True
    if comment.audience == "coaches":
        return manager
    return comment.recipient == viewer.member_id or manager


@router.get("/videos/{video_id}/comments", response_model=list[CommentOut])
def list_comments(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[CommentOut]:
    video, access = _watchable(db, viewer, video_id)
    manager = is_team_manager(viewer, access.ref, access.leads)
    rows = db.scalars(
        select(Comment)
        .where(Comment.video_id == video.id)
        .order_by(Comment.t_ms.nulls_first(), Comment.created_at)
    )
    visible = [c for c in rows if _comment_visible(c, viewer, manager)]
    names = _names(db, {c.author for c in visible} | {c.recipient for c in visible if c.recipient})
    return [
        CommentOut(
            id=c.id,
            video_id=c.video_id,
            target_type=c.target_type,
            target_id=c.target_id,
            t_ms=c.t_ms,
            author=c.author,
            author_name=names.get(c.author),
            body=c.body,
            mentions=list(c.mentions or []),
            audience=c.audience,
            recipient=c.recipient,
            recipient_name=names.get(c.recipient) if c.recipient else None,
            created_at=c.created_at,
            edited_at=c.edited_at,
            mine=c.author == viewer.member_id,
            can_delete=c.author == viewer.member_id or manager,
        )
        for c in visible
    ]


@router.post("/videos/{video_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(video_id: uuid.UUID, body: CommentIn, viewer: ViewerDep, db: DbDep) -> CommentOut:
    video, access = _watchable(db, viewer, video_id)
    if db.get(Comment, body.id) is not None:
        raise HTTPException(409, "This id is already in use")
    members = _team_member_ids(db, video)
    if any(m not in members for m in body.mentions):
        raise HTTPException(422, "Only people of this team can be mentioned")
    if body.recipient is not None and body.recipient not in members:
        raise HTTPException(422, "Only people of this team can be addressed")
    comment = Comment(
        id=body.id,
        video_id=video.id,
        target_type=body.target_type,
        target_id=body.target_id,
        t_ms=body.t_ms,
        author=viewer.member_id,
        body=body.body.strip(),
        mentions=sorted(set(body.mentions)),
        audience=body.audience,
        recipient=body.recipient,
    )
    db.add(comment)
    db.commit()
    return list_comment_out(db, viewer, access, comment)


def list_comment_out(
    db: Session, viewer: Viewer, access: TeamAccess, comment: Comment
) -> CommentOut:
    return next(c for c in list_comments(comment.video_id, viewer, db) if c.id == comment.id)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def edit_comment(
    comment_id: uuid.UUID, body: CommentEdit, viewer: ViewerDep, db: DbDep
) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None or comment.deleted_at is not None or comment.author != viewer.member_id:
        raise HTTPException(404, "Comment not found")
    _, access = _watchable(db, viewer, comment.video_id)
    comment.body, comment.edited_at = body.body.strip(), datetime.now(UTC)
    db.commit()
    return list_comment_out(db, viewer, access, comment)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> None:
    comment = db.get(Comment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise HTTPException(404, "Comment not found")
    _, access = _watchable(db, viewer, comment.video_id)
    manager = is_team_manager(viewer, access.ref, access.leads)
    if comment.author != viewer.member_id and not manager:
        raise HTTPException(404, "Comment not found")
    comment.deleted_at = datetime.now(UTC)
    db.commit()


# --- exports -------------------------------------------------------------------------------


def _export_out(export: VideoExport, db: Session) -> ExportOut:
    progress = None
    if export.status == "queued" and export.job_id:
        job = db.get(Job, export.job_id)
        progress = job.progress if job else None
    return ExportOut(
        id=export.id,
        kind=export.kind,
        title=export.title,
        duration_ms=export.duration_ms,
        status=export.status,
        bytes=export.bytes,
        progress=progress,
        error=export.error,
        created_at=export.created_at,
        expires_at=export.expires_at,
    )


SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def _filename(export: VideoExport) -> str:
    stem = SAFE.sub("", export.title).strip()[:80] or "nomal-export"
    return f"{stem}.mp4"


@router.post("/exports", response_model=ExportOut, status_code=201)
def create_export(body: ExportIn, viewer: ViewerDep, db: DbDep) -> ExportOut:
    """Queue a watermarked MP4 (coaches, team managers, sport admins; PLAN §5.2)."""
    recap_params: dict[str, Any] | None = None
    if body.kind == "clip":
        clip = db.get(Clip, body.clip_id) if body.clip_id else None
        if clip is None or clip.deleted_at is not None:
            raise HTTPException(404, "Clip not found")
        video, _ = _watchable(db, viewer, clip.video_id)
        if clip.owner != viewer.member_id and clip.visibility != "team":
            raise HTTPException(404, "Clip not found")
        pieces = [reels.Piece(video_id=video.id, start_ms=clip.start_ms, end_ms=clip.end_ms)]
        title = body.title or clip.title
    elif body.kind == "condensed":
        if body.video_id is None:
            raise HTTPException(422, "A condensed match names its video")
        video, _ = _watchable(db, viewer, body.video_id)
        pieces = reels.condensed(db, video)
        if not pieces:
            raise HTTPException(409, "This video has no rallies marked yet")
        title = body.title or f"{video.title} (condensed)"
    elif body.kind == "recap":
        if body.video_id is None:
            raise HTTPException(422, "A game recap names its video")
        video, _ = _watchable(db, viewer, body.video_id)
        if video.sport != "volleyball":
            raise HTTPException(409, "Game recaps are for volleyball matches")
        chosen = recap.select_plays(db, video)
        if not chosen.plays:
            raise HTTPException(409, "This video has no rallies or plays to choose from yet")
        pieces = [
            reels.Piece(video_id=video.id, start_ms=p.start_ms, end_ms=p.end_ms)
            for p in chosen.plays
        ]
        card, sides = recap.card(db, video, body.lang)
        recap_params = {
            "card": asdict(card),
            "card_ms": recap.TITLE_MS,
            "bugs": [recap.bug(p, sides, body.lang) for p in chosen.plays],
            "source": chosen.source,
            "lang": body.lang,
        }
        title = body.title or recap.title_of(video, body.lang)
    else:
        reel = _readable_reel(db, viewer, body.reel_id) if body.reel_id else None
        if reel is None:
            raise HTTPException(404, "Reel not found")
        pieces = reels.merge(_reel_pieces(db, viewer, reel))
        title = body.title or reel.title
    if not pieces:
        raise HTTPException(409, "Nothing to export")
    if len(pieces) > MAX_EXPORT_PIECES or reels.total_ms(pieces) > MAX_EXPORT_MS:
        raise HTTPException(413, "The export is too long (at most 90 minutes)")

    videos = {
        v.id: v for v in db.scalars(select(Video).where(Video.id.in_({p.video_id for p in pieces})))
    }
    for video in videos.values():
        _, access = visible_video(db, viewer, video.id)
        if not can_export(viewer, access.ref, access.leads):
            raise HTTPException(403, "Only coaches and sport admins download videos")
        if not all(f.nas_path and f.offset_ms is not None for f in video.files):
            raise HTTPException(409, f"The originals of {video.title} are not available")

    export = VideoExport(
        id=uuid.uuid4(),
        kind=body.kind,
        title=title.strip()[:200],
        duration_ms=reels.total_ms(pieces) + (recap.TITLE_MS if recap_params else 0),
        source={
            "clip_id": str(body.clip_id) if body.clip_id else None,
            "reel_id": str(body.reel_id) if body.reel_id else None,
            "video_id": str(body.video_id) if body.video_id else None,
        },
        status="queued",
        requested_by=viewer.member_id,
    )
    db.add(export)
    job_pieces = [
        {
            "video_id": str(p.video_id),
            "start_ms": p.start_ms,
            "end_ms": p.end_ms,
            # Each camera file with its place in the video's timeline (set at ingest).
            "parts": [
                {"path": f.nas_path, "offset_ms": f.offset_ms, "duration_ms": f.duration_ms}
                for f in videos[p.video_id].files
            ],
            "has_audio": bool(videos[p.video_id].has_audio),
        }
        for p in pieces
    ]
    if recap_params is not None:
        # The score bug of each clip goes with it (worker/recap.py draws it).
        for item, bug in zip(job_pieces, recap_params.pop("bugs"), strict=True):
            item["bug"] = bug
    watermark = f"KSCW intern · {viewer.name} · {datetime.now(UTC):%d.%m.%Y}"
    pipeline.enqueue_export(db, export, job_pieces, watermark, recap=recap_params)
    audit(db, viewer.member_id, "export_requested", "export", str(export.id), kind=body.kind,
          pieces=len(pieces), duration_ms=export.duration_ms)  # fmt: skip
    db.commit()
    return _export_out(export, db)


@router.get("/exports", response_model=list[ExportOut])
def my_exports(viewer: ViewerDep, db: DbDep) -> list[ExportOut]:
    rows = db.scalars(
        select(VideoExport)
        .where(VideoExport.requested_by == viewer.member_id, VideoExport.status != "deleted")
        .order_by(VideoExport.created_at.desc())
        .limit(100)
    )
    return [_export_out(e, db) for e in rows]


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: uuid.UUID, viewer: ViewerDep, db: DbDep, store: StoreDep, settings: SettingsDep
) -> RedirectResponse:
    export = db.get(VideoExport, export_id)
    if export is None or export.requested_by != viewer.member_id:
        raise HTTPException(404, "Export not found")
    if export.status != "ready" or export.object_key is None:
        raise HTTPException(409, "This export is not ready")
    if export.expires_at is not None and export.expires_at < datetime.now(UTC):
        raise HTTPException(410, "This export has expired")
    audit(db, viewer.member_id, "export_downloaded", "export", str(export.id))
    db.commit()
    url = store.presign_get(export.object_key, DOWNLOAD_TTL, download_name=_filename(export))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, no-store"})


@router.delete("/exports/{export_id}", status_code=204)
def delete_export(export_id: uuid.UUID, viewer: ViewerDep, db: DbDep, store: StoreDep) -> None:
    export = db.get(VideoExport, export_id)
    if export is None or export.requested_by != viewer.member_id:
        raise HTTPException(404, "Export not found")
    if export.object_key:
        store.delete([export.object_key])
    export.status = "deleted"
    db.commit()


def sweep_exports(db: Session, store: ObjectStore, now: datetime | None = None) -> int:
    """Exports live 30 days (PLAN §5.5); the bucket's lifecycle rule is the backstop."""
    now = now or datetime.now(UTC)
    rows = db.scalars(
        select(VideoExport).where(VideoExport.status == "ready", VideoExport.expires_at < now)
    ).all()
    for export in rows:
        if export.object_key:
            store.delete([export.object_key])
        export.status = "expired"
    db.commit()
    return len(rows)


__all__ = ["router", "sweep_exports", "visible_team"]

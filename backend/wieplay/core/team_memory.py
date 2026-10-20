"""What WiePlay knows about a team (PLAN §10.5b; the owner, 20.10.2026: "save per team the
players, rotation, and training", and "training" as per-team learning: every game a team
reviews makes WiePlay better for that team).

    the team's games ─▶ facts per game: the typed sheet's sets (our six, substitutions), a
        coach's starting six and setter zone, the cards and tags a coach named with a number,
        the liberos of the roster snapshot
    + the roster (Wiedisync, or a guest team's own) and the coaches' fixes on the team page
    ─▶ per season's team row: our numbers with the member wearing each and how many games
       named it (`team_player`), the usual service orders and starting rotations, how well
       they predicted the team's own sheets, the models' scores for the team (`team_memory`)
    ─▶ a new game of the team: the roster snapshot's number ↔ member links, a prior on the
       numbers read on its cards (`card_memory`), our likely server and six per rally where
       the sheet is missing (core/lineups.py `prior_for_video`)

Privacy (PLAN §5.7; the owner's nDSG line): shirt numbers, the roster, the sheets' and the
coaches' facts only. No faces, no face or body embeddings, no appearance of a person is
stored or compared across videos; the opponents have no memory (their numbers stay in their
video). A guest team's memory is its own: nothing of one team ever informs another.

Refreshed on use when its inputs changed (`get`: a fingerprint of the team's sheets, sets,
named cards and tags, roster, fixes and trainings), so no other module has to call it.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from wieplay.core import tagging
from wieplay.core.db import Base
from wieplay.core.models import (
    Event,
    ModelRun,
    Period,
    Scoresheet,
    Video,
    VideoRoster,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from wieplay.ml import on_court
from wieplay.ml import sheet as sh
from wieplay.ml import team_prior as tp

# Bump when what a refresh computes changes: every team's memory is refreshed on next use.
MEMORY_VERSION = 1
SPORTS = frozenset({"volleyball"})
# An earlier season of the same lineage informs the rotations at this weight (the numbers
# and the members wearing them are the season's own).
EARLIER_SEASON = 0.5
# Roster positions of people who rarely play: with several members on one number, the one
# without them is taken (Wiedisync lists guests and staff with numbers too).
NOT_PLAYING = frozenset({"guest", "staff_only", "other"})
# A substitute counts as half a set for the number.
SUB_SETS = 0.5
# The memory is learnt again at most this often on use (seconds).
MIN_REFRESH_S = 60


class TeamPlayer(Base):
    """One of our shirt numbers in a season's team: the member wearing it and how often the
    team's games named it (no pictures, no appearance)."""

    __tablename__ = "team_player"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("wd_team.id", ondelete="CASCADE"), index=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("team_lineage.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    member_id: Mapped[int | None] = mapped_column(Integer)
    libero: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    games: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    answers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sets: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    last_on: Mapped[date | None] = mapped_column(Date)
    # The video ids of those games (a video's own facts never count as "earlier games" for it).
    videos: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(16), default="roster", server_default="roster")
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fixed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fixed_by: Mapped[int | None] = mapped_column(Integer)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TeamMemory(Base):
    """A season's team: its usual rotations, the models' scores for it, and what they were
    learnt from."""

    __tablename__ = "team_memory"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("wd_team.id", ondelete="CASCADE"), primary_key=True
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("team_lineage.id", ondelete="CASCADE"), index=True
    )
    games: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    named_videos: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    rotations: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    models: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- what one game says ---------------------------------------------------------------------------


@dataclass
class SheetSetFacts:
    """A typed sheet set the scoring used: the check of the rotations is made on these."""

    no: int
    sheet: sh.SheetSet
    decoded: sh.Decoded
    setter: int | None


@dataclass
class GameFacts:
    video_id: uuid.UUID
    on: date
    numbers: set[int] = field(default_factory=set)
    answers: Counter[int] = field(default_factory=Counter)
    sets: dict[int, float] = field(default_factory=dict)
    starts: list[tp.SetStart] = field(default_factory=list)
    liberos: set[int] = field(default_factory=set)
    sheet_sets: list[SheetSetFacts] = field(default_factory=list)

    def add_set(self, numbers: Iterable[int | None], weight: float = 1.0) -> None:
        for n in numbers:
            if n is not None:
                self.numbers.add(int(n))
                self.sets[int(n)] = self.sets.get(int(n), 0.0) + weight

    @property
    def named(self) -> set[int]:
        return self.numbers | set(self.answers)


def setter_in(lineup: Sequence[int | None], setters: set[int], period: Period | None) -> int | None:
    """Our setter in a starting six: the coach's starting rotation (the setter's zone), else
    the one setter of the roster among the six."""
    if period is not None and period.start_rotation in (1, 2, 3, 4, 5, 6):
        assert period.start_rotation is not None
        found = lineup[period.start_rotation - 1]
        if found is not None:
            return int(found)
    hits = [int(n) for n in lineup if n is not None and n in setters]
    return hits[0] if len(hits) == 1 else None


def setters_of(db: Session, team_id: int) -> set[int]:
    """Our setters by number: the roster's positions (Wiedisync: the first one listed)."""
    out: set[int] = set()
    for m in _roster_members(db, team_id):
        positions = [str(p).lower() for p in (m.positions or [])]
        if positions and positions[0] == "setter" and m.number:
            out.add(int(m.number))
    return out


def game_facts(db: Session, video: Video, setters: set[int]) -> GameFacts:
    """Our side of one game: the sheet's used sets, a coach's starting sixes, the cards and
    tags a coach named with a number, the snapshot's liberos. Never the opponent's."""
    from wieplay.core import scoresheet
    from wieplay.core.players import PlayerCluster

    facts = GameFacts(video.id, video.recorded_on)
    periods = {
        p.no: p
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    covered: set[int] = set()
    row = scoresheet.stored(db, video.id)
    if row is not None and row.sets and row.use_in_scoring:
        checks = scoresheet.check(
            row.sets, scoresheet.official_sets(db, video), scoresheet.drafts_of(row)
        )
        for c in checks:
            lineup = c.sheet.lineup.get("us")
            if c.use(True) != "path" or not lineup or len(lineup) != 6:
                continue
            covered.add(c.no)
            setter = setter_in(lineup, setters, periods.get(c.no))
            facts.starts.append(tp.SetStart(tuple(lineup), setter))
            facts.sheet_sets.append(SheetSetFacts(c.no, c.sheet, c.decoded, setter))
            facts.add_set(lineup)
            facts.add_set((u.number_in for u in c.sheet.subs if u.side == "us"), weight=SUB_SETS)
    for p in periods.values():
        if p.no in covered or not p.start_lineup or len(p.start_lineup) != 6:
            continue
        lineup = tuple(p.start_lineup)
        facts.starts.append(tp.SetStart(lineup, setter_in(lineup, setters, p)))
        facts.add_set(lineup)
    for n in db.scalars(
        select(PlayerCluster.number).where(
            PlayerCluster.video_id == video.id,
            PlayerCluster.team == "us",
            PlayerCluster.merged_into.is_(None),
            PlayerCluster.assigned_by.is_not(None),
            PlayerCluster.number.is_not(None),
            PlayerCluster.not_player.is_(False),
        )
    ):
        if n is not None:
            facts.answers[int(n)] += 1
    # A person's tags with a number (made, imported, or WiePlay's that a person answered).
    for n in db.scalars(
        select(Event.number).where(
            Event.video_id == video.id,
            Event.side == "us",
            Event.number.is_not(None),
            Event.deleted_at.is_(None),
            tagging.event_fact(),
        )
    ):
        if n is not None:
            facts.answers[int(n)] += 1
    facts.liberos = {
        int(n)
        for n in db.scalars(
            select(VideoRoster.number).where(
                VideoRoster.video_id == video.id,
                VideoRoster.side == "us",
                VideoRoster.is_libero.is_(True),
            )
        )
    }
    return facts


# --- the roster -----------------------------------------------------------------------------------


def _roster_members(db: Session, team_id: int) -> list[WdMember]:
    return list(
        db.scalars(
            select(WdMember)
            .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
            .where(WdTeamMember.team_id == team_id, WdTeamMember.rel == "player")
            .distinct()
            .order_by(WdMember.id)
        )
    )


def roster_by_number(db: Session, team_id: int) -> dict[int, list[WdMember]]:
    """The team's players by shirt number (none: no number)."""
    out: dict[int, list[WdMember]] = {}
    for m in _roster_members(db, team_id):
        if m.number is not None:
            out.setdefault(int(m.number), []).append(m)
    return out


def _playing(m: WdMember) -> bool:
    return not {str(p).lower() for p in (m.positions or [])} & NOT_PLAYING


def resolve(members: Sequence[WdMember]) -> tuple[int | None, bool]:
    """The member wearing a number (several on the roster: the one who plays, i.e. not a guest
    or staff) and whether that stays unclear."""
    if not members:
        return None, False
    if len(members) == 1:
        return members[0].id, False
    playing = [m for m in members if _playing(m)]
    if len(playing) == 1:
        return playing[0].id, False
    return None, True


def fixes(db: Session, team_id: int) -> dict[int, TeamPlayer]:
    """The coaches' fixes of the team: number → row (fixed member or hidden)."""
    return {
        r.number: r
        for r in db.scalars(
            select(TeamPlayer).where(
                TeamPlayer.team_id == team_id,
                (TeamPlayer.fixed.is_(True)) | (TeamPlayer.hidden.is_(True)),
            )
        )
    }


@dataclass(frozen=True)
class Link:
    number: int
    member_id: int | None
    position: str | None
    libero: bool


def roster_links(db: Session, team: WdTeam) -> list[Link]:
    """Our numbers and the member wearing each, as a new roster snapshot takes them (tagging
    `ensure_roster`): the roster, several members on one number resolved (`resolve`), and the
    coaches' fixes (a member on a number, a number that is none of ours)."""
    members = {m.id: m for ms in roster_by_number(db, team.id).values() for m in ms}
    for m in _roster_members(db, team.id):
        members.setdefault(m.id, m)
    out: dict[int, Link] = {}
    for number, ms in roster_by_number(db, team.id).items():
        member_id, _ = resolve(ms)
        if member_id is None:
            member_id = ms[0].id  # unresolved: as before, the first one (the coach can fix)
        out[number] = _link(number, members.get(member_id))
    for number, row in fixes(db, team.id).items():
        if row.hidden:
            out.pop(number, None)
        elif row.fixed:
            out[number] = _link(number, members.get(row.member_id) if row.member_id else None)
            if row.member_id is not None and row.member_id not in members:
                out[number] = Link(number, row.member_id, None, row.libero)
    return [out[n] for n in sorted(out)]


def _link(number: int, m: WdMember | None) -> Link:
    if m is None:
        return Link(number, None, None, False)
    positions = [str(p).lower() for p in (m.positions or [])]
    return Link(number, m.id, positions[0] if positions else None, "libero" in positions)


# --- refresh --------------------------------------------------------------------------------------


def team_videos(db: Session, team: WdTeam) -> list[Video]:
    return list(
        db.scalars(
            select(Video)
            .where(
                Video.team_id == team.id,
                Video.sport.in_(SPORTS),
                Video.status == "ready",
                Video.deleted_at.is_(None),
            )
            .order_by(Video.recorded_on, Video.id)
        )
    )


def earlier_videos(db: Session, team: WdTeam) -> list[Video]:
    """The lineage's videos of earlier seasons (their rotations count half)."""
    return list(
        db.scalars(
            select(Video)
            .join(WdTeam, WdTeam.id == Video.team_id)
            .where(
                WdTeam.lineage_id == team.lineage_id,
                WdTeam.season < team.season,
                Video.sport.in_(SPORTS),
                Video.status == "ready",
                Video.deleted_at.is_(None),
            )
            .order_by(Video.recorded_on, Video.id)
        )
    )


def fingerprint(db: Session, team: WdTeam) -> str:
    """What the memory is learnt from, hashed: refreshed when it changes. Two halves: the
    facts (videos, sheets, starting sixes, roster, fixes, trainings) and the coaches' answers
    (named cards and tags), which change with every answer and are throttled (`get`)."""
    from wieplay.core.players import PlayerCluster

    videos = [*team_videos(db, team), *earlier_videos(db, team)]
    ids = [v.id for v in videos]
    facts: list[Any] = [MEMORY_VERSION, team.id, [(str(v.id), str(v.recorded_on)) for v in videos]]
    answers: list[Any] = []
    if ids:
        facts.append(
            db.execute(
                select(func.count(), func.max(Scoresheet.updated_at)).where(
                    Scoresheet.video_id.in_(ids)
                )
            ).one()
        )
        # The coach's (or the sheet's) starting sixes and setter zones only: the scoring
        # rewrites the sets' other fields on every review decision.
        facts.append(
            db.execute(
                select(Period.video_id, Period.no, Period.start_lineup, Period.start_rotation)
                .where(Period.video_id.in_(ids), Period.deleted_at.is_(None))
                .order_by(Period.video_id, Period.no)
            ).all()
        )
        facts.append(
            db.execute(
                select(func.count()).where(
                    VideoRoster.video_id.in_(ids), VideoRoster.is_libero.is_(True)
                )
            ).scalar()
        )
        answers.append(
            db.execute(
                select(func.count(), func.max(PlayerCluster.updated_at)).where(
                    PlayerCluster.video_id.in_(ids), PlayerCluster.assigned_by.is_not(None)
                )
            ).one()
        )
        answers.append(
            db.execute(
                select(func.count(), func.max(Event.updated_at)).where(
                    Event.video_id.in_(ids),
                    Event.side == "us",
                    Event.number.is_not(None),
                    tagging.event_fact(),
                )
            ).one()
        )
    facts.append(
        sorted(
            (n, m.id, list(m.positions or []))
            for n, ms in roster_by_number(db, team.id).items()
            for m in ms
        )
    )
    facts.append(
        db.execute(
            select(func.count(), func.max(TeamPlayer.fixed_at)).where(
                TeamPlayer.team_id == team.id,
                (TeamPlayer.fixed.is_(True)) | (TeamPlayer.hidden.is_(True)),
            )
        ).one()
    )
    for kind in ("train_rallies", "train_actions"):
        facts.append(
            db.scalar(
                select(ModelRun.id)
                .where(ModelRun.kind == kind)
                .order_by(ModelRun.created_at.desc())
                .limit(1)
            )
        )

    def digest(parts: list[Any]) -> str:
        text = json.dumps(parts, default=str, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    return digest(facts) + digest(answers)


def _lock(db: Session, team: WdTeam) -> TeamMemory:
    db.execute(
        insert(TeamMemory)
        .values(team_id=team.id, lineage_id=team.lineage_id)
        .on_conflict_do_nothing(index_elements=["team_id"])
    )
    row = db.scalars(
        select(TeamMemory)
        .where(TeamMemory.team_id == team.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return row


def get(db: Session, team_id: int, *, refresh_stale: bool = True) -> TeamMemory | None:
    """The team's memory, refreshed when what it is learnt from changed. None: not a
    volleyball team."""
    team = db.get(WdTeam, team_id)
    if team is None or team.sport not in SPORTS:
        return None
    if not refresh_stale:
        return db.get(TeamMemory, team_id)
    found = db.get(TeamMemory, team_id)
    fp = fingerprint(db, team)
    if found is not None and found.fingerprint == fp:
        return found
    if found is not None and found.fingerprint and found.fingerprint[:32] == fp[:32]:
        # Only answers changed (a coach naming cards one after the other): one refresh a
        # minute is enough.
        age = (datetime.now(UTC) - found.refreshed_at).total_seconds()
        if 0 <= age < MIN_REFRESH_S:
            return found
    return refresh(db, team, fp)


def refresh(db: Session, team: WdTeam, fp: str | None = None) -> TeamMemory:
    """Learn the team's memory anew from its games (module docstring)."""
    fp = fp or fingerprint(db, team)
    mem = _lock(db, team)
    if mem.fingerprint == fp:
        return mem  # another request refreshed it meanwhile
    setters = setters_of(db, team.id)
    videos = team_videos(db, team)
    facts = [game_facts(db, v, setters) for v in videos]
    earlier = [game_facts(db, v, setters) for v in earlier_videos(db, team)]
    _store_players(db, team, facts)
    starts = [s for f in facts for s in f.starts]
    starts += [
        tp.SetStart(s.lineup, s.setter, s.weight * EARLIER_SEASON)
        for f in earlier
        for s in f.starts
    ]
    prior = tp.learn(starts)
    rotations: dict[str, Any] = dict(prior.as_json())
    rotations["liberos"] = sorted({n for f in facts for n in f.liberos})
    rotations["check"] = check_rotations(facts, earlier)
    mem.rotations = rotations
    # The games the memory learnt from: those that named any of our numbers.
    learnt = [f for f in facts if f.named or f.starts]
    mem.games = len(learnt)
    mem.named_videos = sorted(str(f.video_id) for f in learnt)
    mem.models = team_scores(db, team, videos)
    mem.fingerprint = fp
    mem.refreshed_at = datetime.now(UTC)
    db.flush()
    return mem


def _store_players(db: Session, team: WdTeam, facts: Sequence[GameFacts]) -> None:
    roster = roster_by_number(db, team.id)
    rows = {
        r.number: r for r in db.scalars(select(TeamPlayer).where(TeamPlayer.team_id == team.id))
    }
    learned: set[int] = set()
    for f in facts:
        learned |= f.named
    numbers = set(roster) | learned | {n for n, r in rows.items() if r.fixed or r.hidden}
    roster_libero = {
        n
        for n, ms in roster.items()
        for m in ms
        if "libero" in [str(p).lower() for p in (m.positions or [])]
    }
    for n in sorted(numbers):
        row = rows.get(n)
        if row is None:
            row = TeamPlayer(team_id=team.id, lineage_id=team.lineage_id, number=n)
            db.add(row)
        games = [f for f in facts if n in f.named]
        row.games = len(games)
        row.answers = sum(f.answers.get(n, 0) for f in facts)
        row.sets = round(sum(f.sets.get(n, 0.0) for f in facts), 2)
        row.last_on = max((f.on for f in games), default=None)
        row.videos = sorted(str(f.video_id) for f in games)
        row.libero = n in roster_libero or any(n in f.liberos for f in facts)
        member_id, ambiguous = resolve(roster.get(n, []))
        if not row.fixed:
            row.member_id = member_id
            row.ambiguous = ambiguous
        else:
            row.ambiguous = False
        row.source = "coach" if row.fixed else ("roster" if n in roster else "learned")
    for n, row in rows.items():
        if n not in numbers:
            db.delete(row)
    db.flush()


def check_rotations(facts: Sequence[GameFacts], earlier: Sequence[GameFacts]) -> dict[str, Any]:
    """How well the memory predicts the team's own sheets: each game with a typed sheet left
    out in turn, the memory learnt from the others (another video, never the same), our server
    at each of our serves from the memory and the set's setter zone, against the sheet."""
    out = {"sets": 0, "serves": 0, "right": 0, "sure": 0, "sure_right": 0}
    for i, f in enumerate(facts):
        if not f.sheet_sets:
            continue
        others = [s for j, g in enumerate(facts) if j != i for s in g.starts]
        others += [
            tp.SetStart(s.lineup, s.setter, s.weight * EARLIER_SEASON)
            for g in earlier
            for s in g.starts
        ]
        prior = tp.learn(others)
        if prior.empty:
            continue
        for s in f.sheet_sets:
            lineup = s.sheet.lineup.get("us") or ()
            zone = None
            if s.setter is not None:
                idx = tp.setter_index(lineup, s.setter)
                zone = idx + 1 if idx is not None else None
            post = tp.posterior(prior, setter_zone=zone)
            out["sets"] += 1
            for lu in on_court.point_lineups(s.sheet, s.decoded):
                if lu.serving != "us" or lu.server is None:
                    continue
                best = post.best(lu.rotation["us"])
                out["serves"] += 1
                if best is None:
                    continue
                out["right"] += int(best[0] == lu.server)
                if best[1] >= 0.9:
                    out["sure"] += 1
                    out["sure_right"] += int(best[0] == lu.server)
    return out


# --- the models' scores for the team --------------------------------------------------------------


def _mean(values: Iterable[float | None]) -> float | None:
    found = [float(v) for v in values if v is not None]
    return round(sum(found) / len(found), 4) if found else None


def team_scores(db: Session, team: WdTeam, videos: Sequence[Video]) -> dict[str, Any]:
    """The rally finder's and the action finder's held-out scores on the team's reviewed games
    (the newest training; split by match), the per-team variant kept for it, and how far its
    games' players are identified."""
    from wieplay.core.players import PlayerCluster

    ids = {str(v.id) for v in videos}
    lineage = str(team.lineage_id)
    out: dict[str, Any] = {}
    run = db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_rallies")
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()
    if run is not None:
        per = (run.metrics or {}).get("videos") or {}
        held = [
            (e.get("lovo") or {})
            for vid, e in per.items()
            if vid in ids and isinstance(e, dict) and e.get("mode") == "trained_on"
        ]
        team_entry = ((run.metrics or {}).get("teams") or {}).get(lineage) or {}
        out["rallies"] = {
            "trained_at": run.created_at.isoformat() if run.created_at else None,
            "games": len(held),
            "v0": _mean(((h.get("v0") or {}).get("boundary") or {}).get("f1") for h in held),
            "v1": _mean(((h.get("v1") or {}).get("boundary") or {}).get("f1") for h in held),
            "team": team_entry or None,
        }
    run = db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_actions")
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()
    if run is not None:
        per = (run.metrics or {}).get("videos") or {}
        held = [e for vid, e in per.items() if vid in ids and isinstance(e, dict)]
        team_entry = ((run.metrics or {}).get("teams") or {}).get(lineage) or {}
        out["actions"] = {
            "trained_at": run.created_at.isoformat() if run.created_at else None,
            "games": len(held),
            "v0": _mean((h.get("v0") or {}).get("action_f1") for h in held),
            "v1": _mean((h.get("v1") or {}).get("action_f1") for h in held),
            "team": team_entry or None,
        }
    if videos:
        rows = db.execute(
            select(
                PlayerCluster.video_id,
                func.sum(PlayerCluster.detections),
                func.sum(PlayerCluster.detections).filter(
                    (PlayerCluster.assigned_by.is_not(None)) | (PlayerCluster.confidence >= 0.5)
                ),
            )
            .where(
                PlayerCluster.video_id.in_([v.id for v in videos]),
                PlayerCluster.team == "us",
                PlayerCluster.merged_into.is_(None),
                PlayerCluster.not_player.is_(False),
            )
            .group_by(PlayerCluster.video_id)
        ).all()
        shares = [float(s or 0) / float(t) for _, t, s in rows if t]
        out["players"] = {"games": len(shares), "sure_share": _mean(shares)}
    return out


# --- use in a new game ----------------------------------------------------------------------------


def players(db: Session, team_id: int) -> list[TeamPlayer]:
    return list(
        db.scalars(
            select(TeamPlayer).where(TeamPlayer.team_id == team_id).order_by(TeamPlayer.number)
        )
    )


def games_of(row: TeamPlayer, exclude: uuid.UUID | None) -> int:
    """The games that named the number, the video `exclude` left out (its own facts)."""
    return row.games - int(exclude is not None and str(exclude) in (row.videos or []))


def number_prior(
    db: Session,
    team_id: int,
    mem: TeamMemory | None = None,
    exclude: uuid.UUID | None = None,
) -> dict[int, float]:
    """Per number of ours how likely a person of ours on a video wears it (the games that
    named it, smoothed; `exclude`: the video it is for, whose own facts do not count); hidden
    numbers 0. Empty: no memory yet."""
    mem = mem if mem is not None else get(db, team_id)
    if mem is None:
        return {}
    rows = [r for r in players(db, team_id) if not r.hidden]
    named = mem.games - int(exclude is not None and str(exclude) in (mem.named_videos or []))
    named = max(named, max((games_of(r, exclude) for r in rows), default=0))
    prior = tp.number_prior(
        (tp.NumberFacts(r.number, games=games_of(r, exclude)) for r in rows), named
    )
    for r in players(db, team_id):
        if r.hidden:
            prior[r.number] = 0.0
    return prior


def has_rotations(db: Session, team_id: int) -> bool:
    """The team's memory holds usual rotations (as last learnt; cheap, no refresh)."""
    mem = db.get(TeamMemory, team_id)
    return mem is not None and float((mem.rotations or {}).get("sets") or 0) > 0


def order_prior(mem: TeamMemory | None) -> tp.OrderPrior:
    return tp.OrderPrior.from_json(mem.rotations if mem is not None else None)


@dataclass(frozen=True)
class CardMemory:
    """What the memory says about a card's read number: the member wearing it, the games that
    named it, how sure the number is with the reader's confidence (0..1)."""

    number: int
    member_id: int | None
    games: int
    confidence: float
    # Offered as recognised: the team's games named the number and the reading is good
    # (team_prior.recognised); a coach accepts these in one step.
    sure: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "member_id": self.member_id,
            "games": self.games,
            "confidence": round(self.confidence, 3),
            "sure": self.sure,
        }


def card_memory(
    rows: Sequence[TeamPlayer],
    prior: dict[int, float],
    number: int | None,
    read_conf: float | None,
    exclude: uuid.UUID | None = None,
) -> CardMemory | None:
    """A card of ours with a read number: how sure that number is given how often the team
    plays it (`team_prior.reading_confidence`). None: no number, or no memory of it."""
    if number is None or not prior:
        return None
    row = next((r for r in rows if r.number == number), None)
    p = prior.get(number)
    if p is None:
        p = min(prior.values(), default=0.5) / 2  # a number the team never played
    conf = tp.reading_confidence(float(read_conf or 0.0), p)
    games = games_of(row, exclude) if row is not None else 0  # earlier games only
    hidden = row is not None and row.hidden
    return CardMemory(
        number,
        row.member_id if row is not None and not hidden else None,
        games,
        conf,
        sure=games > 0 and not hidden and tp.recognised(read_conf, p),
    )


# --- a coach's fix --------------------------------------------------------------------------------


class FixError(ValueError):
    pass


def fix(
    db: Session,
    team: WdTeam,
    number: int,
    *,
    member_id: int | None,
    hidden: bool,
    actor: int,
) -> TeamPlayer:
    """A coach says who wears a number in this season's team (or that it is none of ours).
    The season's games follow: their roster snapshots, the tags and the cards of ours with
    that number name the member (a correction of a link, PLAN §10.5b)."""
    if not 0 <= number < 100:
        raise FixError("A shirt number is 0-99")
    if member_id is not None and member_id not in {m.id for m in _roster_members(db, team.id)}:
        raise FixError("Pick a player of this team")
    row = db.scalars(
        select(TeamPlayer)
        .where(TeamPlayer.team_id == team.id, TeamPlayer.number == number)
        .with_for_update()
    ).first()
    if row is None:
        row = TeamPlayer(team_id=team.id, lineage_id=team.lineage_id, number=number)
        db.add(row)
        previous, _ = resolve(roster_by_number(db, team.id).get(number, []))
    else:
        previous = row.member_id
    row.fixed = not hidden
    row.hidden = hidden
    row.member_id = None if hidden else member_id
    row.ambiguous = False
    row.source = "coach"
    row.fixed_by = actor
    row.fixed_at = datetime.now(UTC)
    db.flush()
    if not hidden:
        relink_videos(db, team, number, member_id, previous)
    refresh(db, team)
    return row


def unfix(db: Session, team: WdTeam, number: int, actor: int) -> None:
    """Back to what the roster and the games say."""
    row = db.scalars(
        select(TeamPlayer)
        .where(TeamPlayer.team_id == team.id, TeamPlayer.number == number)
        .with_for_update()
    ).first()
    if row is None or not (row.fixed or row.hidden):
        return
    was_fixed, previous = row.fixed, row.member_id
    row.fixed = row.hidden = False
    row.fixed_by = actor
    row.fixed_at = datetime.now(UTC)
    member_id, ambiguous = resolve(roster_by_number(db, team.id).get(number, []))
    row.member_id, row.ambiguous = member_id, ambiguous
    row.source = "roster" if number in roster_by_number(db, team.id) else "learned"
    db.flush()
    if was_fixed:
        relink_videos(db, team, number, member_id, previous)
    refresh(db, team)


def relink_videos(
    db: Session, team: WdTeam, number: int, member_id: int | None, previous: int | None
) -> int:
    """The season's games name `member_id` for our `number`: the roster snapshots (a row added
    where the snapshot lacks the number), our tags and cards with it. Only the link being
    corrected moves (nobody, or `previous`): a game whose snapshot names someone else for the
    number (a guest wearing it that day, a coach's answer for that game) keeps its own.
    Returns the videos changed."""
    replaceable = {None, previous}
    from wieplay.core.players import PlayerCluster

    member = db.get(WdMember, member_id) if member_id is not None else None
    positions = [str(p).lower() for p in (member.positions or [])] if member else []
    changed = 0
    for video in team_videos(db, team):
        snap = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
        if not snap:
            continue  # taken later, with the fix
        found = next((r for r in snap if r.side == "us" and r.number == number), None)
        if found is None:
            if member_id is None:
                continue
            db.add(
                VideoRoster(
                    video_id=video.id,
                    side="us",
                    number=number,
                    member_id=member_id,
                    position=positions[0] if positions else None,
                    is_libero="libero" in positions,
                )
            )
        elif found.member_id == member_id or found.member_id not in replaceable:
            continue
        else:
            found.member_id = member_id
        for ev in db.scalars(
            select(Event).where(
                Event.video_id == video.id,
                Event.side == "us",
                Event.number == number,
                Event.deleted_at.is_(None),
            )
        ):
            if ev.member_id != member_id and ev.member_id in replaceable:
                ev.member_id = member_id
                ev.version = (ev.version or 0) + 1
        for c in db.scalars(
            select(PlayerCluster).where(
                PlayerCluster.video_id == video.id,
                PlayerCluster.team == "us",
                PlayerCluster.number == number,
            )
        ):
            if c.member_id in replaceable:
                c.member_id = member_id
        changed += 1
    db.flush()
    return changed


# --- measured on a game with a typed sheet (the CLI's `team-memory --eval`) ----------------------


def evaluate(db: Session, video: Video, read: Any = None) -> dict[str, Any]:
    """How well the memory names our server per rally of a game whose typed sheet is the truth,
    the sheet hidden: the memory learnt from the team's other games only (another video, never
    this one), the rallies' serving and rotations from the scoring, anchored by nothing, the
    sets' setter zone (as a coach types it in the Sets bar), and the players run's picks at our
    serves (the image attribution: a card the coach named, else the number read on it with the
    team's number prior; `read`: the bucket reader, None skips them). Also the cards the memory
    would offer as recognised, checked against the sheet's server where one served. Reads only;
    the caller rolls back."""
    from wieplay.core import lineups as lu
    from wieplay.core import players as pl
    from wieplay.core.models import Segment

    truth = lu.for_video(db, video)
    out: dict[str, Any] = {"video": str(video.id), "title": video.title}
    if truth is None or not truth.by_rally:
        out["error"] = "no typed sheet lined up with the rallies"
        return out
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    setters = setters_of(db, team.id)
    others = [game_facts(db, v, setters) for v in team_videos(db, team) if v.id != video.id]
    earlier = [game_facts(db, v, setters) for v in earlier_videos(db, team)]
    starts = [s for f in others for s in f.starts]
    starts += [
        tp.SetStart(s.lineup, s.setter, s.weight * EARLIER_SEASON)
        for f in earlier
        for s in f.starts
    ]
    prior = tp.learn(starts)
    named_games = sum(1 for f in others if f.named)
    known: dict[int, int] = {}
    for f in others:
        for n in f.named:
            known[n] = known.get(n, 0) + 1
    numbers = tp.number_prior((tp.NumberFacts(n, games=g) for n, g in known.items()), named_games)
    out["memory"] = {"games": len(others), "named_games": named_games, "sets": prior.sets}

    # Image picks at our serves (as the attribution would make them without the sheet).
    picks: dict[uuid.UUID, tuple[int, float, str]] = {}  # rally → (number, right, kind)
    card_of_rally: dict[uuid.UUID, Any] = {}
    run = pl.latest_run(db, video.id)
    index = pl.load_index(db, run, read) if run is not None and read is not None else None
    if index is not None and run is not None:
        ball = pl.load_ball(db, video.id, read)
        rows = pl.clusters(db, video.id)
        mapping = pl.Mapping.of(rows)
        events = db.scalars(
            select(Event).where(
                Event.video_id == video.id,
                Event.code == "S",
                Event.side == "us",
                Event.deleted_at.is_(None),
                Event.segment_id.is_not(None),
            )
        )
        for ev in events:
            pick, card, _ = pl.attribute_event(ev, index, ball, mapping)
            if pick is None or card is None or card.not_player or ev.segment_id is None:
                continue
            card_of_rally[ev.segment_id] = card
            if card.number is not None and card.assigned_by is not None:
                picks[ev.segment_id] = (int(card.number), lu.NAMED_SEEN, "named")
            elif card.ocr_number is not None and card.kind == pl.PERSON:
                p = numbers.get(int(card.ocr_number), 0.0) if numbers else 0.5
                right = min(lu.READ_SEEN_MAX, tp.reading_confidence(card.ocr_confidence or 0.0, p))
                if right > 0.3:
                    picks[ev.segment_id] = (int(card.ocr_number), right, "read")
    out["picks"] = {
        "serves_with_a_pick": len(picks),
        "pick_right": sum(
            1
            for rid, (n, _, _) in picks.items()
            if (found := truth.of(rid)) is not None and found.serving == "us" and found.server == n
        ),
    }

    periods = list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms)
        )
    )
    starts_ms = [p.start_ms for p in periods]
    segs = list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
                Segment.review.not_in(("rejected", "merged")),
            )
            .order_by(Segment.start_ms, Segment.id)
        )
    )
    by_period: dict[uuid.UUID, list[Segment]] = {}
    for seg in segs:
        k = bisect.bisect_right(starts_ms, seg.start_ms) - 1
        if k >= 0:
            by_period.setdefault(periods[k].id, []).append(seg)
    modes = ("memory only", "setter zone", "picks", "picks, no memory", "setter zone + picks")
    scores = {m: {"serves": 0, "right": 0, "sure": 0, "sure_right": 0} for m in modes}
    for period in periods:
        rallies = by_period.get(period.id, [])
        rots = lu.rally_rotations(rallies)
        seen = [
            tp.Seen(r, picks[seg.id][0], picks[seg.id][1])
            for seg, r in zip(rallies, rots, strict=True)
            if seg.id in picks and lu.serving_of(seg) == "us"
        ]
        zone = period.start_rotation if period.start_rotation in (1, 2, 3, 4, 5, 6) else None
        posts = {
            "memory only": tp.posterior(prior),
            "setter zone": tp.posterior(prior, setter_zone=zone) if zone else None,
            "picks": tp.posterior(prior, seen),
            "picks, no memory": tp.posterior(tp.OrderPrior(), seen),
            "setter zone + picks": tp.posterior(prior, seen, setter_zone=zone) if zone else None,
        }
        for seg, r in zip(rallies, rots, strict=True):
            found = truth.of(seg.id)
            if found is None or found.serving != "us" or found.server is None:
                continue
            for mode, post in posts.items():
                s = scores[mode]
                s["serves"] += 1
                best = post.best(r) if post is not None else None
                if best is None:
                    continue
                s["right"] += int(best[0] == found.server)
                if best[1] >= lu.MEMORY_APPLY:
                    s["sure"] += 1
                    s["sure_right"] += int(best[0] == found.server)
    out["servers"] = scores

    # The cards the memory would offer as recognised, against the sheet's server where the
    # card was WiePlay's pick at one of our serves.
    rows_all = pl.clusters(db, video.id) if run is not None else []
    rec = [
        c
        for c in rows_all
        if c.team == "us"
        and c.kind == pl.PERSON
        and not c.not_player
        and c.ocr_number is not None
        and (c.confidence or 0) < pl.UNCERTAIN
        and known.get(int(c.ocr_number))
        and tp.recognised(c.ocr_confidence, numbers.get(int(c.ocr_number)))
    ]
    ours = [c for c in rows_all if c.team == "us" and not c.not_player and c.merged_into is None]
    total = sum(c.detections for c in ours) or 1
    checked = right = 0
    rec_ids = {c.id for c in rec}
    for rid, card in card_of_rally.items():
        found = truth.of(rid)
        if card.id in rec_ids and found is not None and found.serving == "us":
            checked += 1
            right += int(found.server == card.ocr_number)
    out["recognised"] = {
        "cards": len(rec),
        "footage_share": round(sum(c.detections for c in rec) / total, 4),
        "sure_share_before": round(
            sum(c.detections for c in ours if (c.confidence or 0) >= pl.UNCERTAIN) / total, 4
        ),
        "checked_at_serves": checked,
        "right_at_serves": right,
    }
    return out

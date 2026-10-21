"""Identifying the players with the coach (PLAN §10.5, the owner's "if not sure let me try to
identify players (and teams), like in Hudl/Balltime", 19.10.2026), API side.

    the players job's cards ─▶ a queue of the cards WiePlay is not sure of, the most footage
        named per answer first (`gain`) ─▶ the coach answers from pictures (the video zoomed
        onto the card at several moments): a number, not a player, wrong team, "same as"
        another card, mixed (split), skip; for a pieces card, the pieces of one person ticked
        and named ─▶ each answer is a fact: the cards linked anew around it (`relink`), so
        neighbouring cards join a named one and the queue shrinks

and, while watching, a tap on a person in the paused video names the card of that track.

Nothing new is detected or stored: the answers change the cards (`player_cluster`), as the
gallery's buttons do, and the linking uses what the cards already hold (their time on court,
their team, the number read on them) and the official scoresheet's six on court (§10.3a). No
faces: the coach's eyes do the matching, WiePlay links the answer to the tracks and cards; all
within one video (PLAN §5.7).

`relink` joins an unsure card to a card the coach named when the reader read the named card's
number on it (`ocr_number`, at least MIN_OCR) and the two are never seen at the same time.
"Who is missing" (a card or a piece without a number given to the one named teammate missing
while it is seen) was measured on the Legends game and is not used: of the pieces it moved,
those whose number shows were mostly someone else (the libero, a teammate the coach had not
named yet, the other team's player). Since players v3 a card is one person; the pieces
without a number that look alike are a *pieces* card (`kind`), several teammates as a rule: a
"pieces" answer names the ticked ones at once (they move to the card with that number, or a
new one), and the rest stays for the next answer.
"""

from __future__ import annotations

import bisect
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core import lineups
from wieplay.core.models import Period, Segment, Video
from wieplay.core.players import (
    PERSON,
    PIECES,
    UNCERTAIN,
    GalleryError,
    Index,
    PlayerCluster,
    merge,
    roster_numbers,
    split,
)

MIN_OCR = 0.5
# A pieces card shows its biggest pieces (the web shows the same): one answer names one
# person among them, about half of their footage (the Legends game, 19.10.2026).
PIECES_SHOWN = 24
PIECES_GAIN = 0.5
MOMENTS = 6
CLIP_MS = 3000  # the loop around a moment
FIELDS = (
    "team", "number", "member_id", "not_player", "confidence", "merged_into", "tracks", "crops",
    "detections", "source", "assigned_by", "assigned_at", "kind",
)  # fmt: skip


# --- what is sure -------------------------------------------------------------------------------


def named(c: PlayerCluster) -> bool:
    """The coach answered this card: a number, or not a player."""
    return c.assigned_by is not None and (c.number is not None or c.not_player)


def sure(c: PlayerCluster) -> bool:
    """Named by the coach, or one whole person by the job's confidence."""
    return named(c) or (c.confidence is not None and c.confidence >= UNCERTAIN)


def live(rows: Iterable[PlayerCluster]) -> list[PlayerCluster]:
    return [c for c in rows if c.merged_into is None and c.tracks]


def players_of(rows: Iterable[PlayerCluster]) -> list[PlayerCluster]:
    """The cards that are (or may be) players: not merged away, not "not a player"."""
    return [c for c in live(rows) if not c.not_player]


def sure_share(rows: Iterable[PlayerCluster]) -> float:
    """The players' footage on sure cards (0..1)."""
    cards = players_of(rows)
    total = sum(c.detections for c in cards)
    return round(sum(c.detections for c in cards if sure(c)) / total, 4) if total else 0.0


def gain(c: PlayerCluster) -> float:
    """What an answer about the card is expected to name (detections): a person card all of
    its footage; a pieces card one of its people among the pieces shown (PIECES_SHOWN, the
    biggest), about half of them on the Legends game (PLAN §10.5)."""
    if c.kind == PIECES:
        shown = sorted((int(t["n"]) for t in c.tracks), reverse=True)[:PIECES_SHOWN]
        return PIECES_GAIN * sum(shown)
    return float(c.detections)


def queue(rows: Iterable[PlayerCluster]) -> list[PlayerCluster]:
    """The cards to check: not sure, not answered, the most footage named per answer first
    (`gain`)."""
    out = [c for c in players_of(rows) if not sure(c)]
    out.sort(key=lambda c: (-gain(c), c.key))
    return out


# --- time on court ------------------------------------------------------------------------------


def intervals(c: PlayerCluster) -> list[tuple[int, int]]:
    return sorted((int(t["t0"]), int(t["t1"])) for t in c.tracks)


def overlap(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]) -> bool:
    """Seen at the same time (two people)."""
    starts = [s for s, _ in b]
    for s0, e0 in a:
        k = bisect.bisect_right(starts, e0)
        for j in range(max(0, k - 50), k):
            s1, e1 = b[j]
            if s0 <= e1 and s1 <= e0:
                return True
    return False


def gap_ms(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]) -> int | None:
    """The shortest time between two sets of intervals; None when they overlap."""
    if overlap(a, b):
        return None
    best: int | None = None
    starts = [s for s, _ in b]
    for s0, e0 in a:
        k = bisect.bisect_left(starts, s0)
        for j in (k - 1, k):
            if 0 <= j < len(b):
                s1, e1 = b[j]
                g = s1 - e0 if s1 > e0 else s0 - e1
                best = g if best is None else min(best, g)
    return best


# --- answers as facts ---------------------------------------------------------------------------


def relink(
    db: Session, video: Video, rows: list[PlayerCluster], actor: int
) -> list[dict[str, Any]]:
    """Unsure cards joined to the cards the coach named (module docstring). Returns what was
    joined ({card, into, why})."""
    out: list[dict[str, Any]] = []
    anchors = {(c.team, c.number): c for c in players_of(rows) if named(c) and c.number is not None}
    if not anchors:
        return out
    spans = {c.id: intervals(c) for c in live(rows)}

    def join(u: PlayerCluster, target: PlayerCluster, why: str) -> None:
        merge(db, target, [u], actor)
        spans[target.id] = intervals(target)
        out.append({"card": str(u.id), "into": str(target.id), "why": why})

    # The number read on it says who it is. ("Who is missing" was measured and is not used:
    # with only some people named, the one named teammate missing is often not the one.)
    for u in sorted(queue(rows), key=lambda c: -c.detections):
        if u.ocr_number is None or (u.ocr_confidence or 0) < MIN_OCR:
            continue
        found = anchors.get((u.team, u.ocr_number))
        if found is not None and not overlap(spans[u.id], spans[found.id]):
            join(u, found, "number")
    return out


def snapshot(rows: Iterable[PlayerCluster]) -> list[dict[str, Any]]:
    """The cards as they are, for undoing an answer (`restore`)."""
    out = []
    for c in rows:
        item: dict[str, Any] = {"id": str(c.id)}
        for name in FIELDS:
            value = getattr(c, name)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            item[name] = value
        out.append(item)
    return out


def restore(db: Session, video: Video, items: Sequence[dict[str, Any]]) -> int:
    """Undo: the cards back as `snapshot` had them. Only this video's cards, only tracks this
    video's cards hold (or held)."""
    rows = {
        str(c.id): c
        for c in db.scalars(select(PlayerCluster).where(PlayerCluster.video_id == video.id))
    }
    known = {int(t["id"]) for c in rows.values() for t in c.tracks}
    for item in items:
        if str(item.get("id")) not in rows:
            raise GalleryError("not a card of this video")
        for t in item.get("tracks") or []:
            if not isinstance(t, dict) or "id" not in t:
                raise GalleryError("a card's tracks are objects with an id")
    moved = {int(t["id"]) for item in items for t in item.get("tracks") or []}
    if not moved <= known:  # tracks only move between this video's cards
        raise GalleryError("tracks of another video")
    for item in items:
        c = rows[str(item["id"])]
        for name in FIELDS:
            if name not in item:
                continue
            value = item[name]
            if name == "merged_into":
                value = uuid.UUID(value) if value else None
            elif name == "assigned_at":
                value = datetime.fromisoformat(value) if value else None
            elif name == "kind" and value not in (PERSON, PIECES):
                raise GalleryError("a card is a person or pieces")
            setattr(c, name, value)
    db.flush()
    return len(items)


def set_team(db: Session, video: Video, c: PlayerCluster, team: str, actor: int) -> None:
    """This card only is the other team's (`swap_teams` turns the whole video round)."""
    if team not in ("us", "them"):
        raise GalleryError("a team is us or them")
    if c.team == team:
        return
    c.team = team
    roster = roster_numbers(db, video)
    c.member_id = roster.get(c.number) if team == "us" and c.number is not None else None
    c.confidence = None  # the coach's now
    c.assigned_by = actor
    c.assigned_at = datetime.now(UTC)


def name_pieces(
    db: Session,
    video: Video,
    rows: list[PlayerCluster],
    card: PlayerCluster,
    pieces: Sequence[int],
    number: int | None,
    not_player: bool,
    actor: int,
    team: str | None = None,
) -> PlayerCluster:
    """Some pieces of a card are one person (a number, or not a player): they move to the card
    of that number (`holder`: the coach's, else the one the job is sure of; never seen at the
    same time), else to a new card named so. All of a card's pieces: the card itself is
    named. `team`: the person plays for the other team than the card (a kit WiePlay took for
    the other's): the pieces move there first."""
    from wieplay.core.players import assign

    wanted = {int(p) for p in pieces}
    if not wanted or not wanted <= {int(t["id"]) for t in card.tracks}:
        raise GalleryError("pick pieces of this card")
    if wanted == {int(t["id"]) for t in card.tracks}:
        part = card
    else:
        part = split(db, card, sorted(wanted), actor)
        rows.append(part)
    if team is not None and team != part.team:
        set_team(db, video, part, team, actor)
    target = None
    if number is not None and not not_player:
        target = holder(rows, part, number)
    if target is not None:
        merge(db, target, [part], actor)
        return target
    assign(db, video, part, number=number, not_player=not_player, actor=actor)
    return part


def holder(rows: Sequence[PlayerCluster], part: PlayerCluster, number: int) -> PlayerCluster | None:
    """The card of `number` on `part`'s team that `part` can join (never seen at the same
    time): the one the coach named so, else the one the job is sure of with that number
    read."""
    mine = intervals(part)
    cands = [
        c for c in players_of(rows)
        if c.id != part.id and c.team == part.team and not overlap(intervals(c), mine)
    ]  # fmt: skip
    for c in cands:
        if named(c) and c.number == number:
            return c
    for c in cands:
        if not named(c) and sure(c) and c.number is None and c.ocr_number == number:
            return c
    return None


def tap(
    db: Session,
    video: Video,
    rows: list[PlayerCluster],
    track_id: int,
    number: int | None,
    not_player: bool,
    actor: int,
) -> PlayerCluster:
    """A person tapped in the paused video named: the card of that track gets the answer; a
    track on a card the coach named otherwise is split off into a card of its own first."""
    from wieplay.core.players import assign

    card = next((c for c in live(rows) if any(int(t["id"]) == track_id for t in c.tracks)), None)
    if card is None:
        raise GalleryError("nobody WiePlay followed is there")
    differs = named(card) and (card.not_player != not_player or card.number != number)
    if differs and len(card.tracks) > 1:
        card = split(db, card, [track_id], actor)
        rows.append(card)
    assign(db, video, card, number=number, not_player=not_player, actor=actor)
    return card


# --- what the coach sees --------------------------------------------------------------------------


def rally_spans(db: Session, video: Video) -> list[tuple[int, int, uuid.UUID]]:
    return [
        (int(s), int(e), rid)
        for s, e, rid in db.execute(
            select(Segment.start_ms, Segment.end_ms, Segment.id)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
            )
            .order_by(Segment.start_ms)
        )
    ]


def on_court_numbers(
    c: PlayerCluster,
    rows: Sequence[PlayerCluster],
    sheet: lineups.VideoLineups | None,
    spans: Sequence[tuple[int, int, uuid.UUID]],
    prior: lineups.PriorLineups | None = None,
) -> list[int]:
    """The numbers the sheet has on court for the card's team in the rallies it is seen in
    (where it has none, our six the team's memory expects, weighed by how sure it is), the
    most often first, those of named cards seen at the same time last; then its liberos."""
    if (sheet is None and prior is None) or c.team not in ("us", "them"):
        return []
    starts = [s for s, _, _ in spans]
    mine = intervals(c)
    weights: dict[int, float] = defaultdict(float)
    for t0, t1 in mine:
        k = bisect.bisect_right(starts, t1) - 1
        while k >= 0 and spans[k][1] >= t0:
            lu = sheet.of(spans[k][2]) if sheet is not None else None
            if lu is not None:
                for n in lu.numbers(c.team):  # type: ignore[arg-type]
                    weights[n] += 1
            elif prior is not None and c.team == "us" and spans[k][2] in prior.six:
                six, p = prior.six[spans[k][2]]
                for m in six:
                    if m is not None:
                        weights[m] += p
            k -= 1
    taken = {
        o.number
        for o in players_of(rows)
        if o.id != c.id and o.team == c.team and named(o) and o.number is not None
        and overlap(mine, intervals(o))
    }  # fmt: skip
    ordered = sorted(weights, key=lambda n: (n in taken, -weights[n], n))
    for n in sorted(sheet.liberos.get(c.team, ()) if sheet is not None else ()):
        if n not in ordered:
            ordered.append(n)
    return ordered


def same_as(
    c: PlayerCluster, rows: Sequence[PlayerCluster], likely: Iterable[int]
) -> list[PlayerCluster]:
    """Cards that may be the same person: of its team, never seen at the same time, with its
    read number or one of the likely numbers; named and bigger first."""
    wanted = {n for n in likely}
    if c.ocr_number is not None:
        wanted.add(c.ocr_number)
    mine = intervals(c)
    out = []
    for o in players_of(rows):
        if o.id == c.id or o.team != c.team:
            continue
        number = o.number if o.number is not None else o.ocr_number
        if number is None or number not in wanted or overlap(mine, intervals(o)):
            continue
        out.append(o)
    out.sort(key=lambda o: (not named(o), o.number != c.ocr_number, -o.detections))
    return out[:6]


def moments(index: Index | None, c: PlayerCluster, n: int = MOMENTS) -> list[dict[str, Any]]:
    """Up to `n` moments of the card, from its longest tracks: the frame where the person is
    tallest, and the boxes CLIP_MS around it (the web zooms the video onto them)."""
    if index is None:
        return []
    out: list[dict[str, Any]] = []
    tracks = sorted(c.tracks, key=lambda t: -int(t["n"]))[: n * 2]
    for t in tracks:
        tid = int(t["id"])
        lo = bisect.bisect_left(index.t_ms, int(t["t0"]))
        hi = bisect.bisect_right(index.t_ms, int(t["t1"]))
        best: tuple[float, int, tuple[float, float, float, float]] | None = None
        step = max(1, (hi - lo) // 60)
        for f in range(lo, hi, step):
            for track, box in index.boxes(f):
                if track == tid and (best is None or box[3] - box[1] > best[0]):
                    best = (box[3] - box[1], f, box)
        if best is None:
            continue
        _, f, box = best
        t_ms = int(index.t_ms[f])
        clip: list[list[float]] = []
        a = bisect.bisect_left(index.t_ms, t_ms - CLIP_MS // 2)
        b = bisect.bisect_right(index.t_ms, t_ms + CLIP_MS // 2)
        for g in range(a, b):
            for track, bx in index.boxes(g):
                if track == tid:
                    clip.append([int(index.t_ms[g]), *(round(v, 4) for v in bx)])
        out.append({"t_ms": t_ms, "track": tid, "box": [round(v, 4) for v in box], "clip": clip})
        if len(out) >= n:
            break
    out.sort(key=lambda m: m["t_ms"])
    return out


def frame_boxes(
    index: Index | None, rows: Sequence[PlayerCluster], t_ms: int
) -> tuple[int | None, list[dict[str, Any]]]:
    """The people WiePlay followed at the analysed frame nearest `t_ms` (within half a
    second), with their cards."""
    if index is None:
        return None, []
    frame = index.frame_near(t_ms, within=500)
    if frame is None:
        return None, []
    by_track = {int(t["id"]): c for c in live(rows) for t in c.tracks}
    out = []
    for track, box in index.boxes(frame):
        c = by_track.get(track)
        if c is None:
            continue
        out.append(
            {
                "track": track,
                "box": [round(v, 4) for v in box],
                "card": str(c.id),
                "key": c.key,
                "team": c.team,
                "number": c.number,
                "ocr_number": c.ocr_number,
                "not_player": c.not_player,
                "sure": sure(c),
            }
        )
    return int(index.t_ms[frame]), out


# --- which team is ours ---------------------------------------------------------------------------


def first_set(db: Session, video: Video) -> Period | None:
    return db.scalars(
        select(Period)
        .where(Period.video_id == video.id, Period.deleted_at.is_(None))
        .order_by(Period.start_ms, Period.no)
        .limit(1)
    ).first()


def teams_question(
    db: Session, video: Video, rows: Sequence[PlayerCluster], uncertain: bool
) -> dict[str, Any] | None:
    """When the teams are uncertain (no court calibration: the sides come from foot heights,
    and the kits may look alike) and nobody said which side we started on: the biggest cards
    seen on each side of the net in the first set, for the coach to pick ours."""
    period = first_set(db, video)
    if not uncertain or period is None or period.our_court_side_at_start in ("near", "far"):
        return None
    end = period.end_ms if period.end_ms is not None else 10**12
    sides: dict[str, Counter[uuid.UUID]] = {"near": Counter(), "far": Counter()}
    for c in players_of(rows):
        for t in c.tracks:
            if int(t["t1"]) >= period.start_ms and int(t["t0"]) <= end:
                sides["near" if t.get("side") == "near" else "far"][c.id] += int(t["n"])
    by_id = {c.id: c for c in rows}
    out: dict[str, Any] = {"set": period.no}
    for side, counts in sides.items():
        cards = [by_id[k] for k, _ in counts.most_common(8)]
        out[side] = [{"card": str(c.id), "crops": c.crops[:2], "team": c.team} for c in cards]
        teams = Counter(by_id[k].team for k in counts.elements())
        out[f"{side}_team"] = teams.most_common(1)[0][0] if teams else None
    if not out["near"] or not out["far"]:
        return None
    return out


def answer_teams(
    db: Session, video: Video, rows: Sequence[PlayerCluster], near_is_ours: bool, actor: int
) -> bool:
    """The coach says which side of the net was ours in the first set: the cards are turned
    round when they say the other (`swap_teams`), and the first set's side is kept as the
    coach's (the next scoring and players run start from it). Returns whether it turned."""
    from wieplay.core.players import swap_teams

    question = teams_question(db, video, rows, uncertain=True)
    period = first_set(db, video)
    turned = False
    if question is not None and question.get("near_team") is not None:
        ours_near = question["near_team"] == "us"
        if ours_near != near_is_ours:
            swap_teams(db, video, actor)
            turned = True
    if period is not None and period.our_court_side_at_start is None:
        period.our_court_side_at_start = "near" if near_is_ours else "far"
    db.flush()
    return turned

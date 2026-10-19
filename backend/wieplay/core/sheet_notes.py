"""The scoresheet's timeouts, substitutions and sanctions in the video, as score checks (PLAN
§10.3a). Plain Python on the scoring's output; the API reads it, nothing is stored.

Each of them is written on the sheet with the score at that moment, and the decoded points are a
path through the scores (wieplay/ml/sheet.py), so each one falls after a known number of points.
The scoring lined the sheet's points up with the rallies (`segment.sheet_point`), so each falls
in the break after the rally of its last point. There:

- **the score line** (what the video shows after that rally) should be the sheet's score, and
  so should **the club scoreboard's log** (its score after that rally);
- **a timeout** needs a break of at least TIMEOUT_MIN_S (30 s and the walk back);
- **a penalty** (P, DP) gives the opponent a point **without a rally**: a rally lined up with
  that point is flagged.

A disagreement is flagged in the review ("Timeout (them) at 14:12: the score line says 13:12
here"). The cards (sanctions) are markers on the timeline at their break; a sanction names no
person, only the team (a sanctioned person's exceptional substitution is on the sheet as a
substitution). Using these as alignment evidence was measured (19.10.2026: a cost for a timeout
or substitution placed in a short gap) and gave nothing: the two real sheets lined up as well
or slightly worse, so they only check.
"""

from __future__ import annotations

import bisect
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from wieplay.ml import sheet as sh

Kind = Literal["timeout", "sub", "sub_back", "sanction"]
Issue = Literal["score_line", "log", "short_break", "penalty_rally", "off_path", "no_rally"]

# A timeout lasts 30 s; with the walk to the bench and back the break is longer.
TIMEOUT_MIN_S = 30.0
POINT_KINDS = sh.POINT_KINDS


@dataclass(frozen=True)
class Note:
    """One of the sheet's notes: what it is, whose, the score written with it (us, them) and
    after how many of the set's points it falls (None: that score is not on the path)."""

    set_no: int
    kind: Kind
    index: int  # in the set's timeouts, substitutions or sanctions
    side: sh.Side
    score: tuple[int, int]
    at_points: int | None
    sanction: str | None = None
    number_in: int | None = None  # substitutions: who came in (by number)
    position: int | None = None  # substitutions: the starting position (1-6)


@dataclass(frozen=True)
class RallyFacts:
    """What the checks need of a rally: its time, the score line after it and the log's."""

    id: uuid.UUID | str
    start_ms: int
    end_ms: int
    line: tuple[int, int] | None
    log: tuple[int, int] | None


@dataclass
class Placed:
    note: Note
    rally: uuid.UUID | str | None = None  # the rally after which it falls
    start_ms: int | None = None  # the break in the video
    end_ms: int | None = None
    line: tuple[int, int] | None = None
    log: tuple[int, int] | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def gap_s(self) -> float | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return (self.end_ms - self.start_ms) / 1000

    def as_json(self) -> dict[str, Any]:
        n = self.note
        return {
            "set": n.set_no,
            "kind": n.kind,
            "index": n.index,
            "side": n.side,
            "score": list(n.score),
            "at_points": n.at_points,
            "sanction": n.sanction,
            "number_in": n.number_in,
            "position": n.position,
            "rally": str(self.rally) if self.rally is not None else None,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "line": list(self.line) if self.line else None,
            "log": list(self.log) if self.log else None,
            "issues": list(self.issues),
        }


def notes_of(s: sh.SheetSet, d: sh.Decoded) -> list[Note]:
    """A decoded set's timeouts, substitutions (and returns) and sanctions, in sheet order."""
    out: list[Note] = []
    for k, (t, at) in enumerate(zip(s.timeouts, d.timeout_at, strict=False)):
        out.append(Note(s.no, "timeout", k, t.side, t.score(), at))
    for k, (u, (came, went)) in enumerate(zip(s.subs, d.sub_at, strict=False)):
        for kind, score, at in (("sub", u.at, came), ("sub_back", u.back, went)):
            if score is None:
                continue
            us_them = score if u.side == "us" else (score[1], score[0])
            out.append(Note(s.no, kind, k, u.side, us_them, at, None, u.number_in, u.position))  # type: ignore[arg-type]
    for k, (x, at) in enumerate(zip(s.sanctions, d.sanction_at, strict=False)):
        out.append(Note(s.no, "sanction", k, x.side, x.score(), at, x.kind))
    return out


def place(
    notes: Sequence[Note],
    point_rally: Mapping[int, uuid.UUID | str],
    rallies: Sequence[RallyFacts],
    offset: int = 0,
) -> list[Placed]:
    """Each note in the break after the rally of its last point (the latest point before it
    that has a rally), with the checks. `point_rally`: the set's sheet points (0-based, in the
    decoded set) lined up with a rally; `rallies`: the video's rallies in time order;
    `offset`: points of the set played before the video joined it."""
    by_id = {r.id: r for r in rallies}
    starts = [r.start_ms for r in rallies]
    out: list[Placed] = []
    for n in notes:
        p = Placed(n)
        out.append(p)
        if n.at_points is None:
            p.issues.append("off_path")
            continue
        # A penalty point (the next one, the opponent's) has no rally by the rules.
        if n.kind == "sanction" and n.sanction in POINT_KINDS and n.at_points in point_rally:
            p.issues.append("penalty_rally")
        before = next(
            (point_rally[k] for k in range(n.at_points - 1, offset - 1, -1) if k in point_rally),
            None,
        )
        if before is None or before not in by_id:
            if n.at_points > offset:
                p.issues.append("no_rally")
            continue
        r = by_id[before]
        p.rally = r.id
        p.start_ms = r.end_ms
        k = bisect.bisect_right(starts, r.end_ms)
        while k < len(rallies) and rallies[k].start_ms <= r.end_ms:
            k += 1
        p.end_ms = rallies[k].start_ms if k < len(rallies) else None
        # The last point before the note had a rally of its own: the score after that rally
        # is the note's. (After a missed point the rally before is earlier: no score check.)
        own = point_rally.get(n.at_points - 1) == r.id
        p.line, p.log = r.line, r.log
        if own and r.line is not None and r.line != n.score:
            p.issues.append("score_line")
        if own and r.log is not None and r.log != n.score:
            p.issues.append("log")
        gap = p.gap_s
        if n.kind == "timeout" and gap is not None and gap < TIMEOUT_MIN_S:
            p.issues.append("short_break")
    return out


def summary(placed: Sequence[Placed]) -> dict[str, Any]:
    """Counts for the scoring's metrics: notes placed, checked against the score line and the
    log, and how many disagree."""
    out: dict[str, Any] = {"notes": len(placed)}
    for kind in ("timeout", "sub", "sub_back", "sanction"):
        mine = [p for p in placed if p.note.kind == kind]
        if not mine:
            continue
        out[kind] = {
            "notes": len(mine),
            "placed": sum(1 for p in mine if p.start_ms is not None),
            "line_checked": sum(1 for p in mine if p.line is not None and p.start_ms is not None),
            "line_differs": sum(1 for p in mine if "score_line" in p.issues),
            "log_checked": sum(1 for p in mine if p.log is not None and p.start_ms is not None),
            "log_differs": sum(1 for p in mine if "log" in p.issues),
            "flagged": sum(1 for p in mine if p.issues),
        }
    return out

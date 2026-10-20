"""The game recap (PLAN §7.7): the best plays of a match cut into a 2-3 minute MP4, a title card
with the teams, the date and the result per set, a small score bug on every clip ("Set 2 ·
22:20"). An export of kind "recap" on the export pipeline: the worker renders it with ffmpeg,
watermarked like every export. No commentary, no generated text: everything on screen comes
from the facts below.

Which plays (`select`):

- **Facts first**: tags that stand (a person's, and WiePlay's that nobody rejected; with "sure
  only" without those still to check) and rallies that stand. Kills, aces and block points score
  (ours more than theirs), long rallies, and the rallies that ended a set or the match.
- **No tags yet**: the rally finder's rallies (also those still to check), the longest ones
  and the set and match points from the score line.
- The export says how many of its plays rest on something still to check (`to_check`).
- Best first until about 2.5 minutes, then in the order they were played; the match point when
  there is one. A clip runs from just before the serve to just after the point.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core import guests, names, tagging
from wieplay.core.config import get_settings
from wieplay.core.models import Event, Period, Segment, Video, WdGame, WdTeam

Lang = Literal["de", "en"]
Kind = Literal["ace", "kill", "block", "long", "set_point", "match_point"]
Ending = Literal["set_point", "match_point"]

TARGET_MS = 150_000  # the plays, without the title card
MAX_MS = 180_000
MIN_FACTS_MS = 60_000  # fewer plays than this from tags: topped up with long rallies
TITLE_MS = 4_000
PRE_MS = 1_500  # before the serve
POST_MS = 2_000  # after the point
TAG_PRE_MS, TAG_POST_MS = 4_000, 2_500  # a tag without a rally around it
LONG_MS = 15_000  # a long rally
SUSPECT_MS = 28_000  # a proposal nobody checked that is longer is likely two rallies in one
MAX_CLIP_MS = 30_000
FADE_S = 0.25

WEIGHTS: dict[str, float] = {"ace": 3.5, "kill": 3.0, "block": 3.5}
THEIRS = 0.6  # their best plays count, less than ours
SET_POINT, MATCH_POINT = 3.0, 4.5
WON_BY_US = 0.5

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "eyebrow": "GAME RECAP",
        "set": "Set {no}",
        "ace": "Ace",
        "kill": "Kill",
        "block": "Block point",
        "long": "Long rally",
        "set_point": "Set point",
        "match_point": "Match point",
        "footer": "Best plays · WiePlay · {club}",
        "title": "{title} (recap)",
        "them": "Opponent",
    },
    "de": {
        "eyebrow": "SPIELZUSAMMENFASSUNG",
        "set": "Satz {no}",
        "ace": "Ass",
        "kill": "Angriffspunkt",
        "block": "Blockpunkt",
        "long": "Langer Ballwechsel",
        "set_point": "Satzball",
        "match_point": "Matchball",
        "footer": "Die besten Szenen · WiePlay · {club}",
        "title": "{title} (Zusammenfassung)",
        "them": "Gegner",
    },
}
WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
}  # fmt: skip


@dataclass
class Play:
    start_ms: int  # the clip
    end_ms: int
    point_ms: int  # when the point was decided (the bug shows the new score from here)
    kinds: list[Kind] = field(default_factory=list)
    score: float = 0.0
    rally_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    set_no: int | None = None
    before: tuple[int, int] | None = None  # us, them
    after: tuple[int, int] | None = None
    number: int | None = None  # the shirt number of the tag that made it (ours)
    side: str | None = None  # whose play it was
    ends: Ending | None = None  # the last rally of a set or of the match (maybe a guess)

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class Selection:
    plays: list[Play]
    source: Literal["facts", "rallies"]
    # Plays that rest on something still to check: their rally, or the tag that made them.
    to_check: int = 0
    sure_only: bool = False

    @property
    def total_ms(self) -> int:
        return sum(p.length_ms for p in self.plays)


# --- the plays ----------------------------------------------------------------------------------


def _rallies(db: Session, video: Video, sure_only: bool = False) -> list[Segment]:
    query = select(Segment).where(
        Segment.video_id == video.id,
        Segment.kind == "rally",
        Segment.deleted_at.is_(None),
        tagging.stands(sure_only),
    )
    return list(db.scalars(query.order_by(Segment.start_ms)))


def _events(db: Session, video: Video, sure_only: bool = False) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.video_id == video.id,
                Event.deleted_at.is_(None),
                tagging.event_stands(sure_only),
                Event.code.in_(("S", "A", "B")),
            )
            .order_by(Event.t_ms)
        )
    )


def _rally_to_check(r: Segment) -> bool:
    """tagging.rally_to_check, on a row."""
    if r.review in tagging.GONE:
        return False
    return r.review == "check" or (
        r.score_sure is False and r.won_by is None and r.review not in tagging.HUMAN_REVIEWS
    )


def _periods(db: Session, video: Video) -> list[Period]:
    return list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms, Period.no)
        )
    )


def _point_kind(event: Event) -> Kind | None:
    """A tag that won the point for the tagged side: an ace, a kill, a block point."""
    if (event.attributes or {}).get("grade") != "#":
        return None
    return {"S": "ace", "A": "kill", "B": "block"}.get(event.code)  # type: ignore[return-value]


@dataclass(frozen=True)
class RallyFacts:
    """What the recap may write about a rally: only facts (the score bug never guesses)."""

    set_no: int | None  # the set, when it is a fact
    before: tuple[int, int] | None  # the score before it (us, them), when a fact
    after: tuple[int, int] | None  # the score after it, when a fact
    ended: Ending | None  # the last rally of its set or of the match (also a guess)
    ended_fact: bool  # ... and that is a fact


def _period_fact(period: Period, official: list[tuple[int, int]] | None) -> bool:
    """A set that is a fact: a coach's (made, accepted or corrected), one WiePlay is sure of,
    or one whose final score is Wiedisync's official score of that set."""
    if period.source != "model" or period.review in ("confirmed", "corrected", "auto"):
        return True
    if not official or not 1 <= period.no <= len(official):
        return False
    return (period.final_us, period.final_them) == official[period.no - 1]


def _board_check(db: Session, video: Video) -> Any:
    """A test: does the scoreboard show this score right after a rally (sure, no conflict)?"""
    from wieplay.core import scoreboard as sboard
    from wieplay.core import scoring
    from wieplay.ml import board as bd

    tl = sboard.timeline(db, video.id)
    run = scoring.latest_run(db, video.id)
    told = (run.metrics or {}).get("board") if run is not None else None
    if tl is None or not told:
        return None
    left_is_us = bool(told.get("left_is_us", True))

    def shows(after: tuple[int, int], end_ms: int, next_mid: int | None) -> bool:
        if next_mid is not None:
            found = tl.at(next_mid)
        else:
            later = [s for s in tl.within(end_ms, end_ms + 60_000) if s.first_ms > end_ms]
            found = later[0] if later else None
        if found is None or found.confidence < bd.MIN_CONF:
            return False
        seen = (found.left, found.right) if left_is_us else (found.right, found.left)
        return seen == after

    return shows


def _scores(db: Session, video: Video, rallies: list[Segment]) -> dict[uuid.UUID, RallyFacts]:
    """Per rally the facts the bug may show: its set (a coach's set, or Wiedisync's official
    score at its end), the score before and after it when every point up to it is a fact (the
    coach's winners, the rallies marked done without a check, or the scoreboard showing that
    score, sure and without a check, or the joint decode sure of the rally), and whether it
    ended its set or the match."""
    from wieplay.core import scoring

    official = scoring.official_sets(db, video)
    board = _board_check(db, video)
    reviewed = video.rallies_reviewed_at is not None
    out: dict[uuid.UUID, RallyFacts] = {}
    by_period: dict[uuid.UUID, list[Segment]] = {}
    for r in rallies:
        if r.period_id is not None:
            by_period.setdefault(r.period_id, []).append(r)
    played = [p for p in _periods(db, video) if by_period.get(p.id)]
    last_period = played[-1].id if played else None
    for period in played:
        rs = by_period[period.id]
        set_fact = _period_fact(period, official)
        before: tuple[int, int] | None = (period.start_us, period.start_them) if set_fact else None
        for i, r in enumerate(rs):
            after = (
                (r.score_us_after, r.score_them_after)
                if r.score_us_after is not None and r.score_them_after is not None
                else None
            )
            fact = set_fact and after is not None and before is not None
            if fact and after is not None:
                nxt = rs[i + 1] if i + 1 < len(rs) else None
                fact = (
                    r.score_source == "tags"
                    or bool(r.score_sure)
                    or (reviewed and not r.score_conflict)
                    or (
                        board is not None
                        and not r.score_conflict
                        and board(
                            after, r.end_ms, (nxt.start_ms + nxt.end_ms) // 2 if nxt else None
                        )
                    )
                )
            ended: Ending | None = None
            if i == len(rs) - 1 and after is not None:
                ended = "match_point" if period.id == last_period else "set_point"
            out[r.id] = RallyFacts(
                period.no if set_fact else None,
                before,
                after if fact else None,
                ended,
                ended is not None and fact,
            )
            before = after if fact else None
    return out


def select_plays(db: Session, video: Video, sure_only: bool = False) -> Selection:
    """The plays of a recap, in the order played (the module's docstring); `sure_only`:
    without what is still to check."""
    events = _events(db, video, sure_only)
    points = [e for e in events if _point_kind(e)]
    every = _rallies(db, video, sure_only)
    # Facts: points tagged, the rallies marked done, or rallies a person made or accepted.
    human = any(r.source != "model" or r.review in ("confirmed", "corrected") for r in every)
    facts = bool(points) or video.rallies_reviewed_at is not None or human
    rallies = every
    scores = _scores(db, video, rallies)
    plays: dict[uuid.UUID, Play] = {}
    for r in rallies:
        known = scores.get(r.id) or RallyFacts(None, None, None, None, False)
        play = Play(
            start_ms=max(0, r.start_ms - PRE_MS),
            end_ms=r.end_ms + POST_MS,
            point_ms=r.end_ms,
            rally_id=r.id,
            set_no=known.set_no,
            before=known.before,
            after=known.after,
        )
        ended = known.ended
        length = r.end_ms - r.start_ms
        unchecked = r.source == "model" and r.review == "check"
        if length >= LONG_MS and not (unchecked and length > SUSPECT_MS):
            play.kinds.append("long")
            play.score += min(3.0, max(0.0, (length - 10_000) / 3_000))
            if unchecked and length > 20_000:
                # The longer an unchecked proposal, the likelier it holds dead time too.
                play.score -= (length - 20_000) / 5_000
        if ended is not None:
            # Chosen like one either way; called one only when it is a fact.
            if known.ended_fact:
                play.kinds.append(ended)
            play.score += MATCH_POINT if ended == "match_point" else SET_POINT
            play.ends = ended
        won = r.won_by or r.model_won_by
        if won == "us" and play.score > 0:
            play.score += WON_BY_US
        plays[r.id] = play
    loose: list[Play] = []
    for e in points:
        kind = _point_kind(e)
        assert kind is not None
        weight = WEIGHTS[kind] * (1.0 if e.side == "us" else THEIRS)
        rally = next((r for r in rallies if r.start_ms - 1000 <= e.t_ms <= r.end_ms + 1000), None)
        target = plays.get(rally.id) if rally is not None else None
        if target is None:
            target = Play(
                start_ms=max(0, e.t_ms - TAG_PRE_MS),
                end_ms=e.t_ms + TAG_POST_MS,
                point_ms=e.t_ms + 300,
                event_id=e.id,
            )
            loose.append(target)
        target.kinds.insert(0, kind)
        target.score += weight + (WON_BY_US if e.side == "us" else 0.0)
        target.side = e.side
        if e.side == "us" and e.number is not None:
            target.number = e.number
        target.event_id = target.event_id or e.id
    candidates = [p for p in [*plays.values(), *loose] if p.score > 0]
    chosen = _choose(candidates)
    if facts and sum(p.length_ms for p in chosen) < MIN_FACTS_MS:
        # Few tags yet: the longest rallies fill the recap up.
        rest = sorted(
            (p for p in plays.values() if p not in chosen and p.length_ms <= SUSPECT_MS),
            key=lambda p: p.end_ms - p.start_ms,
            reverse=True,
        )
        for p in rest:
            if sum(c.length_ms for c in chosen) + p.length_ms > TARGET_MS:
                break
            if "long" not in p.kinds and p.length_ms >= LONG_MS:
                p.kinds.append("long")
            chosen.append(p)
    chosen.sort(key=lambda p: p.start_ms)
    _trim_overlaps(chosen)
    unsure_rallies = {r.id for r in rallies if _rally_to_check(r)}
    unsure_events = {e.id for e in events if e.source == "model" and e.review == "check"}
    to_check = sum(1 for p in chosen if p.rally_id in unsure_rallies or p.event_id in unsure_events)
    return Selection(chosen, "facts" if facts else "rallies", to_check, sure_only)


def _choose(candidates: list[Play]) -> list[Play]:
    """Best first until the target length; the match point always (it ends the recap)."""
    ordered = sorted(candidates, key=lambda p: (-p.score, p.start_ms))
    chosen: list[Play] = []
    total = 0
    for p in ordered:
        length = min(p.length_ms, MAX_CLIP_MS)
        if total + length > TARGET_MS and p.ends != "match_point":
            continue
        if total + length > MAX_MS:
            continue
        if length < p.length_ms:
            p.start_ms = p.end_ms - MAX_CLIP_MS  # a very long rally: its end
        chosen.append(p)
        total += length
    return chosen


def _trim_overlaps(plays: list[Play]) -> None:
    for prev, p in pairwise(plays):
        if p.start_ms < prev.end_ms:
            p.start_ms = prev.end_ms
    plays[:] = [p for p in plays if p.end_ms - p.start_ms >= 1000]


# --- what is written on it ----------------------------------------------------------------------


@dataclass
class Card:
    eyebrow: str
    matchup: str
    result: str | None  # sets won, e.g. "3:1"
    sets: str | None  # "25:20 · 23:25 · 25:18 · 25:22"
    when: str  # the date (and the hall)
    footer: str

    def lines(self) -> list[str]:
        return [
            x
            for x in (self.eyebrow, self.matchup, self.result, self.sets, self.when, self.footer)
            if x
        ]


def long_date(day: date, lang: Lang) -> str:
    weekday = WEEKDAYS[lang][day.weekday()]
    month = MONTHS[lang][day.month - 1]
    return (
        f"{weekday}, {day.day}. {month} {day.year}"
        if lang == "de"
        else (f"{weekday}, {day.day} {month} {day.year}")
    )


@dataclass
class Sides:
    """Which team is written first: home when the game is known (the official order), else
    us; `left_is_us` turns (us, them) into that order. The short names head the score bug
    ("KSCW H3 - Volero"), in the same order."""

    left: str
    right: str
    left_is_us: bool
    left_short: str = ""
    right_short: str = ""
    club: str = ""  # our club, for the footer (a guest club's own name)

    def order(self, score: tuple[int, int]) -> tuple[int, int]:
        return score if self.left_is_us else (score[1], score[0])

    @property
    def teams(self) -> str:
        left, right = self.left_short or self.left, self.right_short or self.right
        return f"{left} \N{EN DASH} {right}" if left and right else ""


def sides(db: Session, video: Video, lang: Lang) -> tuple[Sides, WdGame | None]:
    """The teams' names (wieplay/core/names.py): the official ones on the title card when the
    game is known (unless a coach set one for the video), the short ones on the bug."""
    game = db.get(WdGame, video.game_id) if video.game_id is not None else None
    team = db.get(WdTeam, video.team_id)
    settings = get_settings()
    club_short, club_name = (
        guests.club_names(team, settings.club_short_name, settings.club_name)
        if team
        else (settings.club_short_name, settings.club_name)
    )
    found = names.for_video(db, video, team.name if team else "", club_short, club_name)
    them_short = found.them or TEXT[lang]["them"]
    them_full = found.them_set or found.them_full or TEXT[lang]["them"]
    if game is not None and game.home_name and game.away_name:
        home = game.our_side != "away"
        us_full = found.us_set or (game.home_name if home else game.away_name)
        if not found.them_set:
            them_full = game.away_name if home else game.home_name
        left, right = (us_full, them_full) if home else (them_full, us_full)
        short = (found.us, them_short) if home else (them_short, found.us)
        return Sides(left, right, home, *short, club=club_name), game
    return Sides(found.us_full, them_full, True, found.us, them_short, club=club_name), game


def set_scores(
    db: Session, video: Video, game: WdGame | None, side: Sides
) -> list[tuple[int, int]]:
    """Per set (in the written order): Wiedisync's official scores, else those of the sets a
    coach stands behind; a set only the scoring proposed is no result to write on the card."""
    if game is not None and game.sets:
        out = []
        for s in game.sets:
            if isinstance(s, list | tuple) and len(s) == 2:
                home, away = int(s[0]), int(s[1])
                out.append((home, away))
        if out:
            return out
    finals = [
        (p.final_us, p.final_them)
        for p in _periods(db, video)
        if p.final_us is not None
        and p.final_them is not None
        and (p.source != "model" or p.review in ("confirmed", "corrected"))
    ]
    return [side.order((int(a), int(b))) for a, b in finals]


def card(db: Session, video: Video, lang: Lang) -> tuple[Card, Sides]:
    side, game = sides(db, video, lang)
    sets = set_scores(db, video, game, side)
    won = (sum(1 for a, b in sets if a > b), sum(1 for a, b in sets if b > a))
    hall = game.hall_name if game is not None else None
    when = long_date(game.date if game is not None and game.date else video.recorded_on, lang)
    return (
        Card(
            eyebrow=TEXT[lang]["eyebrow"],
            matchup=f"{side.left} \N{EN DASH} {side.right}",
            result=f"{won[0]}:{won[1]}" if sets else None,
            sets=" · ".join(f"{a}:{b}" for a, b in sets) if sets else None,
            when=f"{when} · {hall}" if hall else when,
            footer=TEXT[lang]["footer"].format(club=side.club or get_settings().club_name),
        ),
        side,
    )


def label(play: Play, lang: Lang) -> str | None:
    """What the clip shows: the play (with our player's number), the set or match point."""
    words = TEXT[lang]
    parts: list[str] = []
    end = next((k for k in play.kinds if k in ("match_point", "set_point")), None)
    if end:
        parts.append(words[end])
    best = next((k for k in play.kinds if k in ("ace", "kill", "block")), None)
    if best:
        parts.append(f"{words[best]} #{play.number}" if play.number is not None else words[best])
    elif "long" in play.kinds and not end:
        parts.append(words["long"])
    return " · ".join(parts) or None


def bug(play: Play, side: Sides, lang: Lang) -> dict[str, Any] | None:
    """The score bug of a clip, facts only: the set when it is a fact, the score before the
    point and after it when they are facts (see `_scores`), what the play was. Nothing to
    show: no bug."""
    head = TEXT[lang]["set"].format(no=play.set_no) if play.set_no is not None else ""
    # The score only with the point's result as a fact (then the score before it is one too).
    after = side.order(play.after) if play.after else None
    before = side.order(play.before) if play.before and after else None
    out = {
        "set": head,
        "before": f"{before[0]}:{before[1]}" if before else "",
        "after": f"{after[0]}:{after[1]}" if after else "",
        "point_ms": play.point_ms,
        "label": label(play, lang) or "",
    }
    if not (out["set"] or out["before"] or out["after"] or out["label"]):
        return None
    if out["after"] and side.teams:
        out["teams"] = side.teams  # whose score is whose, in the score's order
    return out


def title_of(video: Video, lang: Lang) -> str:
    return TEXT[lang]["title"].format(title=video.title)[:200]

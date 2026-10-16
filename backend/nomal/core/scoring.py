"""Automatic scoring of a video, API side (PLAN §10.3, §10.1): who served each rally, who won
it, the score, the sets and the gaps between rallies.

Computed here, in the API, because it needs the database (the official set scores from the
Wiedisync mirror, the coach's serves, winners and sets) and is cheap (about a second per
match, plain Python: nomal/ml/scoring.py and nomal/ml/gaps.py). The worker only provides two
small series per video (nomal/ml/tracks.py: the serve evidence and the activity), stored in
`analysis_track` with each analysis or training.

Runs after the analysis and after every training (new rallies, new serve evidence), when a
coach marks the rallies done, changes a set (who served first, our side, its bounds) or calls
a serve in review, and on request (POST /api/videos/{id}/scoring).

What it writes, always next to the coach's work and never in its place (PLAN §10, §11):

- per rally: the model's server and winner (`model_serving`, `model_won_by`), how sure
  (`score_confidence`), "check" (`score_conflict`, `score_note`), the serve side
  (`serving_court`, `serve_confidence`); `tagging.rescore` then fills the score line with
  the model's winners where the coach has none (`score_source` "model");
- sets (`period`, source "model", review "unreviewed") when the video has no coach's sets;
  the model's first server and our side per set (`model_first_serve`, `model_court_side`);
- gap labels (segments warmup / set_break / timeout / stoppage / other_dead, source
  "model", review "unreviewed"), replaced on every run unless a coach answered them;
- a `model_run` (kind "score") with the metrics: per set the official score, the score line's
  and the evidence's final; flagged rallies; gaps; on the coach's rallies, how often the
  model (scored blind to them) gets the serve side and the winner right.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nomal import __version__
from nomal.core import tagging
from nomal.core.models import AnalysisTrack, Event, ModelRun, Period, Segment, Video, WdGame
from nomal.ml import gaps
from nomal.ml import scoring as sc
from nomal.ml import tracks as tr

SPORTS = frozenset({"volleyball"})
MODEL_NAME = "nomal-scoring"
GAP_KINDS = gaps.KINDS
SET_LEAD_MS = 3000  # a proposed set starts this long before its first rally
SET_TAIL_MS = 3000
OVERLAP = 0.5  # a proposal covering this much of something a coach rejected is dropped
MIN_FACTS = 5  # rallies with a coach's serve or winner before "vs coach" is measured
TRACK_KINDS = ("serve", "activity")


def code_version() -> str:
    return (os.environ.get("NOMAL_GIT_SHA") or __version__)[:64]


# --- inputs ----------------------------------------------------------------------------------


def official_sets(db: Session, video: Video) -> list[tuple[int, int]] | None:
    """The federation's set scores as [us, them], when the video's game is known."""
    if video.game_id is None:
        return None
    game = db.get(WdGame, video.game_id)
    if game is None or not game.sets:
        return None
    out = []
    for s in game.sets:
        if not isinstance(s, list | tuple) or len(s) != 2:
            return None
        home, away = int(s[0]), int(s[1])
        out.append((away, home) if game.our_side == "away" else (home, away))
    return out


def latest_track(db: Session, video_id: uuid.UUID, kind: str) -> AnalysisTrack | None:
    return db.scalars(
        select(AnalysisTrack)
        .where(AnalysisTrack.video_id == video_id, AnalysisTrack.kind == kind)
        .order_by(AnalysisTrack.created_at.desc())
        .limit(1)
    ).first()


def _serve_series(track: AnalysisTrack | None) -> tr.Series | None:
    if track is None:
        return None
    try:
        return tr.series(track.channels, "p_near", track.step_ms)
    except ValueError:
        return None


def _activity(track: AnalysisTrack | None) -> gaps.Activity:
    if track is None:
        return gaps.Activity()
    try:
        return gaps.Activity(
            *(tr.series(track.channels, name, track.step_ms) for name in tr.ACTIVITY_CHANNELS)
        )
    except ValueError:
        return gaps.Activity()


def store_tracks(
    db: Session, video_id: uuid.UUID, run: ModelRun, found: dict[str, dict[str, Any]]
) -> list[str]:
    """Keep the series a worker sent (validated by the caller). Returns the kinds stored."""
    stored = []
    for kind in TRACK_KINDS:
        entry = found.get(kind)
        if not entry:
            continue
        db.add(
            AnalysisTrack(
                video_id=video_id,
                model_run_id=run.id,
                kind=kind,
                step_ms=int(entry["step_ms"]),
                finder=entry.get("finder"),
                source_run_id=entry.get("model_run_id"),
                channels=dict(entry["channels"]),
            )
        )
        stored.append(kind)
    db.flush()
    return stored


def _serve_calls(db: Session, video: Video, rallies: Sequence[Segment]) -> list[str | None]:
    """A coach's serve per rally: a serve tag in it (like the rescore), else the serve called
    in review."""
    serves = list(
        db.execute(
            select(Event.t_ms, Event.side)
            .where(Event.video_id == video.id, Event.code == "S", Event.deleted_at.is_(None))
            .order_by(Event.t_ms)
        ).all()
    )
    out: list[str | None] = []
    k = 0
    for rally in rallies:
        while k < len(serves) and serves[k][0] < rally.start_ms - tagging.SERVE_SLACK_MS:
            k += 1
        tag = serves[k][1] if k < len(serves) and serves[k][0] <= rally.end_ms else None
        out.append(str(tag) if tag else rally.serving_manual)
    return out


def _side(value: str | None) -> sc.Side | None:
    if value == "us":
        return "us"
    return "them" if value == "them" else None


def _court(value: str | None) -> sc.Court | None:
    if value == "near":
        return "near"
    return "far" if value == "far" else None


def _period_of(periods: Sequence[Period], start_ms: int) -> Period | None:
    found = None
    for period in periods:
        if period.start_ms <= start_ms and (period.end_ms is None or start_ms < period.end_ms):
            found = period
    return found


def _coach_period(p: Period) -> bool:
    """A set a person stands behind: made by hand, a proposal accepted or corrected, or one
    the coach filled in (who served first, our side, the starting rotation or score)."""
    return (
        p.source != "model"
        or p.review in ("confirmed", "corrected")
        or any(getattr(p, f) is not None for f in tagging.PERIOD_COACH_FIELDS)
        or bool(p.start_us or p.start_them)
    )


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Share of `a` covered by `b`."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return max(0, inter) / max(1, a[1] - a[0])


# --- sets ---------------------------------------------------------------------------------------


def _propose_periods(
    db: Session,
    video: Video,
    run: ModelRun,
    rallies: Sequence[Segment],
    official: list[tuple[int, int]] | None,
) -> None:
    """Replace the model's untouched sets with new ones from the long breaks and the official
    number of points per set; a set a coach rejected is not proposed again."""
    db.execute(delete(Period).where(Period.video_id == video.id, tagging.untouched_model_sets()))
    rejected = [
        (p.start_ms, p.end_ms or p.start_ms + 1)
        for p in db.scalars(
            select(Period).where(
                Period.video_id == video.id,
                Period.source == "model",
                Period.deleted_at.is_not(None),
            )
        )
    ]
    if not rallies:
        return
    spans = sc.propose_sets(
        [(r.start_ms, r.end_ms) for r in rallies],
        [a + b for a, b in official] if official else None,
        sure=[r.source != "model" or r.review in ("confirmed", "corrected") for r in rallies],
    )
    for no, span in enumerate(spans, start=1):
        prev_end = rallies[span.first - 1].end_ms if span.first > 0 else 0
        nxt = spans[no].first if no < len(spans) else None
        start = max(prev_end, rallies[span.first].start_ms - SET_LEAD_MS)
        end = rallies[span.last].end_ms + SET_TAIL_MS
        if nxt is not None:
            end = min(end, rallies[nxt].start_ms - 1)
        if any(_overlap((start, end), r) >= OVERLAP for r in rejected):
            continue
        db.add(
            Period(
                id=uuid.uuid4(),
                video_id=video.id,
                no=no,
                start_ms=start,
                end_ms=end,
                source="model",
                review="unreviewed",
                confidence=span.confidence,
                model_run_id=run.id,
                version=1,
            )
        )
    db.flush()


# --- the run ------------------------------------------------------------------------------------


def _obs(
    rally: Segment,
    serve: tr.Series | None,
    call: str | None,
    gap: gaps.GapEvidence | None,
) -> sc.RallyObs:
    open_proposal = rally.source == "model" and rally.review == "unreviewed"
    return sc.RallyObs(
        key=str(rally.id),
        start_ms=rally.start_ms,
        end_ms=rally.end_ms,
        p_near=serve.near(rally.start_ms, 2) if serve is not None else None,
        serve_fact=_side(call),
        won_fact=_side(rally.won_by),
        proposal=(rally.confidence if rally.confidence is not None else 0.5)
        if open_proposal
        else None,
        gap_ball_s=gap.ball_s if gap else 0.0,
        gap_whistles=gap.whistles if gap else 0,
    )


def _set_in(
    period: Period, obs: list[sc.RallyObs], official: list[tuple[int, int]] | None
) -> sc.SetIn:
    deciding = period.no == 5 or period.side_switch_at_score is not None
    rules = (
        sc.SetRules(target=15, switch_at=period.side_switch_at_score or 8)
        if deciding
        else sc.SetRules()
    )
    off = official[period.no - 1] if official and 1 <= period.no <= len(official) else None
    if off is not None and (off[0] < period.start_us or off[1] < period.start_them):
        off = None
    return sc.SetIn(
        no=period.no,
        rallies=obs,
        rules=rules,
        start_us=period.start_us,
        start_them=period.start_them,
        official=off,
        first_serve=_side(period.first_serve),
        our_side=_court(period.our_court_side_at_start),
        deciding=deciding,
    )


def score_video(db: Session, video: Video, *, reason: str) -> ModelRun | None:
    """Score the video now (module docstring). None for a sport without scoring."""
    if video.sport not in SPORTS:
        return None
    started = time.perf_counter()
    db.flush()
    serve_track = latest_track(db, video.id, "serve")
    act_track = latest_track(db, video.id, "activity")
    serve = _serve_series(serve_track)
    act = _activity(act_track)
    official = official_sets(db, video)
    run = ModelRun(
        id=uuid.uuid4(),
        kind="score",
        video_id=video.id,
        model_name=MODEL_NAME,
        model_version=code_version(),
        params={
            "reason": reason,
            "serve_finder": serve_track.finder if serve_track else None,
            "serve_track_run": str(serve_track.model_run_id) if serve_track else None,
            "serve_model_run": str(serve_track.source_run_id)
            if serve_track and serve_track.source_run_id
            else None,
            "activity": act_track is not None,
            "official_sets": [list(s) for s in official] if official else None,
        },
    )
    db.add(run)
    db.flush()

    rallies = list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
            )
            .order_by(Segment.start_ms, Segment.id)
        )
    )
    periods = _live_periods(db, video)
    if not any(_coach_period(p) for p in periods):
        _propose_periods(db, video, run, rallies, official)
        periods = _live_periods(db, video)

    calls = _serve_calls(db, video, rallies)
    by_period: dict[uuid.UUID, list[int]] = {}
    for i, rally in enumerate(rallies):
        period = _period_of(periods, rally.start_ms)
        if period is not None:
            by_period.setdefault(period.id, []).append(i)
    obs: list[sc.RallyObs] = []
    for i, rally in enumerate(rallies):
        period = _period_of(periods, rally.start_ms)
        gap = None
        if period is not None and i > 0 and by_period[period.id][0] != i:
            gap = gaps.evidence(act, rallies[i - 1].end_ms, rally.start_ms)
        obs.append(_obs(rally, serve, calls[i], gap))

    scored = [p for p in periods if by_period.get(p.id)]
    set_ins = [_set_in(p, [obs[i] for i in by_period[p.id]], official) for p in scored]
    match = sc.score_match(set_ins) if set_ins else sc.MatchOut([], 1.0)

    # The rallies: clear, then write what the model found.
    by_key = {str(r.id): r for r in rallies}
    for rally, o in zip(rallies, obs, strict=True):
        rally.model_serving = rally.model_won_by = rally.score_note = None
        rally.model_missed_us = rally.model_missed_them = None
        rally.score_confidence = None
        rally.score_conflict = False
        if o.p_near is None:
            rally.serving_court = rally.serve_confidence = None
        else:
            rally.serving_court = "near" if o.p_near >= 0.5 else "far"
            rally.serve_confidence = round(max(o.p_near, 1 - o.p_near), 3)
    for so in match.sets:
        for out in so.rallies:
            rally = by_key[out.key]
            rally.model_serving = out.serving
            rally.model_won_by = out.won_by
            rally.score_confidence = out.confidence
            rally.score_note = out.note
            rally.score_conflict = out.note is not None
            if out.missed != (0, 0):
                rally.model_missed_us, rally.model_missed_them = out.missed
    for period in periods:
        period.model_missed_us = period.model_missed_them = None
    for period, so in zip(scored, match.sets, strict=True):
        period.model_first_serve = so.first_serve
        period.model_court_side = so.our_side
        if so.missed_after != (0, 0):
            period.model_missed_us, period.model_missed_them = so.missed_after
        if period.source == "model":
            period.model_run_id = run.id

    gap_counts = _label_gaps(db, video, run, rallies, scored, by_period, act)
    db.flush()
    tagging.rescore(db, video)

    sets_out: list[dict[str, Any]] = []
    for period, st, so in zip(scored, set_ins, match.sets, strict=True):
        evidence = sc.evidence_final(st, so.our_side, so.first_serve) if st.rallies else None
        sets_out.append(
            {
                "no": period.no,
                "start_ms": period.start_ms,
                "end_ms": period.end_ms,
                "source": "coach" if _coach_period(period) else "model",
                "official": list(st.official) if st.official else None,
                "model": list(so.final) if so.rallies else None,
                "evidence": list(evidence) if evidence else None,
                "matches_official": (evidence == st.official) if st.official else None,
                "rallies": sum(1 for r in so.rallies if r.point),
                "flagged": so.flagged,
                "missed": so.missed,
                "extra": so.extra,
                "first_serve": so.first_serve,
                "our_side_at_start": so.our_side,
                "feasible": so.feasible,
            }
        )
    flagged = sum(s["flagged"] for s in sets_out)
    in_sets = sum(len(so.rallies) for so in match.sets)
    first_serve_ms = _first_serve_ms(db, video)
    run.metrics = {
        "serve_finder": serve_track.finder if serve_track else None,
        "sides_confidence": match.sides_confidence if set_ins else None,
        "rallies": in_sets,
        "flagged": flagged,
        "flagged_share": round(flagged / in_sets, 4) if in_sets else None,
        "sets": sets_out,
        "sets_official": sum(1 for s in sets_out if s["official"]),
        "sets_matching_official": sum(1 for s in sets_out if s["matches_official"]),
        "gaps": gap_counts,
        "first_serve_ms": first_serve_ms,
        "vs_coach": _vs_coach(set_ins, match),
        "notes": dict(Counter(r.note for so in match.sets for r in so.rallies if r.note)),
    }
    run.timing = {"wall_s": round(time.perf_counter() - started, 3), "rallies": len(rallies)}
    return run


def _live_periods(db: Session, video: Video) -> list[Period]:
    return list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms, Period.no)
        )
    )


def _first_serve_ms(db: Session, video: Video) -> int | None:
    """The match's first serve: the start of set 1's first rally (else of its set), else the
    end of the warm-up."""
    periods = _live_periods(db, video)
    first = next((p for p in periods if p.no == 1), periods[0] if periods else None)
    if first is not None:
        rally = db.scalars(
            select(Segment.start_ms)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
                Segment.period_id == first.id,
            )
            .order_by(Segment.start_ms)
            .limit(1)
        ).first()
        return int(rally) if rally is not None else first.start_ms
    warmup = db.scalars(
        select(Segment.end_ms)
        .where(
            Segment.video_id == video.id,
            Segment.kind == "warmup",
            Segment.deleted_at.is_(None),
        )
        .order_by(Segment.end_ms.desc())
        .limit(1)
    ).first()
    return int(warmup) if warmup is not None else None


def first_serve_ms(db: Session, video: Video) -> int | None:
    return _first_serve_ms(db, video) if video.sport in SPORTS else None


def _label_gaps(
    db: Session,
    video: Video,
    run: ModelRun,
    rallies: Sequence[Segment],
    periods: Sequence[Period],
    by_period: dict[uuid.UUID, list[int]],
    act: gaps.Activity,
) -> dict[str, int]:
    """Replace the model's untouched gap labels with new ones; never over a coach's own dead
    time label or one a coach rejected."""
    db.execute(
        delete(Segment).where(
            Segment.video_id == video.id,
            Segment.kind.in_(GAP_KINDS),
            Segment.source == "model",
            Segment.review == "unreviewed",
            Segment.updated_by.is_(None),
            Segment.deleted_at.is_(None),
        )
    )
    others = db.execute(
        select(
            Segment.start_ms, Segment.end_ms, Segment.source, Segment.review, Segment.deleted_at
        ).where(Segment.video_id == video.id, Segment.kind.in_(GAP_KINDS))
    ).all()
    blocked = [
        (int(s), int(e))
        for s, e, source, review, deleted in others
        if deleted is None or (source == "model" and review == "rejected")
    ]
    sets = [
        gaps.SetRange(rallies[by_period[p.id][0]].start_ms, rallies[by_period[p.id][-1]].end_ms)
        for p in periods
    ]
    labels = gaps.classify(
        [(r.start_ms, r.end_ms) for r in rallies], sets, video.duration_ms or 0, act
    )
    counts: dict[str, int] = dict.fromkeys(GAP_KINDS, 0)
    for label in labels:
        span = (label.start_ms, label.end_ms)
        if span[1] <= span[0] or any(_overlap(span, b) >= OVERLAP for b in blocked):
            continue
        db.add(
            Segment(
                id=uuid.uuid4(),
                video_id=video.id,
                kind=label.kind,
                start_ms=span[0],
                end_ms=span[1],
                source="model",
                model_run_id=run.id,
                confidence=label.confidence,
                review="unreviewed",
                version=1,
            )
        )
        counts[label.kind] += 1
    return counts


def _vs_coach(set_ins: Sequence[sc.SetIn], match: sc.MatchOut) -> dict[str, Any] | None:
    """The model scored blind to the coach's serves and winners (the sets and their sides as
    they are), against them: the serve side and the winner per rally."""
    facts = sum(1 for st in set_ins for r in st.rallies if r.serve_fact or r.won_fact)
    if facts < MIN_FACTS:
        return None
    blind_sets = [
        replace(st, rallies=[replace(r, serve_fact=None, won_fact=None) for r in st.rallies])
        for st in set_ins
    ]
    blind = sc.score_match(blind_sets)
    serve_right = serve_known = won_right = won_known = 0
    for st, so, chosen in zip(set_ins, blind.sets, match.sets, strict=True):
        outs = {o.key: o for o in so.rallies}
        sides = {o.key: o.our_side for o in chosen.rallies}
        for r in st.rallies:
            o = outs.get(r.key)
            if r.serve_fact and r.p_near is not None and r.key in sides:
                near_is_us = sides[r.key] == "near"
                expected_near = (r.serve_fact == "us") == near_is_us
                serve_known += 1
                serve_right += int((r.p_near >= 0.5) == expected_near)
            if r.won_fact and o is not None and o.point:
                won_known += 1
                won_right += int(o.won_by == r.won_fact)
    return {
        "serving_side_accuracy": round(serve_right / serve_known, 4) if serve_known else None,
        "serving_known": serve_known,
        "winner_accuracy": round(won_right / won_known, 4) if won_known else None,
        "winners_known": won_known,
    }


def latest_run(db: Session, video_id: uuid.UUID) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "score", ModelRun.video_id == video_id)
        .order_by(ModelRun.created_at.desc(), ModelRun.id.desc())
        .limit(1)
    ).first()


# --- labels for the serve model --------------------------------------------------------------


def serve_labels(db: Session, video: Video) -> list[list[int]]:
    """For the serve model's training (a reviewed game): [start ms, 1 near / 0 far] for every
    rally a person stands behind whose server is a fact, with our side from the coach's set
    (else the model's guess for it), switched in the deciding set."""
    periods = {p.id: p for p in _live_periods(db, video)}
    rallies = list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
                tagging.stands(),
            )
            .order_by(Segment.start_ms)
        )
    )
    out: list[list[int]] = []
    before: dict[uuid.UUID, tuple[int, int]] = {}
    for rally in rallies:
        period = periods.get(rally.period_id) if rally.period_id else None
        if period is None:
            continue
        score = before.get(period.id, (period.start_us, period.start_them))
        if rally.score_us_after is not None and rally.score_them_after is not None:
            before[period.id] = (rally.score_us_after, rally.score_them_after)
        side = _court(period.our_court_side_at_start) or _court(period.model_court_side)
        if rally.serving not in ("us", "them") or side is None:
            continue
        switch = period.side_switch_at_score or (8 if period.no == 5 else None)
        if switch is not None and max(score) >= switch:
            side = sc.flip(side)
        out.append([rally.start_ms, int((rally.serving == "us") == (side == "near"))])
    return out

"""Automatic scoring of a video, API side (PLAN §10.3, §10.1): who served each rally, who won
it, the score, the sets and the gaps between rallies.

Computed here, in the API, because it needs the database (the official set scores from the
Wiedisync mirror, the coach's serves, winners and sets) and is cheap (about a second per
match, plain Python: wieplay/ml/scoring.py and wieplay/ml/gaps.py). The worker only provides two
small series per video (wieplay/ml/tracks.py: the serve evidence and the activity), stored in
`analysis_track` with each analysis or training.

Runs after the analysis and after every training (new rallies, new serve evidence), when a
coach marks the rallies done, changes a set (who served first, our side, its bounds), calls
a serve in review or changes who won a rally (a fact the model keeps to), and on request
(POST /api/videos/{id}/scoring).

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
  model (scored blind to them) gets the serve side and the winner right;
- per rally, what the club scoreboard's log says (`log_won_by`, `log_score_*`, `log_at_ms`;
  PLAN §10.10, wieplay/core/scorelog.py), in the sets where the log ends on the official score;
- with the official scoresheet (PLAN §10.3a, wieplay/core/scoresheet.py): a consistent set as
  strong evidence, its points lined up with the rallies where the decode keeps to it
  (`sheet_point`, `sheet_won_by`, `sheet_serving`; where the board log and the video outvote it,
  the sheet page offers the boxes to change), its first server and lineups on the set where
  empty, its timeouts as `timeout` breaks with their team (source "import"), a doubtful set's
  winners as a hint, a draft read from the photo as soft evidence (its boxes the decode is sure
  of are resolved in the draft);
- per rally whether the joint decode is sure (`score_sure`) and why the coach is asked
  (`score_reasons`, the first one also as `score_note`; PLAN §10.1 "Needs you").
"""

from __future__ import annotations

import os
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from wieplay import __version__
from wieplay.core import sheet_notes, tagging
from wieplay.core.models import AnalysisTrack, Event, ModelRun, Period, Segment, Video, WdGame
from wieplay.ml import gaps
from wieplay.ml import scoring as sc
from wieplay.ml import tracks as tr

SPORTS = frozenset({"volleyball"})
MODEL_NAME = "wieplay-scoring"
# The model's gap labels, and a coach's substitution (the review's "Sub"): every label of dead
# time keeps the model's labels off its span.
GAP_KINDS = (*gaps.KINDS, "substitution")
SET_LEAD_MS = 3000  # a proposed set starts this long before its first rally
SET_TAIL_MS = 3000
OVERLAP = 0.5  # a proposal covering this much of something a coach rejected is dropped
MIN_FACTS = 5  # rallies with a coach's serve or winner before "vs coach" is measured
TRACK_KINDS = ("serve", "activity")


def code_version() -> str:
    return (os.environ.get("WIEPLAY_GIT_SHA") or __version__)[:64]


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


def rescore_new_results(db: Session) -> list[uuid.UUID]:
    """Videos scored before their game's official result came through Wiedisync (or scored
    with one that was corrected since): scored again with it, in the hourly sync. A video
    never scored is left to its analysis. Commits per video."""
    done = []
    videos = db.scalars(
        select(Video).where(
            Video.game_id.is_not(None),
            Video.deleted_at.is_(None),
            Video.status == "ready",
            Video.sport.in_(SPORTS),
        )
    ).all()
    for video in videos:
        last = db.scalars(
            select(ModelRun)
            .where(ModelRun.video_id == video.id, ModelRun.kind == "score")
            .order_by(ModelRun.created_at.desc())
            .limit(1)
        ).first()
        if last is None:
            continue
        official = official_sets(db, video)
        if (last.params or {}).get("official_sets") == (
            [list(s) for s in official] if official else None
        ):
            continue
        score_video(db, video, reason="official_result")
        db.commit()
        done.append(video.id)
    return done


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
                rule=entry.get("rule") if kind == "serve" else None,
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
            .where(
                Event.video_id == video.id,
                Event.code == "S",
                Event.deleted_at.is_(None),
                tagging.event_stands(),  # a suggested serve is no fact
            )
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
    log_spans: list[tuple[int, int, int]] | None = None,
) -> None:
    """Replace the model's untouched sets with new ones: where the club scoreboard's log says
    which rally belongs to which set, from it; else from the long breaks and the official
    number of points per set. A set a coach rejected is not proposed again."""
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
    spans: list[tuple[int, int, int, float]]
    if log_spans:
        spans = [(no, first, last, 0.95) for no, first, last in log_spans]
    else:
        found = sc.propose_sets(
            [(r.start_ms, r.end_ms) for r in rallies],
            [a + b for a, b in official] if official else None,
            sure=[r.source != "model" or r.review in ("confirmed", "corrected") for r in rallies],
        )
        spans = [(no, x.first, x.last, x.confidence) for no, x in enumerate(found, start=1)]
    for k, (no, first, last, confidence) in enumerate(spans):
        prev_end = rallies[first - 1].end_ms if first > 0 else 0
        nxt = spans[k + 1][1] if k + 1 < len(spans) else None
        start = max(prev_end, rallies[first].start_ms - SET_LEAD_MS)
        end = rallies[last].end_ms + SET_TAIL_MS
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
                confidence=confidence,
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
        # Not the transaction's start: a video scored twice in one transaction (an analysis,
        # then its score log) has its newest run last.
        created_at=func.clock_timestamp(),
        model_name=MODEL_NAME,
        model_version=code_version(),
        params={
            "reason": reason,
            "serve_finder": serve_track.finder if serve_track else None,
            "serve_rule": (serve_track.rule or 1) if serve_track else None,
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
    # The club scoreboard's log (PLAN §10.10), lined up with these rallies.
    from wieplay.core import scorelog

    log_ev = scorelog.evidence_for(db, video, rallies)
    # `params` is plain JSONB: a key set in place is never written. A new dict is.
    if log_ev is not None:
        run.params = {**run.params, "score_log_run": str(log_ev.stored.track.model_run_id)}
    periods = _live_periods(db, video)
    if not any(_coach_period(p) for p in periods):
        log_spans = scorelog.set_spans(log_ev, rallies) if log_ev and log_ev.any_trusted else None
        _propose_periods(db, video, run, rallies, official, log_spans)
        periods = _live_periods(db, video)
    # The official scoresheet (PLAN §10.3a): its first servers and lineups on the sets.
    from wieplay.core import scoresheet

    sheet = scoresheet.for_scoring(db, video)
    if sheet is not None:
        run.params = {**run.params, "scoresheet": True}
        scoresheet.fill_periods(db, video, sheet.checks, sheet.on)

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
    # About half a match's serves are near ones: its readings are evened out (the rallies in
    # sets; a picture that leans them one way is shifted back).
    serve_shift = sc.balance([obs[i].p_near for idx in by_period.values() for i in idx])
    if serve_shift:
        obs = [replace(o, p_near=sc.shifted(o.p_near, serve_shift)) for o in obs]

    scored = [p for p in periods if by_period.get(p.id)]
    set_ins = [_set_in(p, [obs[i] for i in by_period[p.id]], official) for p in scored]
    # The sheet: strong evidence below the coach, about as strong as the log (the video decides
    # where they disagree); a draft read from the photo as weaker soft evidence.
    if sheet is not None and set_ins:
        set_ins = scoresheet.apply(sheet, set_ins)
    # The scoreboard, when it was read: it wins over the serve evidence (PLAN §10.3, step 5).
    from wieplay.core import scoreboard as sboard

    board = sboard.board_for(db, video, rallies, scored, by_period) if set_ins else None
    done = decide(set_ins, log_ev=log_ev, board=board)
    set_ins, match = done.set_ins, done.match
    board_metrics = log_metrics = None
    if board is not None and done.board_extra is not None:
        board_metrics = sboard.metrics(board, set_ins, match, done.board_extra)
    if log_ev is not None and done.log_disagree is not None:
        log_metrics = scorelog.metrics(log_ev, set_ins, match, done.log_disagree)
    scorelog.write(log_ev, rallies)
    scoresheet.write(match, rallies)
    sheet_metrics = scoresheet.metrics(sheet, set_ins, match) if sheet is not None else None

    # The rallies: clear, then write what the model found.
    by_key = {str(r.id): r for r in rallies}
    for rally, o in zip(rallies, obs, strict=True):
        rally.model_serving = rally.model_won_by = rally.score_note = None
        rally.model_missed_us = rally.model_missed_them = None
        rally.score_confidence = None
        rally.score_conflict = False
        # Outside every set (a warm-up burst, after the match): the coach says what it is.
        rally.score_sure = False if set_ins else None
        rally.score_reasons = [{"code": "no_set"}] if set_ins else None
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
            rally.score_confidence = _shown_confidence(out)
            rally.score_sure = out.sure
            rally.score_reasons = [r.as_json() for r in out.reasons]
            rally.score_note = out.reasons[0].code if out.reasons else out.note
            rally.score_conflict = rally.score_note is not None
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

    if sheet is not None:
        # A draft read from the photo: the boxes the joint decode is sure of resolve on their
        # own (PLAN §10.3a), so the coach checks only what is still unclear.
        from wieplay.core import sheet_reading

        sheet_reading.resolve(db, video, match)
    _sheet_timeouts(db, video, run, scoresheet.timeout_gaps(sheet, match, rallies) if sheet else [])
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
                "sheet": so.sheet,
            }
        )
    flagged = sum(s["flagged"] for s in sets_out)
    in_sets = sum(len(so.rallies) for so in match.sets)
    sure = sum(1 for so in match.sets for r in so.rallies if r.sure)
    reasons = Counter(r.reasons[0].code for so in match.sets for r in so.rallies if r.reasons)
    first_serve_ms = _first_serve_ms(db, video)
    run.metrics = {
        "serve_finder": serve_track.finder if serve_track else None,
        "serve_rule": (serve_track.rule or 1) if serve_track else None,
        "serve_shift": serve_shift,
        "sides_confidence": match.sides_confidence if set_ins else None,
        "rallies": in_sets,
        "flagged": flagged,
        "flagged_share": round(flagged / in_sets, 4) if in_sets else None,
        # The joint decode (PLAN §10.3): rallies sure, needing the coach, and the first reason.
        "sure": sure,
        "needs_coach": in_sets - sure,
        "sure_share": round(sure / in_sets, 4) if in_sets else None,
        "reasons": dict(reasons),
        "sure_threshold": sc.SURE,
        "sets": sets_out,
        "sets_official": sum(1 for s in sets_out if s["official"]),
        "sets_matching_official": sum(1 for s in sets_out if s["matches_official"]),
        "gaps": gap_counts,
        "first_serve_ms": first_serve_ms,
        "vs_coach": _vs_coach(set_ins, match),
        "notes": dict(Counter(r.note for so in match.sets for r in so.rallies if r.note)),
        "board": board_metrics,
        "log": log_metrics,
        "sheet": sheet_metrics,
    }
    if sheet is not None:
        # The sheet's timeouts, substitutions and sanctions checked at their breaks, and the
        # serves' players from its servers (PLAN §10.3a, §10.5).
        from wieplay.core import lineups
        from wieplay.core import players as found_players

        db.flush()
        court = lineups.for_video(db, video)
        if court is not None and run.metrics.get("sheet") is not None:
            notes = lineups.notes(db, video, court)
            run.metrics = {
                **run.metrics,
                "sheet": {**run.metrics["sheet"], "notes": sheet_notes.summary(notes)},
            }
        found_players.attribute_serves(db, video)
    run.timing = {"wall_s": round(time.perf_counter() - started, 3), "rallies": len(rallies)}
    from wieplay.core import actions  # the suggested actions follow the new sides (PLAN §10.4a)

    actions.after_scoring(db, video, reason)
    return run


def _shown_confidence(out: sc.RallyOut) -> float:
    """How sure the suggested answer is, as the review shows it ("87 %"): the posterior of the
    winner (or of no point), times how sure the set's sides are."""
    p = (out.p_us if out.won_by == "us" else out.p_them) if out.point else out.p_none
    return round(max(0.0, min(1.0, p)), 3)


# --- the joint decode, pure -----------------------------------------------------------------------


@dataclass
class Decision:
    """The decode with every source: the sets as decoded (with the log and the board), the
    match with each rally sure or not, and what the flags need for the metrics."""

    set_ins: list[sc.SetIn]
    match: sc.MatchOut
    board_extra: dict[str, int] | None = None
    log_disagree: int | None = None


def decide(
    set_ins: list[sc.SetIn],
    *,
    log_ev: Any = None,
    board: Any = None,
    sure: float = sc.SURE,
) -> Decision:
    """The joint decode of every set with all evidence there is (PLAN §10.3): the video, the
    sheet (in `set_ins` already), the club scoreboard's log (`log_ev`, wieplay/core/scorelog.py),
    the board read in the picture (`board`, wieplay/core/scoreboard.py), the official score; the
    flags where the video alone said otherwise; then which rallies are sure and why the others
    need the coach. No database: the evaluation runs the same (scripts/ml/fused_eval.py)."""
    from wieplay.core import scoreboard as sboard
    from wieplay.core import scorelog

    if not set_ins:
        return Decision([], sc.MatchOut([], 1.0))
    # The club scoreboard's log (§10.10): with the sheet, the strongest after a coach's facts.
    if log_ev is not None:
        set_ins = scorelog.apply(log_ev, set_ins)
    if board is not None:
        set_ins = sboard.orient(board, set_ins)
    match = sc.score_match(set_ins)
    out = Decision(set_ins, match)
    if board is not None:
        out.match, out.board_extra = sboard.flag_disagreements(set_ins, out.match)
    if log_ev is not None:
        out.match, out.log_disagree = scorelog.flag_disagreements(set_ins, out.match)
    log_scores: dict[str, tuple[int, int]] = {}
    if log_ev is not None:
        for st in set_ins:
            for r in st.rallies:
                v = log_ev.verdict(r.key)
                if v is not None and v.score is not None:
                    log_scores[r.key] = scorelog.ours(v.score, log_ev.us)
    for st, so in zip(set_ins, out.match.sets, strict=True):
        sc.judge(st, so, extra=note_reasons(st, so, log_scores), sure=sure)
    return out


SHORT_TIMEOUT_S = 20.0  # a timeout in a break this short does not fit there


def note_reasons(
    st: sc.SetIn, so: sc.SetOut, log_scores: Mapping[str, tuple[int, int]] | None = None
) -> dict[str, list[sc.Reason]]:
    """The sheet's timeouts, substitutions and sanctions against the decoded score: a note whose
    score the decode never had, a timeout in a break too short for one, a score the club
    scoreboard's log had otherwise there, a penalty point (no rally, by the rules) on a rally.
    The reason goes to the rally next to the break ("a timeout at 14:12 doesn't fit here")."""
    if not st.notes or not so.rallies:
        return {}
    obs = {r.key: r for r in st.rallies}
    outs = so.rallies
    # Per rally: the score before its gap, right before it (after the points without a rally),
    # after it.
    before = (st.start_us, st.start_them)
    places: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int]]] = []
    for ro in outs:
        pre = (before[0] + ro.missed[0], before[1] + ro.missed[1])
        after = (ro.score_us, ro.score_them)
        places.append((before, pre, after))
        before = after
    found: dict[str, list[sc.Reason]] = {}

    def flag(key: str, note: sc.BreakNote, issue: str, here: tuple[int, int]) -> None:
        params: dict[str, Any] = {
            "kind": note.kind,
            "side": note.side,
            "score": list(note.score),
            "issue": issue,
            "here": list(here),
        }
        if note.sanction:
            params["sanction"] = note.sanction
        found.setdefault(key, []).append(sc.Reason("note", params))

    for note in st.notes:
        k_after = next(
            (k for k, (_, _, after) in enumerate(places) if outs[k].point and after == note.score),
            None,
        )
        k_gap = next(
            (
                k
                for k, (b0, pre, _) in enumerate(places)
                if sum(b0) < note.points <= sum(pre)
                and pre[0] >= note.score[0]
                and pre[1] >= note.score[1]
                and (b0[0] <= note.score[0] and b0[1] <= note.score[1])
            ),
            None,
        )
        if note.kind == "sanction" and note.sanction in ("P", "DP"):
            # The penalty point: the next point after the sanction's score goes to the other
            # team without a rally.
            k = next(
                (
                    k
                    for k, (_, pre, _) in enumerate(places)
                    if outs[k].point and pre == note.score and outs[k].won_by == other(note.side)
                ),
                None,
            )
            if k is not None:
                flag(outs[k].key, note, "penalty_rally", places[k][2])
            continue
        if k_after is None and k_gap is None:
            # Never this score: the rally where the set had as many points.
            k = next(
                (k for k, (_, _, after) in enumerate(places) if sum(after) >= note.points),
                len(outs) - 1,
            )
            flag(outs[k].key, note, "off_path", places[k][2])
            continue
        if k_after is not None:
            k = k_after
            nxt = next((j for j in range(k + 1, len(outs))), None)
            gap_s = (
                (obs[outs[nxt].key].start_ms - obs[outs[k].key].end_ms) / 1000
                if nxt is not None
                else None
            )
            score_log = (log_scores or {}).get(outs[k].key)
            if score_log is not None and score_log != note.score:
                flag(outs[k].key, note, "log", score_log)
            elif note.kind == "timeout" and gap_s is not None and gap_s < SHORT_TIMEOUT_S:
                flag(outs[k].key, note, "short", places[k][2])
    return found


def other(side: str) -> sc.Side:
    return "them" if side == "us" else "us"


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


def _sheet_timeouts(
    db: Session, video: Video, run: ModelRun, found: Sequence[tuple[int, int, str, str]]
) -> None:
    """The sheet's timeouts as `timeout` breaks (source "import", with the team that called
    it), replaced on every run unless a coach changed or removed one; never over a coach's own
    break there."""
    from wieplay.core import scoresheet

    db.execute(
        delete(Segment).where(
            Segment.video_id == video.id,
            Segment.kind == "timeout",
            Segment.source == "import",
            Segment.import_system == scoresheet.IMPORT_SYSTEM,
            Segment.updated_by.is_(None),
            Segment.deleted_at.is_(None),
        )
    )
    if not found:
        return
    others = db.execute(
        select(Segment.start_ms, Segment.end_ms, Segment.source, Segment.review, Segment.deleted_at)
        .where(Segment.video_id == video.id, Segment.kind.in_(GAP_KINDS))
    ).all()  # fmt: skip
    blocked = [
        (int(s), int(e))
        for s, e, source, review, deleted in others
        # a coach's break, or one of these a coach removed; the model's open ones are replaced
        if (deleted is None and not (source == "model" and review == "unreviewed"))
        or (deleted is not None and source == "import")
    ]
    for start, end, team, ref in found:
        if end <= start or any(_overlap((start, end), b) >= OVERLAP for b in blocked):
            continue
        db.add(
            Segment(
                id=uuid.uuid4(),
                video_id=video.id,
                kind="timeout",
                start_ms=start,
                end_ms=end,
                source="import",
                import_system=scoresheet.IMPORT_SYSTEM,
                import_ref=ref,
                called_by=team,
                model_run_id=run.id,
                review="unreviewed",
                version=1,
            )
        )
    db.flush()


def _label_gaps(
    db: Session,
    video: Video,
    run: ModelRun,
    rallies: Sequence[Segment],
    periods: Sequence[Period],
    by_period: dict[uuid.UUID, list[int]],
    act: gaps.Activity,
) -> dict[str, int]:
    """Label the gaps again: the model's untouched labels follow the new ones; never over a
    coach's own dead time label or one a coach rejected.

    Every decision in the review scores again (PLAN §10.1), so a label that stays (the same
    kind over about the same stretch) keeps its row and its id: the review may have it on
    screen. Only labels that changed are created or removed."""
    untouched = list(
        db.scalars(
            select(Segment).where(
                Segment.video_id == video.id,
                Segment.kind.in_(GAP_KINDS),
                Segment.source == "model",
                Segment.review == "unreviewed",
                Segment.updated_by.is_(None),
                Segment.deleted_at.is_(None),
            )
        )
    )
    mine = {s.id for s in untouched}
    others = db.execute(
        select(
            Segment.id,
            Segment.start_ms,
            Segment.end_ms,
            Segment.source,
            Segment.review,
            Segment.deleted_at,
        ).where(Segment.video_id == video.id, Segment.kind.in_(GAP_KINDS))
    ).all()
    blocked = [
        (int(s), int(e))
        for id_, s, e, source, review, deleted in others
        if id_ not in mine and (deleted is None or (source == "model" and review == "rejected"))
    ]
    sets = [
        gaps.SetRange(rallies[by_period[p.id][0]].start_ms, rallies[by_period[p.id][-1]].end_ms)
        for p in periods
    ]
    labels = gaps.classify(
        [(r.start_ms, r.end_ms) for r in rallies], sets, video.duration_ms or 0, act
    )
    counts: dict[str, int] = dict.fromkeys(gaps.KINDS, 0)
    for label in labels:
        span = (label.start_ms, label.end_ms)
        if span[1] <= span[0] or any(_overlap(span, b) >= OVERLAP for b in blocked):
            continue
        counts[label.kind] += 1
        same = next((s for s in untouched if s.kind == label.kind and _same_stretch(span, s)), None)
        if same is not None:
            untouched.remove(same)
            # The model's own fields: the version stays, so an answer on its way still fits.
            same.start_ms, same.end_ms = span
            same.confidence = label.confidence
            same.model_run_id = run.id
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
    for gone in untouched:
        db.delete(gone)
    db.flush()
    return counts


def _same_stretch(span: tuple[int, int], segment: Segment) -> bool:
    """About the same stretch of the video: either covers most of the other."""
    other = (segment.start_ms, segment.end_ms)
    return _overlap(span, other) >= OVERLAP or _overlap(other, span) >= OVERLAP


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

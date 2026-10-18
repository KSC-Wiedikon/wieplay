// Rally proposals from the automatic analysis (docs/PLAN.md §10, §10.1, §11) and the gap
// labels of the automatic scoring (§10.3): which segments are suggestions, the review list and
// its order, and the loop the review plays. Pure, so the review controller and the pages share
// one reading of the data.

import { byStart, ralliesOf } from './rallies'
import type { GapKind, Period, Segment, SegmentKind, Side } from './types'

/** "Accept all remaining with confidence ≥ 90%". */
export const ACCEPT_ALL_MIN = 0.9
/** The review loop starts this long before a rally and ends this long after it. */
export const LOOP_PRE_MS = 1000
export const LOOP_POST_MS = 500
/** Past the loop's end by more than this, the viewer moved on (a seek): no restart. */
const LOOP_SLACK_MS = 1500

type Kind = Pick<Segment, 'kind' | 'source' | 'review'>

/** Made by a model (any review state). */
export function isModel(segment: Pick<Segment, 'source'>): boolean {
  return segment.source === 'model'
}

/** A model's rally proposal that nobody has answered yet. */
export function isSuggestion(segment: Kind): boolean {
  return segment.kind === 'rally' && segment.source === 'model' && segment.review === 'unreviewed'
}

export const GAP_KINDS: readonly GapKind[] = ['warmup', 'set_break', 'timeout', 'stoppage', 'other_dead']

/** Dead time with a label (warm-up, set break, timeout, stoppage, after the match). */
export function isGap(kind: SegmentKind): kind is GapKind {
  return (GAP_KINDS as readonly string[]).includes(kind)
}

/** A model's gap label that nobody has answered yet. */
export function isGapSuggestion(segment: Kind): boolean {
  return isGap(segment.kind) && segment.source === 'model' && segment.review === 'unreviewed'
}

/** Waits for an answer in the review: a rally proposal or a gap label. */
export function isProposal(segment: Kind): boolean {
  return isSuggestion(segment) || isGapSuggestion(segment)
}

/** A model's rally that a person accepted or corrected: a rally like any other, with a marker. */
export function isReviewedSuggestion(segment: Kind): boolean {
  return segment.kind === 'rally' && segment.source === 'model' && (segment.review === 'confirmed' || segment.review === 'corrected')
}

/** A model's rally or break label that a person accepted or corrected. */
export function isReviewedProposal(segment: Kind): boolean {
  return (segment.kind === 'rally' || isGap(segment.kind)) && segment.source === 'model' && (segment.review === 'confirmed' || segment.review === 'corrected')
}

/** Rallies shown as fact: all but unanswered proposals (PLAN §10: nothing is fact until reviewed). */
export function factualRallies(segments: readonly Segment[]): Segment[] {
  return ralliesOf(segments).filter((s) => !isSuggestion(s))
}

export function suggestionCount(segments: readonly Segment[]): number {
  return segments.filter(isSuggestion).length
}

/** The model's rallies in time order, whatever their review state. */
export function modelRallies(segments: readonly Segment[]): Segment[] {
  return ralliesOf(segments).filter(isModel)
}

/**
 * The review list: the model's rallies and gap labels plus the rallies split off in this
 * session (`extra`, manual rallies that still wait for an answer), in time order.
 */
export function reviewItems(segments: readonly Segment[], extra: ReadonlySet<string>): Segment[] {
  return segments
    .filter((s) => (s.kind === 'rally' && (isModel(s) || extra.has(s.id))) || (isGap(s.kind) && isModel(s)))
    .sort(byStart)
}

/** Still waits for an answer: an unanswered proposal, or a part split off in this session. */
export function isOpen(segment: Segment, pending: ReadonlySet<string>): boolean {
  return isProposal(segment) || pending.has(segment.id)
}

/**
 * The next item to answer after `from`: the first open one that starts later, else the first
 * open one from the start (one skipped earlier). Null when everything is answered.
 */
export function nextOpen(items: readonly Segment[], pending: ReadonlySet<string>, from: Pick<Segment, 'id' | 'start_ms'> | null): Segment | null {
  const open = items.filter((s) => isOpen(s, pending) && s.id !== from?.id)
  if (!from) return open[0] ?? null
  return open.find((s) => s.start_ms > from.start_ms) ?? open[0] ?? null
}

/** Open proposals that are at least this sure ("accept all remaining with confidence ≥ 90%"). */
export function sureSuggestions(segments: readonly Segment[], min = ACCEPT_ALL_MIN): Segment[] {
  return segments.filter((s) => isSuggestion(s) && s.confidence != null && s.confidence >= min).sort(byStart)
}

/**
 * What the club scoreboard's log (PLAN §10.10) says about an open rally suggestion:
 * - match: the log has a point for it and WiePlay's winner is the log's, nothing flagged;
 * - none: the log says no point (a replay, or no rally);
 * - disagree: the log names another winner, or the video alone was sure of another answer
 *   (flagged "log"), or a coach's call contradicts it;
 * - missed: a point the log has right next to it with no rally of its own (flagged: a rally
 *   the finder missed, to add);
 * - silent: the log says nothing (no press for it, or a set whose log is not trusted).
 * The server sends `log_won_by` only in trusted sets (a log that ends on the official score).
 */
export type LogVerdict = 'match' | 'none' | 'disagree' | 'missed' | 'silent'

export function logVerdict(rally: Pick<Segment, 'log_won_by' | 'won_by' | 'model_won_by' | 'score_conflict' | 'score_note'>): LogVerdict {
  const says = rally.log_won_by ?? null
  if (says === null) return 'silent'
  if (says === 'none') return 'none'
  if (effectiveWinner(rally).value !== says) return 'disagree'
  if (!rally.score_conflict) return 'match'
  return rally.score_note === 'missing_before' || rally.score_note === 'missing_after' ? 'missed' : 'disagree'
}

/** The video has a trusted scoreboard log: some rally has the log's answer. */
export function hasTrustedLog(segments: readonly Segment[]): boolean {
  return segments.some((s) => s.kind === 'rally' && s.log_won_by != null)
}

/** Open rally suggestions that match the scoreboard log ("accept all that match the log"). */
export function logMatches(segments: readonly Segment[]): Segment[] {
  return segments.filter((s) => isSuggestion(s) && logVerdict(s) === 'match').sort(byStart)
}

/** The open rally suggestions by what the log says about them. */
export function logCounts(segments: readonly Segment[]): Record<LogVerdict, number> {
  const counts: Record<LogVerdict, number> = { match: 0, none: 0, disagree: 0, missed: 0, silent: 0 }
  for (const s of segments) if (isSuggestion(s)) counts[logVerdict(s)] += 1
  return counts
}

export interface ReviewProgress {
  /** Answered: accepted, corrected or rejected. */
  done: number
  total: number
}

/**
 * Progress over every proposal of the video: those seen in this session (live or rejected since)
 * and those rejected before it, which the tagging data no longer lists.
 */
export function reviewProgress(segments: readonly Segment[], seen: ReadonlySet<string>, rejectedBefore: number): ReviewProgress {
  const open = segments.filter((s) => isSuggestion(s) && seen.has(s.id)).length
  const total = seen.size + rejectedBefore
  return { done: Math.max(0, total - open), total }
}

/** Progress over the gap labels seen in this session (live or rejected since). */
export function gapProgress(segments: readonly Segment[], seen: ReadonlySet<string>): ReviewProgress {
  const open = segments.filter((s) => isGapSuggestion(s) && seen.has(s.id)).length
  return { done: Math.max(0, seen.size - open), total: seen.size }
}

/** The review's progress: rallies and gap labels together. */
export function allProgress(
  segments: readonly Segment[],
  seen: ReadonlySet<string>,
  seenGaps: ReadonlySet<string>,
  rejectedBefore: number,
): ReviewProgress {
  const rallies = reviewProgress(segments, seen, rejectedBefore)
  const gaps = gapProgress(segments, seenGaps)
  return { done: rallies.done + gaps.done, total: rallies.total + gaps.total }
}

// Who served and who won: the coach's facts, else the model's suggestions (PLAN §10.3).

export interface Suggested<T> {
  value: T | null
  /** The value is the model's, not a fact. */
  suggested: boolean
}

/** The serving team to show: the fact, else the model's suggestion. */
export function effectiveServer(rally: Pick<Segment, 'serving' | 'model_serving'>): Suggested<Side> {
  if (rally.serving) return { value: rally.serving, suggested: false }
  return { value: rally.model_serving ?? null, suggested: rally.model_serving != null }
}

/** The winner to show: the coach's, else the model's suggestion. */
export function effectiveWinner(rally: Pick<Segment, 'won_by' | 'model_won_by'>): Suggested<Side> {
  if (rally.won_by) return { value: rally.won_by, suggested: false }
  return { value: rally.model_won_by ?? null, suggested: rally.model_won_by != null }
}

/** The score after a rally ("12:10"), and whether a model's winner went into it; null when unknown. */
export function scoreAfter(rally: Pick<Segment, 'score_us_after' | 'score_them_after' | 'score_source'>): { text: string; suggested: boolean } | null {
  if (rally.score_us_after === null || rally.score_them_after === null) return null
  return { text: `${rally.score_us_after}:${rally.score_them_after}`, suggested: rally.score_source === 'model' }
}

/** A model's set that nobody has accepted or rejected yet. */
export function isSetSuggestion(period: Pick<Period, 'source' | 'review'>): boolean {
  return period.source === 'model' && period.review === 'unreviewed'
}

const PERCENT = new Intl.NumberFormat('de-CH', { style: 'percent', maximumFractionDigits: 0 })

/** 0.87 → "87%" (de-CH, like the stats); empty when unknown. */
export function formatPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '' : PERCENT.format(value)
}

/** 12_600 → "12.6 s". */
export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`
}

/**
 * Whether the review loop starts over: the playhead just passed the rally's end (with its
 * post-roll) while playing through it. Further away, the viewer seeked on purpose.
 */
export function loopRestart(t_ms: number, range: Pick<Segment, 'start_ms' | 'end_ms'>): boolean {
  const end = range.end_ms + LOOP_POST_MS
  return t_ms >= end && t_ms < end + LOOP_SLACK_MS
}

// WiePlay's rallies (docs/PLAN.md §10, §10.1, §11) and the break labels of its scoring (§10.3):
// every one of them counts like a person's, the sure ones (review "auto") and the unsure ones
// (review "check") alike; what is unsure waits in one review, "To check". Which segments are
// WiePlay's, which are still to check, the walk's list and order, and the loop the review plays.
// Pure, so the review controller and the pages share one reading of the data (the server reads
// "to check" the same way).

import { byStart, ralliesOf } from './rallies'
import type { GapKind, Period, Review, Segment, SegmentKind, Side } from './types'

/** The review loop starts this long before a rally and ends this long after it. */
export const LOOP_PRE_MS = 1000
export const LOOP_POST_MS = 500
/** Past the loop's end by more than this, the viewer moved on (a seek): no restart. */
const LOOP_SLACK_MS = 1500

type Kind = Pick<Segment, 'kind' | 'source' | 'review'>

/** WiePlay's own review states: nobody has answered (`unreviewed` from older data). */
const UNTOUCHED: readonly Review[] = ['auto', 'check', 'unreviewed']
/** A person's answer. */
const ANSWERED: readonly Review[] = ['confirmed', 'corrected']

/** Made by a model (any review state). */
export function isModel(segment: Pick<Segment, 'source'>): boolean {
  return segment.source === 'model'
}

/** WiePlay's rally or break that nobody has answered yet (sure or to check). */
export function isWiePlays(segment: Pick<Segment, 'source' | 'review'>): boolean {
  return segment.source === 'model' && UNTOUCHED.includes(segment.review)
}

/** WiePlay's rally or break that is sure: it counts, nobody needs to check it. */
export function isAuto(segment: Pick<Segment, 'source' | 'review'>): boolean {
  return segment.source === 'model' && segment.review === 'auto'
}

export const GAP_KINDS: readonly GapKind[] = ['warmup', 'set_break', 'timeout', 'stoppage', 'substitution', 'other_dead']

/** Dead time with a label (warm-up, set break, timeout, stoppage, after the match). */
export function isGap(kind: SegmentKind): kind is GapKind {
  return (GAP_KINDS as readonly string[]).includes(kind)
}

type CheckFields = Pick<Segment, 'kind' | 'source' | 'review' | 'deleted_at'> & Partial<Pick<Segment, 'score_sure' | 'won_by'>>

/**
 * A rally still to check: WiePlay's unsure rally (review "check"), or one whose winner the
 * scoring is not sure of while nobody has said who won or answered the rally (a person's own
 * rally too). Rejected and merged ones are gone.
 */
export function rallyToCheck(segment: CheckFields): boolean {
  if (segment.kind !== 'rally' || segment.deleted_at || segment.review === 'rejected' || segment.review === 'merged') return false
  if (segment.review === 'check') return true
  return segment.score_sure === false && !segment.won_by && !ANSWERED.includes(segment.review)
}

/** A break WiePlay labelled and is not sure of. */
export function gapToCheck(segment: CheckFields): boolean {
  return isGap(segment.kind) && segment.source === 'model' && segment.review === 'check' && !segment.deleted_at
}

/** Waits in "To check": a rally or a break. */
export function isToCheck(segment: CheckFields): boolean {
  return rallyToCheck(segment) || gapToCheck(segment)
}

/** A model's rally or break label that a person accepted or corrected. */
export function isReviewedProposal(segment: Kind): boolean {
  return (segment.kind === 'rally' || isGap(segment.kind)) && segment.source === 'model' && ANSWERED.includes(segment.review)
}

/** The model's rallies in time order, whatever their review state. */
export function modelRallies(segments: readonly Segment[]): Segment[] {
  return ralliesOf(segments).filter(isModel)
}

/**
 * The review list: WiePlay's rallies and break labels, the rallies still to check (a person's
 * too: who won) and the rallies split off in this session (`extra`, manual rallies that still
 * wait for an answer), in time order.
 */
export function reviewItems(segments: readonly Segment[], extra: ReadonlySet<string>): Segment[] {
  return segments
    .filter((s) => (s.kind === 'rally' && (isModel(s) || extra.has(s.id) || rallyToCheck(s))) || (isGap(s.kind) && isModel(s)))
    .sort(byStart)
}

/** Still waits for an answer: to check, or a part split off in this session. */
export function isOpen(segment: Segment, pending: ReadonlySet<string>): boolean {
  return isToCheck(segment) || pending.has(segment.id)
}

/**
 * The next item to answer after `from`: the first open one that starts later, else the first
 * open one from the start (one skipped earlier). Null when everything is checked.
 */
export function nextOpen(items: readonly Segment[], pending: ReadonlySet<string>, from: Pick<Segment, 'id' | 'start_ms'> | null): Segment | null {
  const open = items.filter((s) => isOpen(s, pending) && s.id !== from?.id)
  if (!from) return open[0] ?? null
  return open.find((s) => s.start_ms > from.start_ms) ?? open[0] ?? null
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

/** The rallies still to check, in time order. */
export function toCheckRallies(segments: readonly Segment[]): Segment[] {
  return segments.filter(rallyToCheck).sort(byStart)
}

/** The rallies and breaks still to check, in time order. */
export function toCheckSegments(segments: readonly Segment[]): Segment[] {
  return segments.filter(isToCheck).sort(byStart)
}

export interface ReviewProgress {
  /** Checked: accepted, corrected or rejected. */
  done: number
  total: number
}

/**
 * Progress over the rallies to check of this session: those seen to check (live or rejected
 * since) and those rejected before it, which the tagging data no longer lists.
 */
export function reviewProgress(segments: readonly Segment[], seen: ReadonlySet<string>, rejectedBefore: number): ReviewProgress {
  const open = segments.filter((s) => rallyToCheck(s) && seen.has(s.id)).length
  const total = seen.size + rejectedBefore
  return { done: Math.max(0, total - open), total }
}

/** Progress over the break labels seen to check in this session (live or rejected since). */
export function gapProgress(segments: readonly Segment[], seen: ReadonlySet<string>): ReviewProgress {
  const open = segments.filter((s) => gapToCheck(s) && seen.has(s.id)).length
  return { done: Math.max(0, seen.size - open), total: seen.size }
}

/**
 * The review's progress: rallies and gap labels together, and the parts split off or made in
 * this session (`pending`) that still wait: open is what the review's walk still visits.
 */
export function allProgress(
  segments: readonly Segment[],
  seen: ReadonlySet<string>,
  seenGaps: ReadonlySet<string>,
  rejectedBefore: number,
  pending: ReadonlySet<string> = new Set(),
): ReviewProgress {
  const rallies = reviewProgress(segments, seen, rejectedBefore)
  const gaps = gapProgress(segments, seenGaps)
  const waiting = pending.size === 0 ? 0 : segments.filter((s) => pending.has(s.id) && !isToCheck(s)).length
  return { done: rallies.done + gaps.done, total: rallies.total + gaps.total + waiting }
}

// Who served and who won: a person's facts, else WiePlay's (PLAN §10.3), which count too.

export interface Suggested<T> {
  value: T | null
  /** The value is WiePlay's, not a person's. */
  suggested: boolean
}

/** The serving team to show: the fact, else WiePlay's. */
export function effectiveServer(rally: Pick<Segment, 'serving' | 'model_serving'>): Suggested<Side> {
  if (rally.serving) return { value: rally.serving, suggested: false }
  return { value: rally.model_serving ?? null, suggested: rally.model_serving != null }
}

/** The winner to show: the coach's, else WiePlay's. */
export function effectiveWinner(rally: Pick<Segment, 'won_by' | 'model_won_by'>): Suggested<Side> {
  if (rally.won_by) return { value: rally.won_by, suggested: false }
  return { value: rally.model_won_by ?? null, suggested: rally.model_won_by != null }
}

/** The score after a rally ("12:10"), and whether a model's winner went into it; null when unknown. */
export function scoreAfter(rally: Pick<Segment, 'score_us_after' | 'score_them_after' | 'score_source'>): { text: string; suggested: boolean } | null {
  if (rally.score_us_after === null || rally.score_them_after === null) return null
  return { text: `${rally.score_us_after}:${rally.score_them_after}`, suggested: rally.score_source === 'model' }
}

/** WiePlay's set that nobody has accepted or rejected yet (sure or to check). */
export function isWiePlaysSet(period: Pick<Period, 'source' | 'review'>): boolean {
  return period.source === 'model' && UNTOUCHED.includes(period.review ?? 'unreviewed')
}

/** WiePlay's set that it is not sure of: it counts, and waits in "To check". */
export function setToCheck(period: Pick<Period, 'source' | 'review'> & { deleted_at?: string | null }): boolean {
  return period.source === 'model' && period.review === 'check' && !period.deleted_at
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

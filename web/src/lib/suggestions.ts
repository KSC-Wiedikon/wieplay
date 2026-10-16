// Rally proposals from the automatic analysis (docs/PLAN.md §10, §10.1, §11): which segments
// are suggestions, the review list and its order, and the loop the review plays. Pure, so the
// review controller and the pages share one reading of the data.

import { byStart, ralliesOf } from './rallies'
import type { Segment } from './types'

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

/** A model's rally that a person accepted or corrected: a rally like any other, with a marker. */
export function isReviewedSuggestion(segment: Kind): boolean {
  return segment.kind === 'rally' && segment.source === 'model' && (segment.review === 'confirmed' || segment.review === 'corrected')
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
 * The review list: the model's rallies plus the rallies split off in this session (`extra`,
 * manual rallies that still wait for an answer), in time order.
 */
export function reviewItems(segments: readonly Segment[], extra: ReadonlySet<string>): Segment[] {
  return segments.filter((s) => s.kind === 'rally' && (isModel(s) || extra.has(s.id))).sort(byStart)
}

/** Still waits for an answer: an unanswered proposal, or a part split off in this session. */
export function isOpen(segment: Segment, pending: ReadonlySet<string>): boolean {
  return isSuggestion(segment) || pending.has(segment.id)
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

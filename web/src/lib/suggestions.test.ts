import { describe, expect, it } from 'vitest'
import { period, rally } from '../test/tagging'
import type { Segment } from './types'
import {
  allProgress,
  effectiveServer,
  effectiveWinner,
  formatPercent,
  gapProgress,
  gapToCheck,
  hasTrustedLog,
  isAuto,
  isReviewedProposal,
  isToCheck,
  isWiePlays,
  isWiePlaysSet,
  logVerdict,
  loopRestart,
  nextOpen,
  rallyToCheck,
  reviewItems,
  reviewProgress,
  scoreAfter,
  setToCheck,
  toCheckRallies,
  toCheckSegments,
} from './suggestions'

const manual = rally({ id: 'm1', start_ms: 1_000, end_ms: 5_000 })
// WiePlay's rallies: one it is sure of (auto), two it is not (check), two a person answered.
const check1 = rally({ id: 'a', start_ms: 10_000, end_ms: 15_000, source: 'model', review: 'check', confidence: 0.95, score_sure: false })
const done = rally({ id: 'b', start_ms: 20_000, end_ms: 25_000, source: 'model', review: 'confirmed', confidence: 0.6, won_by: 'us' })
const check2 = rally({ id: 'c', start_ms: 30_000, end_ms: 35_000, source: 'model', review: 'check', confidence: 0.7, score_sure: false })
const fixed = rally({ id: 'd', start_ms: 40_000, end_ms: 45_000, source: 'model', review: 'corrected', confidence: 0.5 })
const sure = rally({ id: 'e', start_ms: 46_000, end_ms: 49_000, source: 'model', review: 'auto', score_sure: true })
const timeout = rally({ id: 't', kind: 'timeout', start_ms: 50_000, end_ms: 80_000, source: 'model', review: 'check', confidence: 0.6 })
const setBreak = rally({ id: 'sb', kind: 'set_break', start_ms: 90_000, end_ms: 200_000, source: 'model', review: 'auto', confidence: 0.9 })
const all = [check2, manual, fixed, check1, done, timeout, sure, setBreak]

describe('WiePlay’s rallies and what is still to check', () => {
  it('tells WiePlay’s own (sure or to check) from answered ones and from rallies marked by hand', () => {
    expect(all.filter(isWiePlays).map((s) => s.id)).toEqual(['c', 'a', 't', 'e', 'sb'])
    expect(all.filter(isAuto).map((s) => s.id)).toEqual(['e', 'sb'])
    expect(all.filter(isReviewedProposal).map((s) => s.id)).toEqual(['d', 'b'])
    // Older data: a model's row still "unreviewed" is WiePlay's too.
    expect(isWiePlays(rally({ start_ms: 0, end_ms: 1, source: 'model' }))).toBe(true)
  })

  it('reads "to check" like the server: unsure rallies and breaks, and a rally whose winner is unsure', () => {
    expect(all.filter(isToCheck).map((s) => s.id)).toEqual(['c', 'a', 't'])
    expect(all.filter(gapToCheck).map((s) => s.id)).toEqual(['t'])
    expect(toCheckRallies(all).map((s) => s.id)).toEqual(['a', 'c'])
    expect(toCheckSegments(all).map((s) => s.id)).toEqual(['a', 'c', 't'])
    // A person's rally the scoring is unsure of, with no winner: to check (who won).
    const unsureHand = rally({ start_ms: 0, end_ms: 1, score_sure: false })
    expect(rallyToCheck(unsureHand)).toBe(true)
    // … not once someone said who won, or confirmed it.
    expect(rallyToCheck({ ...unsureHand, won_by: 'them' })).toBe(false)
    expect(rallyToCheck({ ...unsureHand, review: 'confirmed' })).toBe(false)
    // Rejected, merged or deleted ones are gone.
    expect(rallyToCheck({ ...check1, review: 'rejected' })).toBe(false)
    expect(rallyToCheck({ ...check1, deleted_at: '2026-10-20T10:00:00Z' })).toBe(false)
    // A break a person labelled is never to check.
    expect(gapToCheck(rally({ kind: 'timeout', start_ms: 0, end_ms: 1, review: 'check' }))).toBe(false)
  })

  it("lists WiePlay's rallies and break labels, the rallies to check and the parts split off in this session, in time order", () => {
    expect(reviewItems(all, new Set()).map((s) => s.id)).toEqual(['a', 'b', 'c', 'd', 'e', 't', 'sb'])
    expect(reviewItems(all, new Set(['m1'])).map((s) => s.id)).toEqual(['m1', 'a', 'b', 'c', 'd', 'e', 't', 'sb'])
    // A break marked by hand is not WiePlay's.
    expect(reviewItems([rally({ id: 'mt', kind: 'timeout', start_ms: 0, end_ms: 30_000 })], new Set())).toEqual([])
    // A person's rally to check is in the walk.
    expect(reviewItems([rally({ id: 'h', start_ms: 0, end_ms: 1, score_sure: false })], new Set()).map((s) => s.id)).toEqual(['h'])
  })

  it('goes to the next item to check later in time, then wraps to one skipped earlier; sure ones are not visited', () => {
    const items = reviewItems(all, new Set())
    expect(nextOpen(items, new Set(), null)?.id).toBe('a')
    expect(nextOpen(items, new Set(), check1)?.id).toBe('c')
    expect(nextOpen(items, new Set(), done)?.id).toBe('c')
    expect(nextOpen(items, new Set(), check2)?.id).toBe('t')
    expect(nextOpen(items, new Set(), timeout)?.id).toBe('a')
    expect(nextOpen([done, fixed, sure], new Set(), done)).toBeNull()
    // A split-off part waits like one to check.
    expect(nextOpen(reviewItems(all, new Set(['m1'])), new Set(['m1']), timeout)?.id).toBe('m1')
  })

  it('counts progress over the rallies seen to check, those rejected earlier included', () => {
    const seen = new Set(['a', 'b', 'c', 'd', 'gone'])
    expect(reviewProgress(all, seen, 3)).toEqual({ done: 6, total: 8 })
    expect(reviewProgress([], new Set(), 0)).toEqual({ done: 0, total: 0 })
  })

  it('counts the break labels apart, and both together for the review', () => {
    const seenGaps = new Set(['t', 'gone'])
    expect(gapProgress(all, seenGaps)).toEqual({ done: 1, total: 2 })
    expect(allProgress(all, new Set(['a', 'b', 'c', 'd']), seenGaps, 1)).toEqual({ done: 4, total: 7 })
  })

  it("shows the coach's facts, else WiePlay's", () => {
    expect(effectiveServer({ serving: 'us', model_serving: 'them' })).toEqual({ value: 'us', suggested: false })
    expect(effectiveServer({ serving: null, model_serving: 'them' })).toEqual({ value: 'them', suggested: true })
    expect(effectiveServer({ serving: null })).toEqual({ value: null, suggested: false })
    expect(effectiveWinner({ won_by: null, model_won_by: 'us' })).toEqual({ value: 'us', suggested: true })
    expect(effectiveWinner({ won_by: 'them', model_won_by: 'us' })).toEqual({ value: 'them', suggested: false })
    expect(scoreAfter({ score_us_after: 12, score_them_after: 10, score_source: 'model' })).toEqual({ text: '12:10', suggested: true })
    expect(scoreAfter({ score_us_after: 3, score_them_after: 1, score_source: 'tags' })).toEqual({ text: '3:1', suggested: false })
    expect(scoreAfter({ score_us_after: null, score_them_after: null, score_source: null })).toBeNull()
  })

  it('tells a set WiePlay is unsure of until someone checks it', () => {
    expect(setToCheck(period({ no: 1, start_ms: 0, source: 'model', review: 'check' }))).toBe(true)
    expect(setToCheck(period({ no: 1, start_ms: 0, source: 'model', review: 'auto' }))).toBe(false)
    expect(setToCheck(period({ no: 1, start_ms: 0, source: 'model', review: 'confirmed' }))).toBe(false)
    expect(setToCheck(period({ no: 1, start_ms: 0 }))).toBe(false)
    expect(isWiePlaysSet(period({ no: 1, start_ms: 0, source: 'model', review: 'auto' }))).toBe(true)
    expect(isWiePlaysSet(period({ no: 1, start_ms: 0, source: 'model', review: 'corrected' }))).toBe(false)
  })

  it('says what the scoreboard log says about a rally', () => {
    const open = (id: string, start_ms: number, patch: Partial<Segment>) => rally({ id, start_ms, end_ms: start_ms + 5_000, source: 'model', ...patch })
    expect(logVerdict(open('m', 0, { log_won_by: 'us', model_won_by: 'us' }))).toBe('match')
    expect(logVerdict(open('n', 0, { log_won_by: 'none' }))).toBe('none')
    expect(logVerdict(open('o', 0, { log_won_by: 'us', model_won_by: 'them' }))).toBe('disagree')
    expect(logVerdict(open('x', 0, { log_won_by: 'them', model_won_by: 'them', score_conflict: true, score_note: 'missing_before' }))).toBe('missed')
    expect(logVerdict(open('s', 0, { model_won_by: 'us' }))).toBe('silent')
    // A coach's winner counts over the model's.
    expect(logVerdict({ log_won_by: 'us', won_by: 'them', model_won_by: 'us' })).toBe('disagree')
    expect(hasTrustedLog([open('m', 0, { log_won_by: 'us' })])).toBe(true)
    expect(hasTrustedLog([open('x', 0, { model_won_by: 'us' })])).toBe(false)
  })

  it('restarts the loop just after the rally (and its post-roll), not after a seek away', () => {
    const range = { start_ms: 10_000, end_ms: 15_000 }
    expect(loopRestart(15_400, range)).toBe(false)
    expect(loopRestart(15_500, range)).toBe(true)
    expect(loopRestart(16_900, range)).toBe(true)
    expect(loopRestart(30_000, range)).toBe(false)
    expect(loopRestart(5_000, range)).toBe(false)
  })

  it('formats confidences the Swiss way', () => {
    expect(formatPercent(0.87)).toBe('87%')
    expect(formatPercent(0.9)).toBe('90%')
    expect(formatPercent(null)).toBe('')
  })
})

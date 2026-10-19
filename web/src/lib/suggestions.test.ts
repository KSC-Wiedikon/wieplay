import { describe, expect, it } from 'vitest'
import { period, rally } from '../test/tagging'
import type { Segment } from './types'
import {
  allProgress,
  effectiveServer,
  effectiveWinner,
  factualRallies,
  formatPercent,
  gapProgress,
  hasTrustedLog,
  isGapSuggestion,
  isProposal,
  isReviewedSuggestion,
  isSetSuggestion,
  isSuggestion,
  logCounts,
  logMatches,
  logVerdict,
  loopRestart,
  needsYou,
  needsYouRallies,
  nextOpen,
  reviewItems,
  reviewProgress,
  scoreAfter,
  sureRallies,
  sureSuggestions,
  suggestionCount,
} from './suggestions'

const manual = rally({ id: 'm1', start_ms: 1_000, end_ms: 5_000 })
const open1 = rally({ id: 'a', start_ms: 10_000, end_ms: 15_000, source: 'model', confidence: 0.95 })
const done = rally({ id: 'b', start_ms: 20_000, end_ms: 25_000, source: 'model', review: 'confirmed', confidence: 0.6 })
const open2 = rally({ id: 'c', start_ms: 30_000, end_ms: 35_000, source: 'model', confidence: 0.7 })
const fixed = rally({ id: 'd', start_ms: 40_000, end_ms: 45_000, source: 'model', review: 'corrected', confidence: 0.5 })
const timeout = rally({ id: 't', kind: 'timeout', start_ms: 50_000, end_ms: 80_000, source: 'model' })
const all = [open2, manual, fixed, open1, done, timeout]

describe('suggestions', () => {
  it('tells open suggestions from answered ones and from rallies marked by hand', () => {
    expect(all.filter(isSuggestion).map((s) => s.id)).toEqual(['c', 'a'])
    expect(all.filter(isReviewedSuggestion).map((s) => s.id)).toEqual(['d', 'b'])
    expect(suggestionCount(all)).toBe(2)
    // Only open suggestions are not fact yet (PLAN §10).
    expect(factualRallies(all).map((s) => s.id)).toEqual(['m1', 'b', 'd'])
  })

  it("lists the model's rallies and break labels and the parts split off in this session, in time order", () => {
    expect(reviewItems(all, new Set()).map((s) => s.id)).toEqual(['a', 'b', 'c', 'd', 't'])
    expect(reviewItems(all, new Set(['m1'])).map((s) => s.id)).toEqual(['m1', 'a', 'b', 'c', 'd', 't'])
    // A break marked by hand is no suggestion.
    expect(reviewItems([rally({ id: 'mt', kind: 'timeout', start_ms: 0, end_ms: 30_000 })], new Set())).toEqual([])
  })

  it('tells break labels to review apart from rally suggestions', () => {
    expect(all.filter(isGapSuggestion).map((s) => s.id)).toEqual(['t'])
    expect(all.filter(isProposal).map((s) => s.id)).toEqual(['c', 'a', 't'])
    // Rally counts leave breaks out.
    expect(suggestionCount(all)).toBe(2)
  })

  it('goes to the next open item later in time, then wraps to one skipped earlier', () => {
    const items = reviewItems(all, new Set())
    expect(nextOpen(items, new Set(), null)?.id).toBe('a')
    expect(nextOpen(items, new Set(), open1)?.id).toBe('c')
    expect(nextOpen(items, new Set(), done)?.id).toBe('c')
    expect(nextOpen(items, new Set(), open2)?.id).toBe('t')
    expect(nextOpen(items, new Set(), timeout)?.id).toBe('a')
    expect(nextOpen([done, fixed], new Set(), done)).toBeNull()
    // A split-off part waits like a suggestion.
    expect(nextOpen(reviewItems(all, new Set(['m1'])), new Set(['m1']), timeout)?.id).toBe('m1')
  })

  it('counts progress over every proposal, those rejected earlier included', () => {
    const seen = new Set(['a', 'b', 'c', 'd', 'gone'])
    expect(reviewProgress(all, seen, 3)).toEqual({ done: 6, total: 8 })
    expect(reviewProgress([], new Set(), 0)).toEqual({ done: 0, total: 0 })
  })

  it('counts the break labels apart, and both together for the review', () => {
    const seenGaps = new Set(['t', 'gone'])
    expect(gapProgress(all, seenGaps)).toEqual({ done: 1, total: 2 })
    expect(allProgress(all, new Set(['a', 'b', 'c', 'd']), seenGaps, 1)).toEqual({ done: 4, total: 7 })
  })

  it("shows the coach's facts, else the model's suggestions", () => {
    expect(effectiveServer({ serving: 'us', model_serving: 'them' })).toEqual({ value: 'us', suggested: false })
    expect(effectiveServer({ serving: null, model_serving: 'them' })).toEqual({ value: 'them', suggested: true })
    expect(effectiveServer({ serving: null })).toEqual({ value: null, suggested: false })
    expect(effectiveWinner({ won_by: null, model_won_by: 'us' })).toEqual({ value: 'us', suggested: true })
    expect(effectiveWinner({ won_by: 'them', model_won_by: 'us' })).toEqual({ value: 'them', suggested: false })
    expect(scoreAfter({ score_us_after: 12, score_them_after: 10, score_source: 'model' })).toEqual({ text: '12:10', suggested: true })
    expect(scoreAfter({ score_us_after: 3, score_them_after: 1, score_source: 'tags' })).toEqual({ text: '3:1', suggested: false })
    expect(scoreAfter({ score_us_after: null, score_them_after: null, score_source: null })).toBeNull()
  })

  it('tells a set WiePlay suggested until a coach accepts it', () => {
    expect(isSetSuggestion(period({ no: 1, start_ms: 0, source: 'model', review: 'unreviewed' }))).toBe(true)
    expect(isSetSuggestion(period({ no: 1, start_ms: 0, source: 'model', review: 'confirmed' }))).toBe(false)
    expect(isSetSuggestion(period({ no: 1, start_ms: 0 }))).toBe(false)
  })

  it('picks the open suggestions WiePlay is at least 90% sure of', () => {
    expect(sureSuggestions(all).map((s) => s.id)).toEqual(['a'])
    expect(sureSuggestions(all, 0.7).map((s) => s.id)).toEqual(['a', 'c'])
    expect(sureSuggestions([rally({ start_ms: 0, end_ms: 1, source: 'model', confidence: null })])).toEqual([])
  })

  it('sorts open suggestions by what the scoreboard log says about them', () => {
    const open = (id: string, start_ms: number, patch: Partial<Segment>) => rally({ id, start_ms, end_ms: start_ms + 5_000, source: 'model', ...patch })
    const segments = [
      open('m2', 20_000, { log_won_by: 'them', model_won_by: 'them' }),
      open('m1', 10_000, { log_won_by: 'us', model_won_by: 'us' }),
      open('none', 30_000, { log_won_by: 'none', score_conflict: true, score_note: 'extra' }),
      // The video alone was sure of the other winner: the scoring followed the log, flagged.
      open('video', 40_000, { log_won_by: 'us', model_won_by: 'us', score_conflict: true, score_note: 'log' }),
      open('other', 50_000, { log_won_by: 'us', model_won_by: 'them' }),
      open('missed', 60_000, { log_won_by: 'them', model_won_by: 'them', score_conflict: true, score_note: 'missing_before' }),
      open('silent', 70_000, { model_won_by: 'us' }),
      // Answered already, or a rally marked by hand: not suggestions.
      open('done', 80_000, { log_won_by: 'us', model_won_by: 'us', review: 'confirmed' }),
      rally({ id: 'hand', start_ms: 90_000, end_ms: 95_000, log_won_by: 'us', won_by: 'us' }),
    ]
    expect(logMatches(segments).map((s) => s.id)).toEqual(['m1', 'm2'])
    expect(logCounts(segments)).toEqual({ match: 2, none: 1, disagree: 2, missed: 1, silent: 1 })
    // A coach's winner counts over the model's.
    expect(logVerdict({ log_won_by: 'us', won_by: 'them', model_won_by: 'us' })).toBe('disagree')
    expect(hasTrustedLog(segments)).toBe(true)
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

describe('the joint decode: needs you, sure (docs/PLAN.md §10.1, §10.3)', () => {
  const unsure = { score_sure: false, score_reasons: [{ code: 'log_none' as const }] }
  const segments: Segment[] = [
    rally({ id: 's', start_ms: 10_000, end_ms: 15_000, source: 'model', score_sure: true }),
    rally({ id: 'n', start_ms: 20_000, end_ms: 25_000, source: 'model', ...unsure }),
    // Accepted, but the scoring is still not sure who won: it still needs the coach.
    rally({ id: 'a', start_ms: 30_000, end_ms: 35_000, source: 'model', review: 'confirmed', ...unsure }),
    // The coach said who won: a fact, never asked again.
    rally({ id: 'w', start_ms: 40_000, end_ms: 45_000, source: 'model', review: 'corrected', won_by: 'us', ...unsure }),
    // Not scored (no set yet): neither.
    rally({ id: 'x', start_ms: 50_000, end_ms: 55_000, source: 'model', score_sure: null }),
    rally({ id: 'g', kind: 'timeout', start_ms: 60_000, end_ms: 90_000, source: 'model', score_sure: false }),
  ]

  it('needs you: not sure, no winner from the coach, rallies only', () => {
    expect(segments.filter(needsYou).map((s) => s.id)).toEqual(['n', 'a'])
    expect(needsYouRallies([...segments].reverse()).map((s) => s.id)).toEqual(['n', 'a'])
  })

  it('accept all sure rallies takes the open suggestions the scoring is sure of', () => {
    expect(sureRallies(segments).map((s) => s.id)).toEqual(['s'])
  })

  it('the walk visits what needs the coach first; the sure ones wait for the button', () => {
    const items = reviewItems(segments, new Set())
    expect(nextOpen(items, new Set(), null)?.id).toBe('n')
    expect(nextOpen(items, new Set(), { id: 'n', start_ms: 20_000 })?.id).toBe('x')
    // Only sure ones left open: then those.
    expect(nextOpen(segments.slice(0, 1), new Set(), null)?.id).toBe('s')
  })
})

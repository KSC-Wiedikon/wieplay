import { describe, expect, it } from 'vitest'
import { rally } from '../test/tagging'
import {
  factualRallies,
  formatPercent,
  isReviewedSuggestion,
  isSuggestion,
  loopRestart,
  nextOpen,
  reviewItems,
  reviewProgress,
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

  it("lists the model's rallies and the parts split off in this session, in time order", () => {
    expect(reviewItems(all, new Set()).map((s) => s.id)).toEqual(['a', 'b', 'c', 'd'])
    expect(reviewItems(all, new Set(['m1'])).map((s) => s.id)).toEqual(['m1', 'a', 'b', 'c', 'd'])
  })

  it('goes to the next open item later in time, then wraps to one skipped earlier', () => {
    const items = reviewItems(all, new Set())
    expect(nextOpen(items, new Set(), null)?.id).toBe('a')
    expect(nextOpen(items, new Set(), open1)?.id).toBe('c')
    expect(nextOpen(items, new Set(), done)?.id).toBe('c')
    expect(nextOpen(items, new Set(), open2)?.id).toBe('a')
    expect(nextOpen([done, fixed], new Set(), done)).toBeNull()
    // A split-off part waits like a suggestion.
    expect(nextOpen(reviewItems(all, new Set(['m1'])), new Set(['m1']), fixed)?.id).toBe('m1')
  })

  it('counts progress over every proposal, those rejected earlier included', () => {
    const seen = new Set(['a', 'b', 'c', 'd', 'gone'])
    expect(reviewProgress(all, seen, 3)).toEqual({ done: 6, total: 8 })
    expect(reviewProgress([], new Set(), 0)).toEqual({ done: 0, total: 0 })
  })

  it('picks the open suggestions Nomal is at least 90% sure of', () => {
    expect(sureSuggestions(all).map((s) => s.id)).toEqual(['a'])
    expect(sureSuggestions(all, 0.7).map((s) => s.id)).toEqual(['a', 'c'])
    expect(sureSuggestions([rally({ start_ms: 0, end_ms: 1, source: 'model', confidence: null })])).toEqual([])
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

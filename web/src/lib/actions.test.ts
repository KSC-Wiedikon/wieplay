import { describe, expect, it } from 'vitest'
import { bundle, period, rally, tag } from '../test/tagging'
import {
  acceptChange,
  actionChange,
  actionItems,
  actionProgress,
  factTags,
  isReviewedSuggestion,
  isSuggestedTag,
  nextOpenAction,
  openInRally,
  rejectChange,
  suggestedCount,
} from './actions'
import type { OutboxItem } from './outbox'
import { mergeView } from './taggingStore'
import type { EventIn, Tag } from './types'

const model = (id: string, t_ms: number, patch: Partial<Tag> = {}) =>
  tag({ id, t_ms, code: 'S', number: null, member_id: null, source: 'model', confidence: 0.8, ...patch })

const payload = (change: { payload: unknown }) => change.payload as EventIn & { deleted?: boolean }

describe('suggested actions', () => {
  const items = [
    model('b', 2_000, { code: 'R' }),
    model('a', 1_000),
    tag({ id: 'hand', t_ms: 1_500 }),
    model('c', 3_000, { code: 'E', review: 'confirmed' }),
  ]

  it('tells suggestions from facts', () => {
    expect(items.map(isSuggestedTag)).toEqual([true, true, false, false])
    expect(items.map(isReviewedSuggestion)).toEqual([false, false, false, true])
    expect(factTags(items).map((t) => t.id)).toEqual(['hand', 'c'])
    expect(suggestedCount(items)).toBe(2)
    // Without suggestions the list is the same (a copy).
    const facts = [tag({ id: 'x', t_ms: 1 })]
    expect(factTags(facts)).toEqual(facts)
  })

  it("lists the model's tags in time order and finds the next open one", () => {
    const list = actionItems(items)
    expect(list.map((t) => t.id)).toEqual(['a', 'b', 'c'])
    expect(nextOpenAction(list, null)?.id).toBe('a')
    expect(nextOpenAction(list, list[0] ?? null)?.id).toBe('b')
    // After the last open one: back to the first still open.
    expect(nextOpenAction(list, list[1] ?? null)?.id).toBe('a')
    expect(nextOpenAction([list[2] as Tag], null)).toBeNull()
  })

  it('counts the progress with the ones rejected before', () => {
    expect(actionProgress(items, new Set(['a', 'b', 'c']), 2)).toEqual({ done: 3, total: 5 })
  })

  it('finds the open suggestions of a rally by link or by time', () => {
    const linked = model('l', 9_000, { segment_id: 'r1' })
    const timed = model('t', 5_500)
    const other = model('o', 20_000)
    expect(openInRally([linked, timed, other], { id: 'r1', start_ms: 5_000, end_ms: 10_000 }).map((t) => t.id)).toEqual(['l', 't'])
    expect(openInRally([linked], null)).toEqual([])
  })

  it('corrects on another skill, side, grade or time; a player named is no correction', () => {
    const s = model('s', 1_000)
    expect(payload(actionChange(s, { code: 'R' })).review).toBe('corrected')
    expect(payload(actionChange(s, { side: 'them' })).review).toBe('corrected')
    expect(payload(actionChange(s, { attributes: { grade: '#' } })).review).toBe('corrected')
    expect(payload(actionChange(s, { t_ms: 1_020, time_precision: 'frame' })).review).toBe('corrected')
    expect(payload(acceptChange(s, { number: 7 }))).toMatchObject({ review: 'confirmed', number: 7, code: 'S' })
    // A corrected one stays corrected when accepted.
    expect(payload(acceptChange({ ...s, review: 'corrected' })).review).toBe('corrected')
    // A manual tag keeps what it is.
    expect(payload(actionChange(tag({ t_ms: 5 }), { code: 'R' })).review).toBe('unreviewed')
  })

  it('rejects by deleting with review "rejected"', () => {
    expect(payload(rejectChange(model('s', 1_000)))).toMatchObject({ id: 's', review: 'rejected', deleted: true })
    expect(payload(rejectChange(tag({ id: 'h', t_ms: 1 })))).toMatchObject({ review: 'unreviewed', deleted: true })
  })
})

describe('the score from facts only', () => {
  it('a suggested serve names no server; an accepted one does', () => {
    const set1 = period({ no: 1, start_ms: 0 })
    const r = rally({ id: 'r', start_ms: 10_000, end_ms: 18_000, period_id: set1.id })
    const serve = model('sv', 11_000, { side: 'them', segment_id: 'r' })
    const data = bundle({ periods: [set1], segments: [r], events: [serve] })
    const base = {
      events: new Map(data.events.map((e) => [e.id, e])),
      segments: new Map(data.segments.map((x) => [x.id, x])),
      periods: new Map(data.periods.map((p) => [p.id, p])),
    }
    // Something queued: the score is computed here (provisional).
    const nudge: OutboxItem = {
      key: 'v:segment:r',
      videoId: 'v',
      id: 'r',
      seq: 1,
      queuedAt: 0,
      kind: 'segment',
      payload: { id: 'r', base_version: 1, kind: 'rally', start_ms: 10_000, end_ms: 18_100 },
    }
    const open = mergeView(base, [nudge], data.roster, data.taxonomy)
    expect(open.provisional).toBe(true)
    expect(open.segments[0]?.serving).toBeNull()
    const accepted = mergeView({ ...base, events: new Map([['sv', { ...serve, review: 'confirmed' as const }]]) }, [nudge], data.roster, data.taxonomy)
    expect(accepted.segments[0]?.serving).toBe('them')
  })
})

import '../i18n/pages'
import { describe, expect, it } from 'vitest'
import i18n from '../i18n'
import { bundle, period, rally, tag } from '../test/tagging'
import {
  acceptChange,
  actionChange,
  actionItems,
  actionProgress,
  alternativeOf,
  countedTags,
  gateText,
  isAutoTag,
  isReviewedSuggestion,
  isToCheckTag,
  isWiePlaysTag,
  measuredText,
  nextOpenAction,
  openInRally,
  personTags,
  rejectChange,
  toCheckActions,
  toCheckTagCount,
} from './actions'
import type { OutboxItem } from './outbox'
import { mergeView } from './taggingStore'
import type { ActionGate, EventIn, Tag } from './types'

const model = (id: string, t_ms: number, patch: Partial<Tag> = {}) =>
  tag({ id, t_ms, code: 'S', number: null, member_id: null, source: 'model', review: 'check', confidence: 0.8, ...patch })

const payload = (change: { payload: unknown }) => change.payload as EventIn & { deleted?: boolean }

describe("WiePlay's actions", () => {
  const items = [
    model('b', 2_000, { code: 'R' }),
    model('a', 1_000),
    tag({ id: 'hand', t_ms: 1_500 }),
    model('c', 3_000, { code: 'E', review: 'confirmed' }),
    model('s', 3_500, { code: 'A', review: 'auto', model_sure: true }),
  ]

  it('counts every tag; tells sure ones, ones to check and a person’s apart', () => {
    expect(items.map(isToCheckTag)).toEqual([true, true, false, false, false])
    expect(items.map(isAutoTag)).toEqual([false, false, false, false, true])
    expect(items.map(isWiePlaysTag)).toEqual([true, true, false, false, true])
    expect(items.map(isReviewedSuggestion)).toEqual([false, false, false, true, false])
    expect(toCheckTagCount(items)).toBe(2)
    // Every tag counts; only a rejected one does not.
    expect(countedTags(items).map((t) => t.id)).toEqual(['b', 'a', 'hand', 'c', 's'])
    expect(countedTags([...items, model('x', 9, { review: 'rejected' })]).length).toBe(5)
    // A person's: made by hand or answered (the score's serve facts).
    expect(personTags(items).map((t) => t.id)).toEqual(['hand', 'c'])
    // Without WiePlay's the list is the same (a copy).
    const facts = [tag({ id: 'x', t_ms: 1 })]
    expect(personTags(facts)).toEqual(facts)
    expect(toCheckActions(items).map((t) => t.id)).toEqual(['a', 'b'])
  })

  it("lists the model's tags in time order and finds the next one to check", () => {
    const list = actionItems(items)
    expect(list.map((t) => t.id)).toEqual(['a', 'b', 'c', 's'])
    expect(nextOpenAction(list, null)?.id).toBe('a')
    expect(nextOpenAction(list, list[0] ?? null)?.id).toBe('b')
    // After the last open one: back to the first still open.
    expect(nextOpenAction(list, list[1] ?? null)?.id).toBe('a')
    expect(nextOpenAction([list[2] as Tag], null)).toBeNull()
  })

  it('counts the progress with the ones rejected before', () => {
    expect(actionProgress(items, new Set(['a', 'b', 'c']), 2)).toEqual({ done: 3, total: 5 })
  })

  it('finds the actions to check of a rally by link or by time', () => {
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

describe("the score's facts: a person's serves", () => {
  it("WiePlay's serve names no server (it comes out of the scoring); an accepted one does", () => {
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

describe('whether suggestions are written, and what is measured', () => {
  const gate = (patch: Partial<ActionGate>): ActionGate => ({
    mode: 'auto', open: true, reason: 'auto', finder: 'v0', games: 0, precision: null,
    contact_recall: null, trained_at: null,
    measured: { rallies: 61, contacts: 300, found: 0.3, precision: 0.58, sure: 40, sure_wrong: 0 }, ...patch,
  })
  const t = i18n.getFixedT('en')

  it('says only that they are switched off', () => {
    expect(gateText(t, gate({}))).toBeNull()
    expect(gateText(t, gate({ mode: 'off', open: false, reason: 'off' }))).toBe('WiePlay’s action tagging is switched off.')
    expect(gateText(t, null)).toBeNull()
  })

  it('shows the measured numbers as they are', () => {
    expect(measuredText(t, gate({}))).toBe(
      'Measured on 61 hand-checked rallies: 58% of WiePlay’s actions are fully right (time, skill and side) and 30% of the contacts get one. Of 40 sure ones, 0 were wrong.',
    )
    expect(measuredText(i18n.getFixedT('de'), gate({}))).toMatch(/^Gemessen an 61 von Hand geprüften Ballwechseln: 58 ?% der Aktionen von WiePlay/)
    expect(measuredText(t, gate({ measured: {} }))).toBeNull()
  })
})

describe('sure actions', () => {
  const s = tag({ id: 's', t_ms: 1000, code: 'S', source: 'model', review: 'auto', model_sure: true })
  const r = tag({ id: 'r', t_ms: 2000, code: 'R', source: 'model', review: 'check', model_sure: false, model_alternative: 'D:them' })
  const e = tag({ id: 'e', t_ms: 3000, code: 'E', source: 'model', review: 'check', model_alternative: 'none' })
  const done = tag({ id: 'd', t_ms: 4000, code: 'A', source: 'model', model_sure: true, review: 'confirmed' })

  it('are not in the walk: it visits only those to check', () => {
    const tags = [s, r, e, done]
    expect(toCheckActions(tags).map((x) => x.id)).toEqual(['r', 'e'])
    expect(nextOpenAction(actionItems(tags), null)?.id).toBe('r')
    expect(nextOpenAction(actionItems(tags), e)?.id).toBe('r')
    expect(nextOpenAction(actionItems([s, done]), null)).toBeNull()
  })

  it("reads the model's other answer", () => {
    expect(alternativeOf(r)).toEqual({ code: 'D', side: 'them' })
    expect(alternativeOf(e)).toBe('none')
    expect(alternativeOf(s)).toBeNull()
  })
})

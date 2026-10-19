import { describe, expect, it } from 'vitest'
import { emptySet, withBox, type SheetSetDoc } from './scoresheet'
import {
  altsText,
  canConfirm,
  changedFields,
  draftSummary,
  markOf,
  nextCheck,
  remaining,
  sureFields,
  timeoutKey,
  type ReadCell,
  type SheetReading,
} from './sheetRead'

const sure = (v: unknown): ReadCell => ({ v, alts: [[v, 0.99]], check: false })
const doubt = (v: unknown, ...others: [unknown, number][]): ReadCell => ({ v, alts: [[v, 0.6], ...others], check: true, why: 'unsure' })

/** A draft as the server writes it: two doubtful boxes, sure lineups and boxes, a timeout. */
const cells: Record<string, ReadCell> = {
  first_serve: { v: 'us', alts: [], check: false },
  'lineup.us.1': sure(7),
  'lineup.us.2': sure(12),
  'box.us.2.1': doubt(15, [18, 0.3]),
  'box.us.1.1': sure(3),
  'box.them.3.1': doubt(4, [9, 0.35]),
  'to.them.1': sure([12, 15]),
}

function reading(sets: Record<string, Record<string, ReadCell>>): SheetReading {
  return {
    job: { status: 'succeeded', error: null, created_at: null },
    at: '2026-10-19T12:00:00+02:00',
    worker: 'lenovo',
    model: 'wieplay-sheet-cells v1',
    us_letter: 'B',
    error: null,
    to_check: 0,
    drafts: Object.keys(sets).map(Number),
    sets: Object.fromEntries(Object.entries(sets).map(([no, c]) => [no, { state: 'draft' as const, cells: c, decoded: true }])),
  }
}

describe('checking a sheet read from the photo', () => {
  it('lists the doubtful cells first, in the form’s order, and leaves out what was looked at', () => {
    const left = remaining(cells, new Set())
    expect(left.check).toEqual(['box.us.2.1', 'box.them.3.1'])
    expect(left.sure).toEqual(['lineup.us.1', 'lineup.us.2', 'box.us.1.1', 'to.them.1'])
    expect(remaining(cells, new Set(['box.us.2.1'])).check).toEqual(['box.them.3.1'])
  })

  it('marks doubtful and sure cells until they are accepted', () => {
    expect(markOf(cells, new Set(), 'box.us.2.1')).toBe('check')
    expect(markOf(cells, new Set(), 'lineup.us.1')).toBe('read')
    expect(markOf(cells, new Set(['lineup.us.1']), 'lineup.us.1')).toBeNull()
    expect(markOf(cells, new Set(), 'box.us.5.2')).toBeNull() // typed, or not read
  })

  it('confirms a set only once every proposed cell is looked at', () => {
    const accepted = new Set<string>()
    expect(canConfirm(cells, accepted)).toBe(false)
    for (const key of sureFields(cells)) accepted.add(key) // "Accept the sure cells"
    expect(sureFields(cells)).not.toContain('first_serve')
    expect(canConfirm(cells, accepted)).toBe(false) // two doubtful ones left
    accepted.add('box.us.2.1') // "It is right"
    accepted.add('box.them.3.1')
    expect(canConfirm(cells, accepted)).toBe(true)
  })

  it('counts what an edit changed as looked at', () => {
    const before: SheetSetDoc = { ...emptySet(1), lineup_us: [7, 12, null, null, null, null] }
    let after = withBox(before, 'us', 2, 1, 18)
    after = { ...after, lineup_us: [7, 13, null, null, null, null] }
    after = { ...after, timeouts: [{ side: 'them', own: 12, opp: 16 }] }
    after = { ...after, subs: [{ side: 'us', position: 4, number: 9, at: [10, 8], back: null }], start: '20:00' }
    expect(changedFields(before, after).sort()).toEqual(
      ['box.us.2.1', 'lineup.us.2', 'start', 'sub.us.4.at', 'sub.us.4.number', 'to.them.1'].sort(),
    )
    expect(changedFields(after, after)).toEqual([])
  })

  it('counts the cells to check over the drafts and goes to the next one, set by set', () => {
    const r = reading({ '1': cells, '2': { 'box.us.1.2': doubt(20) } })
    expect(draftSummary(r, {})).toEqual({ sets: [1, 2], check: 3 })
    expect(nextCheck(r, {}, 1)).toEqual([1, 'box.us.2.1'])
    const accepted = { 1: new Set(['box.us.2.1', 'box.them.3.1']) }
    expect(draftSummary(r, accepted).check).toBe(1)
    expect(nextCheck(r, accepted, 1)).toEqual([2, 'box.us.1.2'])
    // From set 2 on, then round to set 1.
    expect(nextCheck(r, {}, 2)).toEqual([2, 'box.us.1.2'])
    expect(nextCheck(r, { ...accepted, 2: new Set(['box.us.1.2']) }, 1)).toBeNull()
  })

  it('finds a timeout’s read field by its score, and says what was read', () => {
    expect(timeoutKey(cells, 'them', 1, 12, 15)).toBe('to.them.1')
    expect(timeoutKey(cells, 'them', 1, null, null)).toBe('to.them.2')
    expect(altsText(cells['box.us.2.1'])).toBe('15 (18?)')
    expect(altsText(cells['to.them.1'])).toBe('12:15')
  })
})

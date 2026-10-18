import { describe, expect, it } from 'vitest'
import cases from './scoresheet.fixture.json'
import { applyRepair, boxName, cells, decode, emptySet, firstIssue, indexOf, normalised, valueAt, withBox, type SheetSetDoc } from './scoresheet'

// The fixture is written by the backend's tests (backend/tests/test_scoresheet.py) from its own
// decoder: both must read every box the same way.
interface Case {
  name: string
  set: SheetSetDoc
  official: [number, number] | null
  expect: {
    winners: string
    penalties: number[]
    final: [number, number]
    complete: boolean
    consistent: boolean
    issues: [string, string, { side: string; position: number; box: number } | null][]
  }
}

describe('the scoresheet decoder', () => {
  it.each((cases as unknown as Case[]).map((c) => [c.name, c] as const))('reads %s as the server does', (_, c) => {
    const d = decode(normalised(c.set), c.official)
    expect(d.points.map((p) => (p.winner === 'us' ? 'U' : 'T')).join('')).toBe(c.expect.winners)
    expect(d.points.flatMap((p, k) => (p.penalty ? [k] : []))).toEqual(c.expect.penalties)
    expect(d.final).toEqual(c.expect.final)
    expect(d.complete).toBe(c.expect.complete)
    expect(d.consistent).toBe(c.expect.consistent)
    expect(d.issues.map((i) => [i.code, i.severity, i.box])).toEqual(c.expect.issues)
  })

  it('types in the sheet’s order: box 1 of every position, then box 2', () => {
    expect(cells(true, false).slice(0, 7)).toEqual([[1, 1], [2, 1], [3, 1], [4, 1], [5, 1], [6, 1], [1, 2]])
    expect(cells(false, false)[0]).toEqual([2, 1]) // the X in position I box 1
    expect(cells(true, true)).toHaveLength(36)
  })

  it('grows the score as boxes are typed', () => {
    let set: SheetSetDoc = { ...emptySet(1), first_serve: 'them' }
    set = withBox(set, 'them', 1, 1, 2)
    let d = decode(set)
    expect(d.final).toEqual([0, 2]) // them 2 break points; every box typed so far is read
    expect(firstIssue(d.issues)).toMatchObject({ code: 'not_over', severity: 'incomplete' })
    set = withBox(set, 'us', 2, 1, 0) // lower than our score (1): an error at once
    d = decode(set)
    expect(firstIssue(d.issues)).toMatchObject({ code: 'low', severity: 'error', box: { side: 'us', position: 2, box: 1 } })
    expect(indexOf(d.points, 0, 0)).toBe(0)
    expect(boxName({ position: 4, box: 2 })).toBe('IV/2')
  })

  it('applies the server’s repairs: a change, an insert that moves the boxes on, a delete', () => {
    let set: SheetSetDoc = { ...emptySet(1), first_serve: 'us' }
    set = withBox(withBox(withBox(set, 'us', 1, 1, 2), 'us', 2, 1, 5), 'us', 3, 1, 9)
    const changed = applyRepair(set, { kind: 'change', box: { side: 'us', position: 2, box: 1 }, new: 6 })
    expect(valueAt(changed, 'us', 2, 1)).toBe(6)
    const inserted = applyRepair(set, { kind: 'insert', box: { side: 'us', position: 2, box: 1 }, new: 4 })
    expect([1, 2, 3, 4].map((p) => valueAt(inserted, 'us', p, 1))).toEqual([2, 4, 5, 9])
    const deleted = applyRepair(set, { kind: 'delete', box: { side: 'us', position: 2, box: 1 }, new: null })
    expect([1, 2, 3].map((p) => valueAt(deleted, 'us', p, 1))).toEqual([2, 9, null])
  })
})

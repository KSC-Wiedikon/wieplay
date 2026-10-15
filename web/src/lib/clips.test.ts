import { describe, expect, it } from 'vitest'
import { clipProblem, clipRange, draftOf, newestFirst, nextInQueue, peopleOf, rangeStep } from './clips'
import type { Clip } from './types'
import { roster } from '../test/tagging'

const clip = (patch: Partial<Clip>): Clip => ({
  id: 'c1',
  video_id: 'v1',
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  start_ms: 10_000,
  end_ms: 15_000,
  title: 'Kill',
  note: null,
  event_id: null,
  owner: 7,
  mine: true,
  visibility: 'private',
  created_at: '2026-10-12T19:00:00Z',
  ...patch,
})

describe('clipRange', () => {
  it('uses both marks, in either order', () => {
    expect(clipRange(30_000, 36_000, 0)).toEqual({ start_ms: 30_000, end_ms: 36_000 })
    expect(clipRange(36_000, 30_000, 0)).toEqual({ start_ms: 30_000, end_ms: 36_000 })
  })

  it('takes the playhead for a missing end, or 10 s', () => {
    expect(clipRange(30_000, null, 34_000)).toEqual({ start_ms: 30_000, end_ms: 34_000 })
    expect(clipRange(30_000, null, 20_000)).toEqual({ start_ms: 30_000, end_ms: 40_000 })
    expect(clipRange(null, 30_000, 25_000)).toEqual({ start_ms: 25_000, end_ms: 30_000 })
    expect(clipRange(null, 5_000, 25_000)).toEqual({ start_ms: 0, end_ms: 5_000 })
  })

  it('stays inside the video and needs a mark', () => {
    expect(clipRange(175_000, null, 0, 180_000)).toEqual({ start_ms: 175_000, end_ms: 180_000 })
    expect(clipRange(180_000, null, 0, 180_000)).toBeNull()
    expect(clipRange(null, null, 12_000)).toBeNull()
  })
})

it('flags clips that end before they start or run over 10 minutes', () => {
  expect(clipProblem({ start_ms: 5_000, end_ms: 5_000 })).toBe('order')
  expect(clipProblem({ start_ms: 0, end_ms: 600_000 })).toBeNull()
  expect(clipProblem({ start_ms: 0, end_ms: 600_001 })).toBe('tooLong')
})

describe('rangeStep', () => {
  const range = { start_ms: 10_000, end_ms: 15_000 }
  it('plays inside, ends at the end, and is left by a seek far outside', () => {
    expect(rangeStep(10_000, range)).toBe('play')
    expect(rangeStep(14_990, range)).toBe('play')
    expect(rangeStep(15_000, range)).toBe('end')
    expect(rangeStep(15_400, range)).toBe('end')
    expect(rangeStep(17_000, range)).toBe('outside')
    expect(rangeStep(8_000, range)).toBe('outside')
  })
})

it('moves a "play all" queue on, and ends it after the last clip', () => {
  const ranges = [clip({ id: 'a' }), clip({ id: 'b' })]
  expect(nextInQueue({ ranges, index: 0 })).toEqual({ ranges, index: 1 })
  expect(nextInQueue({ ranges, index: 1 })).toBeNull()
})

it('sorts my clips newest first', () => {
  const list = [
    clip({ id: 'old', created_at: '2026-10-01T10:00:00Z' }),
    clip({ id: 'new', created_at: '2026-10-14T10:00:00Z' }),
    clip({ id: 'mid', created_at: '2026-10-05T10:00:00Z' }),
  ]
  expect(newestFirst(list).map((c) => c.id)).toEqual(['new', 'mid', 'old'])
})

it('makes a draft of a saved clip', () => {
  expect(draftOf(clip({ note: 'Arm high', visibility: 'team' }))).toEqual({
    id: 'c1',
    video_id: 'v1',
    start_ms: 10_000,
    end_ms: 15_000,
    title: 'Kill',
    note: 'Arm high',
    event_id: null,
    visibility: 'team',
  })
})

it('lists the roster players with a member and a name, by name', () => {
  expect(peopleOf(roster)).toEqual([
    { member_id: 107, name: 'Luca Brunner', number: 7 },
    { member_id: 103, name: 'Mia Keller', number: 3 },
    { member_id: 111, name: 'Noah Meier', number: 11 },
  ])
})

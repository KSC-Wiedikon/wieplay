import { describe, expect, it } from 'vitest'
import { period, rally, tag } from '../test/tagging'
import { filterRallies, filterTags, nextPlayback, NO_FILTER, playLength, playWindows, rallyAt, tagInRally } from './condensed'

describe('playWindows', () => {
  it('adds 2 s before and 1.5 s after each rally', () => {
    expect(playWindows([{ start_ms: 10_000, end_ms: 20_000 }])).toEqual([{ start_ms: 8_000, end_ms: 21_500 }])
  })

  it('merges rallies whose windows overlap or touch', () => {
    const windows = playWindows([
      { start_ms: 30_000, end_ms: 40_000 },
      { start_ms: 10_000, end_ms: 20_000 },
      { start_ms: 23_000, end_ms: 25_000 }, // 21.5 s ≥ 21 s: overlaps the first
      { start_ms: 41_500, end_ms: 50_000 }, // starts at 39.5 s, inside the previous one
    ])
    expect(windows).toEqual([
      { start_ms: 8_000, end_ms: 26_500 },
      { start_ms: 28_000, end_ms: 51_500 },
    ])
  })

  it('clamps to the start and the end of the video', () => {
    expect(playWindows([{ start_ms: 1_000, end_ms: 5_000 }, { start_ms: 90_000, end_ms: 99_500 }], { duration_ms: 100_000 })).toEqual([
      { start_ms: 0, end_ms: 6_500 },
      { start_ms: 88_000, end_ms: 100_000 },
    ])
  })

  it('takes other pre- and post-roll and adds up the length', () => {
    const windows = playWindows([{ start_ms: 10_000, end_ms: 20_000 }], { pre: 0, post: 0 })
    expect(windows).toEqual([{ start_ms: 10_000, end_ms: 20_000 }])
    expect(playLength(playWindows([{ start_ms: 10_000, end_ms: 20_000 }, { start_ms: 60_000, end_ms: 70_000 }]))).toBe(27_000)
  })

  it('is empty without rallies', () => {
    expect(playWindows([])).toEqual([])
  })
})

describe('nextPlayback', () => {
  const windows = [
    { start_ms: 8_000, end_ms: 21_500 },
    { start_ms: 58_000, end_ms: 71_500 },
  ]

  it('plays inside a window', () => {
    expect(nextPlayback(8_000, windows)).toEqual({ kind: 'play' })
    expect(nextPlayback(21_000, windows)).toEqual({ kind: 'play' })
    expect(nextPlayback(60_000, windows)).toEqual({ kind: 'play' })
  })

  it('jumps over dead time to the next window', () => {
    expect(nextPlayback(0, windows)).toEqual({ kind: 'seek', to_ms: 8_000 })
    expect(nextPlayback(21_500, windows)).toEqual({ kind: 'seek', to_ms: 58_000 })
    expect(nextPlayback(40_000, windows)).toEqual({ kind: 'seek', to_ms: 58_000 })
  })

  it('treats a position just before a window as inside it (seeks are not exact)', () => {
    expect(nextPlayback(57_900, windows)).toEqual({ kind: 'play' })
  })

  it('stops after the last window', () => {
    expect(nextPlayback(71_500, windows)).toEqual({ kind: 'end' })
    expect(nextPlayback(90_000, [])).toEqual({ kind: 'end' })
  })
})

describe('rally filters', () => {
  const set1 = period({ no: 1, start_ms: 0, end_ms: 100_000 })
  const set2 = period({ no: 2, start_ms: 100_000 })
  const r1 = rally({ start_ms: 10_000, end_ms: 20_000, period_id: set1.id })
  const r2 = rally({ start_ms: 30_000, end_ms: 40_000, period_id: set1.id })
  const r3 = rally({ start_ms: 110_000, end_ms: 120_000, period_id: null }) // not rescored yet
  const tags = [
    tag({ t_ms: 9_000, code: 'S', side: 'them', number: 12, attributes: { grade: '-' } }), // before r1, within the serve slack
    tag({ t_ms: 15_000, code: 'A', side: 'us', number: 7, attributes: { grade: '#' } }),
    tag({ t_ms: 35_000, code: 'A', side: 'us', number: 3, attributes: { grade: '=' } }),
    tag({ t_ms: 50_000, code: 'A', side: 'us', number: 7, attributes: { grade: '#' } }), // between rallies
    tag({ t_ms: 200_000, code: 'B', side: 'us', number: 7, attributes: { grade: '#' }, segment_id: r3.id }),
  ]
  const all = [r1, r2, r3]

  it('keeps everything without a filter', () => {
    expect(filterRallies(all, tags, [set1, set2], NO_FILTER)).toEqual(all)
  })

  it('narrows to rallies with a matching tag', () => {
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, player: 'us:7' })).toEqual([r1, r3])
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, player: 'us:7', skill: 'A' })).toEqual([r1])
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, grade: '=' })).toEqual([r2])
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, player: 'them:12' })).toEqual([r1])
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, skill: 'D' })).toEqual([])
  })

  it('narrows to a set, by the server period or by time', () => {
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, set: set2.id })).toEqual([r3])
    expect(filterRallies(all, tags, [set1, set2], { ...NO_FILTER, set: set1.id, player: 'us:3' })).toEqual([r2])
  })

  it('places tags by their rally id first, then by time', () => {
    expect(tagInRally(tags[4] as never, r3)).toBe(true)
    expect(tagInRally({ t_ms: 15_000, segment_id: r3.id }, r1)).toBe(false)
    expect(tagInRally({ t_ms: 15_000, segment_id: null }, r1)).toBe(true)
  })

  it('filters tags by set too', () => {
    expect(filterTags(tags, [set1, set2], { ...NO_FILTER, set: set2.id }).map((t) => t.t_ms)).toEqual([200_000])
    expect(filterTags(tags, [set1, set2], { ...NO_FILTER, skill: 'A', grade: '#' }).map((t) => t.t_ms)).toEqual([15_000, 50_000])
  })

  it('finds the rally playing now', () => {
    expect(rallyAt(all, 9_000)).toBe(r1)
    expect(rallyAt(all, 21_000)).toBe(r1)
    expect(rallyAt(all, 25_000)).toBeNull()
  })
})

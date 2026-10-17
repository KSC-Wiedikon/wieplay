import { describe, expect, it } from 'vitest'
import { statColumns } from '../test/stats'
import { period, rally, tag, volleyball } from '../test/tagging'
import {
  columnGroups,
  columnHas,
  formatStat,
  rallyWindow,
  reelLink,
  rotationRallies,
  rotationTags,
  rowHas,
  setterPlays,
  setWindow,
  statRallies,
  statTags,
  tagRallies,
  tagSets,
  tagWindow,
  teamCsvUrl,
  videoCsvUrl,
  type SetPlay,
  type StatColumn,
} from './stats'

const column = (key: string): StatColumn => {
  const found = statColumns.find((c) => c.key === key)
  if (!found) throw new Error(key)
  return found
}

describe('formatStat', () => {
  it('writes Swiss numbers and a dash for nothing', () => {
    expect(formatStat(1 / 3, 'pct')).toBe('33.3%')
    expect(formatStat(-0.0625, 'pct')).toBe('-6.3%')
    expect(formatStat(0, 'pct')).toBe('0.0%')
    expect(formatStat(10 / 7, 'rating')).toBe('1.43')
    expect(formatStat(1234, 'count')).toMatch(/^1['’]234$/) // the apostrophe depends on the ICU data
    expect(formatStat(0, 'count')).toBe('0')
    expect(formatStat(null, 'pct')).toBe('–')
    expect(formatStat(undefined, 'count')).toBe('–')
  })
})

describe('columnGroups', () => {
  it('keeps the columns in order, a lone column on its own', () => {
    const groups = columnGroups(statColumns)
    expect(groups.map((g) => [g.group, g.columns.length])).toEqual([
      [null, 1],
      ['serve', 6],
      ['reception', 5],
      ['attack', 7],
      ['block', 3],
      ['other', 3],
    ])
  })
})

describe('which tags a number is made of', () => {
  it('matches a row: a present key must match, null meaning none', () => {
    const seven = tag({ t_ms: 0, number: 7, member_id: 107 })
    const noNumber = tag({ t_ms: 0, number: null, member_id: null })
    const theirs = tag({ t_ms: 0, side: 'them', number: 12, member_id: null })
    expect(rowHas(seven, { side: 'us', number: 7 })).toBe(true)
    expect(rowHas(seven, { side: 'us', number: 3 })).toBe(false)
    expect(rowHas(seven, { side: 'us' })).toBe(true)
    expect(rowHas(noNumber, { side: 'us', number: null })).toBe(true)
    expect(rowHas(seven, { side: 'us', number: null })).toBe(false)
    expect(rowHas(seven, { side: 'us', member_id: 107 })).toBe(true)
    expect(rowHas(noNumber, { side: 'us', member_id: null })).toBe(true)
    expect(rowHas(theirs, { side: 'them' })).toBe(true)
    expect(rowHas(theirs, { side: 'us' })).toBe(false)
  })

  it('matches a column by skill and grade; null in the grades is an ungraded tag', () => {
    expect(columnHas(tag({ t_ms: 0, code: 'A', attributes: { grade: '#' } }), column('att_kill'))).toBe(true)
    expect(columnHas(tag({ t_ms: 0, code: 'A', attributes: { grade: '=' } }), column('att_kill'))).toBe(false)
    expect(columnHas(tag({ t_ms: 0, code: 'S', attributes: { grade: '#' } }), column('att_kill'))).toBe(false)
    expect(columnHas(tag({ t_ms: 0, code: 'S', attributes: {} }), column('serve_in'))).toBe(true)
    expect(columnHas(tag({ t_ms: 0, code: 'S', attributes: { grade: '=' } }), column('serve_in'))).toBe(false)
    expect(columnHas(tag({ t_ms: 0, code: 'A', attributes: {} }), column('att_ta'))).toBe(true)
    // Points: kills, aces and block points.
    for (const code of ['A', 'S', 'B']) expect(columnHas(tag({ t_ms: 0, code, attributes: { grade: '#' } }), column('points'))).toBe(true)
  })

  it('puts a tag in the set of its rally, like the server', () => {
    const s1 = period({ no: 1, start_ms: 0, end_ms: 100_000 })
    const s2 = period({ no: 2, start_ms: 100_000 })
    const ra = rally({ start_ms: 10_000, end_ms: 20_000, period_id: s1.id })
    const rb = rally({ start_ms: 100_500, end_ms: 110_000, period_id: s2.id })
    const tags = [
      tag({ t_ms: 15_000, segment_id: ra.id }),
      tag({ t_ms: 99_000 }), // the toss, 1.5 s before rally B: set 2
      tag({ t_ms: 50_000 }), // between rallies: the set running then
      tag({ t_ms: 96_000 }), // 4.5 s before rally B: still set 1
      tag({ t_ms: 50_000, segment_id: 'unknown' }), // a rally we don't have: by time
    ]
    const sets = tagSets(tags, [ra, rb], [s2, s1])
    expect(tags.map((t) => sets.get(t.id))).toEqual([s1.id, s2.id, s1.id, s1.id, s1.id])
  })

  it('finds the tags behind a number, per set when asked, in time order', () => {
    const s1 = period({ no: 1, start_ms: 0, end_ms: 60_000 })
    const s2 = period({ no: 2, start_ms: 60_000 })
    const late = tag({ t_ms: 70_000, code: 'A', number: 7, attributes: { grade: '#' } })
    const early = tag({ t_ms: 5_000, code: 'A', number: 7, attributes: { grade: '#' } })
    const events = [late, early, tag({ t_ms: 6_000, code: 'A', number: 3, member_id: 103, attributes: { grade: '#' } })]
    const source = { events, segments: [], periods: [s1, s2] }
    expect(statTags(source, { side: 'us', number: 7 }, column('att_kill'), null)).toEqual([early, late])
    expect(statTags(source, { side: 'us', number: 7 }, column('att_kill'), 2)).toEqual([late])
    expect(statTags(source, { side: 'us' }, column('att_kill'), 1)).toHaveLength(2)
    expect(statTags(source, { side: 'us', number: 7 }, column('att_err'), null)).toEqual([])
  })

  it('finds the rallies behind a team number', () => {
    const set1 = period({ no: 1, start_ms: 0 })
    const won = rally({ start_ms: 1_000, end_ms: 2_000, serving: 'them', won_by: 'us', period_id: set1.id })
    const lost = rally({ start_ms: 3_000, end_ms: 4_000, serving: 'them', won_by: 'them', period_id: set1.id })
    const open = rally({ start_ms: 5_000, end_ms: 6_000, serving: 'them', won_by: null, period_id: set1.id })
    const served = rally({ start_ms: 7_000, end_ms: 8_000, serving: 'us', won_by: 'us', period_id: null })
    const segments = [served, open, lost, won]
    expect(statRallies(segments, { serving: 'them', won_by: 'us' }, null)).toEqual([won])
    expect(statRallies(segments, { serving: 'them', won_by: 'any' }, null)).toEqual([won, lost])
    expect(statRallies(segments, { won_by: 'us' }, set1.id)).toEqual([won])
    expect(statRallies(segments, { won_by: 'us' }, null)).toEqual([won, served])
  })
})

describe('windows', () => {
  it("plays a tag with its skill's window, or its own", () => {
    expect(tagWindow(tag({ t_ms: 10_000, code: 'A' }), volleyball, null)).toEqual({ start_ms: 7_000, end_ms: 12_000 })
    expect(tagWindow(tag({ t_ms: 1_000, code: 'S' }), volleyball, 2_500)).toEqual({ start_ms: 0, end_ms: 2_500 })
    expect(tagWindow(tag({ t_ms: 10_000, code: 'A', clip_pre_ms: 500, clip_post_ms: 800 }), volleyball, null)).toEqual({
      start_ms: 9_500,
      end_ms: 10_800,
    })
  })

  it('plays a rally like condensed playback', () => {
    expect(rallyWindow(rally({ start_ms: 10_000, end_ms: 20_000 }), null)).toEqual({ start_ms: 8_000, end_ms: 21_500 })
  })
})

describe('reelLink', () => {
  const team = { id: 92, season: '2026/27' }
  it('opens the reel builder with the season, the player and the column', () => {
    const url = new URL(reelLink(team, { side: 'us', member_id: 107 }, column('serve_in')) ?? '', 'http://x')
    expect(url.pathname).toBe('/reels/new')
    expect(Object.fromEntries(url.searchParams)).toEqual({ team: '92', season: '2026/27', member: '107', codes: 'S', grades: '+,!,-,/,none' })
  })

  it('names a side for the team and the opponent; any grade means no grades', () => {
    const url = new URL(reelLink(team, { side: 'them' }, column('att_ta')) ?? '', 'http://x')
    expect(url.searchParams.get('side')).toBe('them')
    expect(url.searchParams.get('grades')).toBeNull()
    expect(url.searchParams.get('codes')).toBe('A')
  })

  it("can't name a shirt number or a missing member", () => {
    expect(reelLink(team, { side: 'us', member_id: null }, column('att_ta'))).toBeNull()
    expect(reelLink(team, { side: 'us', number: 7 }, column('att_ta'))).toBeNull()
  })
})

describe('rotations', () => {
  const r1 = rally({ start_ms: 10_000, end_ms: 20_000, serving: 'them', won_by: 'us' })
  const r2 = rally({ start_ms: 30_000, end_ms: 40_000, serving: 'us', won_by: null })
  const r3 = rally({ start_ms: 50_000, end_ms: 60_000, serving: 'us', won_by: 'us' })
  // The walk put r1 and r2 in this rotation; r2's winner is the next rally's server.
  const plays = [
    { id: r1.id, serving: 'them' as const, won_by: 'us' as const },
    { id: r2.id, serving: 'us' as const, won_by: 'us' as const },
  ]

  it('plays the rallies of a rotation behind a team number, with the winner the walk used', () => {
    expect(rotationRallies([r3, r2, r1], plays, { won_by: 'us' }).map((r) => r.id)).toEqual([r1.id, r2.id])
    expect(rotationRallies([r1, r2, r3], plays, { serving: 'us', won_by: 'any' }).map((r) => r.id)).toEqual([r2.id])
    expect(rotationRallies([r1, r2, r3], plays, { won_by: 'them' })).toEqual([])
  })

  it("plays our tags of a column in the rotation's rallies, linked or by time", () => {
    const linked = tag({ t_ms: 15_000, code: 'A', attributes: { grade: '#' }, segment_id: r1.id })
    const toss = tag({ t_ms: 28_500, code: 'S', attributes: { grade: '+' } }) // 1.5 s before r2
    const theirs = tag({ t_ms: 16_000, code: 'A', side: 'them', number: 4, segment_id: r1.id })
    const later = tag({ t_ms: 55_000, code: 'A', segment_id: r3.id })
    const source = { events: [later, theirs, toss, linked], segments: [r1, r2, r3] }
    expect(tagRallies(source.events, source.segments).get(toss.id)).toBe(r2.id)
    expect(rotationTags(source, plays, column('att_ta')).map((t) => t.id)).toEqual([linked.id])
    expect(rotationTags(source, plays, column('serve_ta')).map((t) => t.id)).toEqual([toss.id])
    expect(rotationTags(source, plays, column('att_kill')).map((t) => t.id)).toEqual([linked.id])
  })

  it('names the CSV part', () => {
    expect(videoCsvUrl('v1', 'de')).toBe('/api/videos/v1/stats.csv?lang=de')
    expect(videoCsvUrl('v1', 'en', 'rotations')).toBe('/api/videos/v1/stats.csv?lang=en&part=rotations')
    expect(teamCsvUrl(92, 'en', 'setter')).toBe('/api/teams/92/stats.csv?lang=en&part=setter')
  })
})

describe('setter plays', () => {
  const play = (patch: Partial<SetPlay>): SetPlay => ({
    set_id: 's',
    attack_id: null,
    rally_id: null,
    set_no: 1,
    t_ms: 0,
    attacker: null,
    attacker_member: null,
    setter: 3,
    setter_member: 103,
    zone: null,
    rotation: null,
    quality: 'unknown',
    outcome: null,
    attacked: false,
    ...patch,
  })
  const plays = [
    play({ set_id: 'a', attacker: 7, zone: 'left', outcome: 'kill', attacked: true, rotation: 1, quality: 'perfect' }),
    play({ set_id: 'b', attacker: 7, zone: null, outcome: 'play', attacked: true, rotation: 6, set_no: 2 }),
    play({ set_id: 'c', attacker: 11, zone: 'middle', outcome: 'error', attacked: true, rotation: 1 }),
    play({ set_id: 'd', rotation: 1 }),
  ]

  it("matches every key of a line's filter, a null too, and the set", () => {
    expect(setterPlays(plays, { attacker: 7 }, null).map((p) => p.set_id)).toEqual(['a', 'b'])
    expect(setterPlays(plays, { attacker: 7 }, 1).map((p) => p.set_id)).toEqual(['a'])
    expect(setterPlays(plays, { attacked: true, zone: null }, null).map((p) => p.set_id)).toEqual(['b'])
    expect(setterPlays(plays, { rotation: 1, attacked: true, zone: 'middle' }, null).map((p) => p.set_id)).toEqual(['c'])
    expect(setterPlays(plays, { rotation: 1 }, null).map((p) => p.set_id)).toEqual(['a', 'c', 'd'])
    expect(setterPlays(plays, { attacked: false }, null).map((p) => p.set_id)).toEqual(['d'])
  })

  it("plays from the set to the end of the attack after it", () => {
    const set = tag({ t_ms: 13_000, code: 'E' })
    const attack = tag({ t_ms: 14_000, code: 'A' })
    expect(setWindow(set, attack, volleyball, null)).toEqual({ start_ms: 11_000, end_ms: 16_000 })
    expect(setWindow(set, null, volleyball, null)).toEqual({ start_ms: 11_000, end_ms: 15_000 })
    expect(setWindow(set, attack, volleyball, 15_500)).toEqual({ start_ms: 11_000, end_ms: 15_500 })
  })
})

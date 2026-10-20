import { describe, expect, it } from 'vitest'
import type { Tag } from './types'
import { period, rally, roster, tag, volleyball } from '../test/tagging'
import {
  applyHint,
  SEARCH_KEYWORDS,
  compactSearch,
  formatTerms,
  isEmptySearch,
  parseSearch,
  rotationOfRallies,
  searchContext,
  searchHints,
  searchParam,
  searchRallies,
  searchTags,
} from './search'

const parse = (text: string) => parseSearch(text, volleyball)

describe('parseSearch', () => {
  it('reads the examples of the plan', () => {
    expect(compactSearch(parse('#3 attack #').search)).toEqual({ players: [{ side: 'us', number: 3 }], codes: ['A'], grades: ['#'] })
    expect(compactSearch(parse('set 3, rotation 2, reception -').search)).toEqual({ sets: [3], rotations: [2], codes: ['R'], grades: ['-'] })
  })

  it('takes German words, glued numbers and DataVolley shorthands', () => {
    expect(compactSearch(parse('Satz 2 Annahme gegner #12').search)).toEqual({ sets: [2], codes: ['R'], sides: ['them'], players: [{ side: 'them', number: 12 }] })
    expect(compactSearch(parse('set3 rot2 P4 von 4 nach 1 zone 5').search)).toEqual({ sets: [3], rotations: [2, 4], zones_from: [4], zones_to: [1], zones: [5] })
    expect(compactSearch(parse('Aufschläge im Spiel').search)).toEqual({ codes: ['S'], outcomes: ['play'] })
    expect(compactSearch(parse('7A# a12S= 3A#46').search)).toEqual({
      players: [{ side: 'us', number: 7 }, { side: 'them', number: 12 }, { side: 'us', number: 3 }],
      codes: ['A', 'S'],
      grades: ['#', '='],
      zones_from: [4],
      zones_to: [6],
    })
    expect(compactSearch(parse('A# E r- aA#').search)).toEqual({ codes: ['A', 'E', 'R'], grades: ['#', '-'], sides: ['them'] })
  })

  it('reads rally words, outcomes and grade words', () => {
    expect(compactSearch(parse('won side-out').search)).toEqual({ won_by: ['us'], serving: ['them'] })
    expect(compactSearch(parse('lost, break').search)).toEqual({ won_by: ['them'], serving: ['us'] })
    expect(compactSearch(parse('kill ace blocked').search)).toEqual({ codes: ['A', 'S'], grades: ['#', '/'] })
    expect(compactSearch(parse('point error in play').search)).toEqual({ outcomes: ['point', 'error', 'play'] })
    expect(compactSearch(parse('ungraded serve').search)).toEqual({ grades: [null], codes: ['S'] })
  })

  it('tells a set number from the skill', () => {
    expect(compactSearch(parse('set 2').search)).toEqual({ sets: [2] })
    expect(compactSearch(parse('set').search)).toEqual({ codes: ['E'] })
    expect(compactSearch(parse('sets #3').search)).toEqual({ codes: ['E'], players: [{ side: 'us', number: 3 }] })
  })

  it('lists what it did not understand and keeps the rest', () => {
    const out = parse('#7 the rotation 9 attack')
    expect(out.unknown).toEqual(['the', 'rotation 9'])
    expect(compactSearch(out.search)).toEqual({ players: [{ side: 'us', number: 7 }], codes: ['A'] })
    expect(isEmptySearch(parse('').search)).toBe(true)
    expect(searchParam(parse('').search)).toBeNull()
    expect(searchParam(parse('#7').search)).toBe('{"players":[{"side":"us","number":7}]}')
  })

  it('writes terms back as words that read the same', () => {
    for (const text of ['them #12 attack # set 2', 'a12 #7 rotation 3 lost side-out from 4 ungraded', 'point in play zone 5']) {
      const first = parse(text)
      for (const lang of ['en', 'de'] as const) {
        const again = parse(formatTerms(first.terms, lang, volleyball))
        expect(again.unknown).toEqual([])
        expect(again.search).toEqual(first.search)
      }
    }
    // Capitalised, as the hints write them; they read back the same.
    expect(formatTerms(parse('them attack #7').terms, 'de', volleyball)).toBe('a7 Gegner Angriff')
    expect(formatTerms(parse('set 3 from 4 in play break').terms, 'en', volleyball)).toBe('Set 3 From 4 In play Break')
  })

  it('reads every word the hints offer, as they write it', () => {
    for (const lang of ['en', 'de'] as const) {
      for (const [keyword, kind] of SEARCH_KEYWORDS[lang]) {
        expect(keyword.charAt(0)).toBe(keyword.charAt(0).toUpperCase())
        // A keyword waiting for its number is read with one.
        const out = parse(keyword.endsWith(' ') ? `${keyword}3` : keyword)
        expect(out.unknown, `${lang} "${keyword}"`).toEqual([])
        const want = { skill: 'skill', skillGrade: 'skill', grade: 'grade', side: 'side', outcome: 'outcome', won: 'won', serving: 'serving', set: 'set', rotation: 'rotation', zoneFrom: 'zone', zoneTo: 'zone', zone: 'zone', player: 'player' }[kind]
        expect(out.terms[0]?.kind, `${lang} "${keyword}"`).toBe(want)
      }
    }
    // "Set" the skill, "Set 3" the set number.
    expect(compactSearch(parse('Set').search)).toEqual({ codes: ['E'] })
    expect(compactSearch(parse('Set 3').search)).toEqual({ sets: [3] })
    expect(compactSearch(parse('Attack Set 3 Im Spiel').search)).toEqual({ codes: ['A'], sets: [3], outcomes: ['play'] })
  })
})

describe('matching', () => {
  // Set 1 from rotation 1: r1 we serve and win, r2 they serve and we win (a side-out: rotation 6),
  // r3 we serve and lose. Set 2 has no starting rotation.
  const s1 = period({ no: 1, start_ms: 0, end_ms: 60_000, start_rotation: 1 })
  const s2 = period({ no: 2, start_ms: 60_000 })
  const r1 = rally({ start_ms: 1_000, end_ms: 9_000, period_id: s1.id, serving: 'us', won_by: 'us' })
  const r2 = rally({ start_ms: 11_000, end_ms: 19_000, period_id: s1.id, serving: 'them', won_by: 'us' })
  const r3 = rally({ start_ms: 21_000, end_ms: 29_000, period_id: s1.id, serving: 'us', won_by: 'them' })
  const r4 = rally({ start_ms: 61_000, end_ms: 69_000, period_id: s2.id, serving: 'them', won_by: 'them' })
  const tags = [
    tag({ t_ms: 1_100, code: 'S', number: 7, segment_id: r1.id, attributes: { grade: '+', zone_from: 1, zone_to: 5 } }),
    tag({ t_ms: 6_000, code: 'A', number: 3, segment_id: r1.id, attributes: { grade: '#', zone_from: 4 } }),
    tag({ t_ms: 10_500, code: 'S', side: 'them', number: 12, segment_id: null }), // the toss, before r2
    tag({ t_ms: 12_000, code: 'R', number: 11, segment_id: r2.id, attributes: { grade: '-' } }),
    tag({ t_ms: 21_100, code: 'S', number: 7, segment_id: r3.id, attributes: { grade: '=' } }),
    tag({ t_ms: 62_000, code: 'A', number: 7, segment_id: r4.id, attributes: { grade: '/' } }),
  ]
  const ctx = searchContext(tags, [r1, r2, r3, r4], [s1, s2])
  const at = (i: number) => tags[i] as Tag
  const ids = (list: { id: string }[]) => list.map((x) => x.id)

  it('walks the rotation like the server', () => {
    expect([...rotationOfRallies([r1, r2, r3, r4], [s1, s2]).entries()]).toEqual([
      [r1.id, 1],
      [r2.id, 1],
      [r3.id, 6],
    ])
    // Without a winner the next rally's server says it; without either the walk stops.
    const open = rally({ start_ms: 1_000, end_ms: 9_000, period_id: s1.id, serving: 'them', won_by: null })
    const next = rally({ start_ms: 11_000, end_ms: 19_000, period_id: s1.id, serving: 'us', won_by: null })
    expect([...rotationOfRallies([open, next], [s1]).values()]).toEqual([1])
  })

  it('keeps tags by their own terms and their rally', () => {
    const find = (text: string) => ids(searchTags(tags, parse(text).search, ctx, volleyball))
    expect(find('#7')).toEqual(ids([at(0), at(4), at(5)]))
    expect(find('rotation 6')).toEqual(ids([at(4)]))
    expect(find('set 2')).toEqual(ids([at(5)]))
    expect(find('side-out')).toEqual(ids([at(2), at(3), at(5)])) // the toss belongs to r2
    expect(find('point')).toEqual(ids([at(1)]))
    expect(find('error')).toEqual(ids([at(4), at(5)]))
    expect(find('to 5')).toEqual(ids([at(0)]))
    expect(find('zone 4')).toEqual(ids([at(1)]))
    expect(find('them')).toEqual(ids([at(2)]))
    expect(find('')).toEqual(ids(tags))
  })

  it('keeps rallies whose terms hold and that hold a matching tag', () => {
    const find = (text: string) => ids(searchRallies([r1, r2, r3, r4], tags, parse(text).search, ctx, volleyball))
    expect(find('#7 serve')).toEqual(ids([r1, r3]))
    expect(find('won')).toEqual(ids([r1, r2]))
    expect(find('lost attack')).toEqual(ids([r4]))
    expect(find('rotation 1')).toEqual(ids([r1, r2]))
    expect(find('set 1, reception -')).toEqual(ids([r2]))
  })
})

describe('searchHints', () => {
  it('suggests words, numbers after a keyword, and players', () => {
    expect(searchHints('att', { lang: 'en' }).map((h) => h.label)).toEqual(['Attack'])
    expect(searchHints('rot', { lang: 'en' }).map((h) => [h.label, h.insert])).toEqual([['Rotation …', 'Rotation ']])
    expect(searchHints('set 3 rotation ', { lang: 'en' }).map((h) => h.label)).toEqual(['Rotation 1', 'Rotation 2', 'Rotation 3', 'Rotation 4', 'Rotation 5', 'Rotation 6'])
    expect(searchHints('se', { lang: 'en' }).map((h) => [h.label, h.insert])).toEqual([
      ['Serve', 'Serve'],
      ['Set', 'Set'],
      ['Set …', 'Set '],
    ])
    expect(searchHints('Satz ', { lang: 'de', sets: [1, 2, 3] }).map((h) => h.insert)).toEqual(['1', '2', '3'])
    expect(searchHints('#', { lang: 'en', roster }).map((h) => [h.label, h.detail])).toEqual([
      ['#3', 'Mia Keller'],
      ['#7', 'Luca Brunner'],
      ['#11', 'Noah Meier'],
    ])
    expect(searchHints('1', { lang: 'en', roster }).map((h) => h.label)).toEqual(['#11'])
    // The app's language first, the other one too.
    expect(searchHints('an', { lang: 'de' }).map((h) => h.label)).toEqual(['Angriff', 'Annahme'])
    expect(searchHints('A', { lang: 'en' }).map((h) => h.label)).toEqual(['Attack', 'Ace', 'Angriff', 'Aufschlag', 'Annahme', 'Abwehr', 'Ass'])
    expect(searchHints('', { lang: 'en' })).toHaveLength(6)
  })

  it('puts a hint in place of the word being typed', () => {
    expect(applyHint('#3 att', { insert: 'Attack' })).toBe('#3 Attack ')
    expect(applyHint('Set 2, rot', { insert: 'Rotation ' })).toBe('Set 2, Rotation ')
    expect(applyHint('Rotation ', { insert: '2' })).toBe('Rotation 2 ')
  })
})

import { describe, expect, it } from 'vitest'
import { roster, volleyball } from '../test/tagging'
import { formatEntry, isEntryChar, parseEntry, rallyWinner, skillByCode, toggleSide } from './grammar'

const parse = (text: string) => parseEntry(text, volleyball, roster)

describe('parseEntry', () => {
  it('reads our attack, kill', () => {
    const p = parse('7A#')
    expect(p).toMatchObject({ side: 'us', number: 7, skillCode: 'A', grade: '#', zoneFrom: null, zoneTo: null })
    expect(p.skill?.category).toBe('attack')
    expect(p.player?.name).toBe('Luca Brunner')
    expect(p.unknownNumber).toBe(false)
    expect(p.error).toBeNull()
    expect(p.complete).toBe(true)
  })

  it('reads the opponent with the a prefix', () => {
    const p = parse('a12S=')
    expect(p).toMatchObject({ side: 'them', number: 12, skillCode: 'S', grade: '=', complete: true })
    expect(p.player?.side).toBe('them')
  })

  it('takes an upper-case A as the prefix too', () => {
    expect(parse('A12S=')).toMatchObject({ side: 'them', number: 12, skillCode: 'S' })
  })

  it('reads a reception, positive', () => {
    expect(parse('5R+')).toMatchObject({ side: 'us', number: 5, skillCode: 'R', grade: '+', complete: true })
  })

  it('reads zones after the grade', () => {
    expect(parse('3A#46')).toMatchObject({ number: 3, grade: '#', zoneFrom: 4, zoneTo: 6, expects: 'none', complete: true })
    expect(parse('3A#4')).toMatchObject({ zoneFrom: 4, zoneTo: null, expects: 'zone', complete: true })
  })

  it('reads zones without a grade', () => {
    expect(parse('3A46')).toMatchObject({ grade: null, zoneFrom: 4, zoneTo: 6, complete: true })
  })

  it('accepts lower-case skill letters', () => {
    const p = parse('7a#')
    expect(p).toMatchObject({ side: 'us', number: 7, skillCode: 'A', grade: '#', complete: true })
  })

  it('saves a skill without a grade', () => {
    expect(parse('11D')).toMatchObject({ number: 11, skillCode: 'D', grade: null, complete: true, expects: 'grade' })
  })

  it('allows a tag without a number (an unknown opponent)', () => {
    const p = parse('aS=')
    expect(p).toMatchObject({ side: 'them', number: null, skillCode: 'S', grade: '=', complete: true })
    expect(p.unknownNumber).toBe(false)
  })

  it('flags a number that is not on the roster, but still saves it', () => {
    const p = parse('99A+')
    expect(p.unknownNumber).toBe(true)
    expect(p.player).toBeNull()
    expect(p.complete).toBe(true)
  })

  it("doesn't flag unknown numbers for a side without a roster", () => {
    const p = parseEntry('a4A#', volleyball, roster.filter((r) => r.side === 'us'))
    expect(p.unknownNumber).toBe(false)
    expect(p.complete).toBe(true)
  })

  it('flags an unknown skill letter', () => {
    const p = parse('7X#')
    expect(p).toMatchObject({ skillCode: 'X', skill: null, error: 'unknownSkill', complete: false })
  })

  it('rejects a zone the taxonomy does not have', () => {
    expect(parse('7A#0')).toMatchObject({ zoneFrom: 0, error: 'invalidZone', complete: false })
  })

  it('rejects a third zone and anything after it', () => {
    expect(parse('7A#467')).toMatchObject({ error: 'unexpected', complete: false })
    expect(parse('7A#x')).toMatchObject({ error: 'unexpected', complete: false })
    expect(parse('7A##')).toMatchObject({ error: 'unexpected', complete: false })
  })

  it('rejects a grade before the skill', () => {
    expect(parse('7#')).toMatchObject({ error: 'skillMissing', complete: false, expects: 'skill' })
  })

  it('rejects numbers with more than two digits', () => {
    expect(parse('123A')).toMatchObject({ number: 123, error: 'numberTooLong', complete: false })
  })

  it('reads 07 as number 7', () => {
    expect(parse('07A').number).toBe(7)
  })

  it('gives a useful partial result while typing', () => {
    expect(parse('')).toMatchObject({ side: 'us', number: null, expects: 'number', complete: false, error: null })
    expect(parse('a')).toMatchObject({ side: 'them', number: null, expects: 'number', complete: false })
    expect(parse('1')).toMatchObject({ number: 1, expects: 'skill', complete: false })
    const seven = parse('7')
    expect(seven).toMatchObject({ number: 7, expects: 'skill', complete: false, error: null })
    expect(seven.player?.name).toBe('Luca Brunner')
    expect(parse('7A')).toMatchObject({ skillCode: 'A', expects: 'grade', complete: true })
    expect(parse('7A#')).toMatchObject({ expects: 'zone' })
  })

  it('works without a roster', () => {
    const p = parseEntry('7A#', volleyball)
    expect(p).toMatchObject({ player: null, unknownNumber: false, complete: true })
  })
})

describe('entry helpers', () => {
  it('toggles the side prefix', () => {
    expect(toggleSide('7A')).toBe('a7A')
    expect(toggleSide('a7A')).toBe('7A')
    expect(toggleSide('A7A')).toBe('7A')
    expect(toggleSide('')).toBe('a')
  })

  it('formats a tag back into its entry', () => {
    expect(formatEntry({ side: 'us', number: 3, code: 'A', attributes: { grade: '#', zone_from: 4, zone_to: 6 } })).toBe('3A#46')
    expect(formatEntry({ side: 'them', number: 12, code: 'S', attributes: { grade: '=' } })).toBe('a12S=')
    expect(formatEntry({ side: 'them', number: null, code: 'S', attributes: {} })).toBe('aS')
  })

  it('round-trips through the parser', () => {
    for (const text of ['7A#', 'a12S=', '5R+', '3A#46', '11D-', 'a4B#']) {
      const p = parse(text)
      const attributes = {
        ...(p.grade ? { grade: p.grade } : {}),
        ...(p.zoneFrom !== null ? { zone_from: p.zoneFrom } : {}),
        ...(p.zoneTo !== null ? { zone_to: p.zoneTo } : {}),
      }
      expect(formatEntry({ side: p.side, number: p.number, code: p.skillCode ?? '', attributes })).toBe(text)
    }
  })

  it('knows which keys belong to an entry', () => {
    for (const key of ['7', 'a', 'S', '#', '+', '!', '-', '/', '=']) expect(isEntryChar(key, volleyball)).toBe(true)
    for (const key of [' ', 'Enter', '?', ',', '.', '[']) expect(isEntryChar(key, volleyball)).toBe(false)
  })

  it('finds skills case-insensitively', () => {
    expect(skillByCode(volleyball, 's')?.code).toBe('S')
    expect(skillByCode(volleyball, 'Z')).toBeNull()
  })
})

describe('rallyWinner', () => {
  const skill = (code: string) => skillByCode(volleyball, code)

  it('gives the rally to the tagged side on a kill, an ace or a block point', () => {
    expect(rallyWinner(skill('A'), '#', 'us')).toBe('us')
    expect(rallyWinner(skill('S'), '#', 'them')).toBe('them')
    expect(rallyWinner(skill('B'), '#', 'us')).toBe('us')
  })

  it('gives the rally to the other side on an error or a blocked attack', () => {
    expect(rallyWinner(skill('S'), '=', 'us')).toBe('them')
    expect(rallyWinner(skill('A'), '/', 'us')).toBe('them')
    expect(rallyWinner(skill('R'), '=', 'them')).toBe('us')
  })

  it("doesn't end the rally otherwise", () => {
    expect(rallyWinner(skill('A'), '+', 'us')).toBeNull()
    expect(rallyWinner(skill('R'), '#', 'us')).toBeNull()
    expect(rallyWinner(skill('A'), null, 'us')).toBeNull()
    expect(rallyWinner(null, '#', 'us')).toBeNull()
  })
})

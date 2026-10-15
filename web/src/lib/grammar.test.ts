import { describe, expect, it } from 'vitest'
import { basketball, basketballRoster, roster, volleyball } from '../test/tagging'
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

describe('parseEntry, basketball', () => {
  const shoot = (text: string) => parseEntry(text, basketball, basketballRoster)

  it('reads a made three-pointer', () => {
    const p = shoot('23S3+')
    expect(p).toMatchObject({ side: 'us', number: 23, skillCode: 'S3', grade: '+', zoneFrom: null, zoneTo: null, expects: 'none' })
    expect(p.skill).toMatchObject({ code: 'S3', points: 3, graded: true })
    expect(p.player?.name).toBe('David Frei')
    expect(p.error).toBeNull()
    expect(p.complete).toBe(true)
  })

  it('reads a missed two-pointer and a made free throw', () => {
    expect(shoot('23S2-')).toMatchObject({ skillCode: 'S2', grade: '-', complete: true })
    expect(shoot('5FT+')).toMatchObject({ number: 5, skillCode: 'FT', grade: '+', complete: true, expects: 'none' })
  })

  it('reads the opponent with the a prefix', () => {
    const p = shoot('a4S2+')
    expect(p).toMatchObject({ side: 'them', number: 4, skillCode: 'S2', grade: '+', complete: true })
    expect(p.player?.side).toBe('them')
    expect(shoot('A4S2+')).toMatchObject({ side: 'them', number: 4, skillCode: 'S2' })
    expect(shoot('aS2-')).toMatchObject({ side: 'them', number: null, skillCode: 'S2', complete: true })
  })

  it('reads codes case-insensitively', () => {
    expect(shoot('23s3+')).toMatchObject({ skillCode: 'S3', complete: true })
    expect(shoot('11ro')).toMatchObject({ skillCode: 'RO', complete: true })
    expect(shoot('7as')).toMatchObject({ side: 'us', number: 7, skillCode: 'AS', complete: true })
  })

  it('takes a shot without a grade, and waits for one', () => {
    expect(shoot('23S3')).toMatchObject({ skillCode: 'S3', grade: null, complete: true, expects: 'grade' })
  })

  it('reads skills that take no grade', () => {
    for (const [text, code] of [['11RO', 'RO'], ['11RD', 'RD'], ['7TO', 'TO'], ['5AS', 'AS'], ['23ST', 'ST'], ['23BL', 'BL'], ['5F', 'F']] as const) {
      expect(shoot(text)).toMatchObject({ skillCode: code, grade: null, error: null, complete: true, expects: 'none' })
    }
  })

  it('takes the longest code: 5F is a foul, 5FT a free throw', () => {
    expect(shoot('5F').skill?.code).toBe('F')
    expect(shoot('5FT').skill?.code).toBe('FT')
    expect(shoot('5FT')).toMatchObject({ expects: 'grade', complete: true })
  })

  it('waits while only the start of a code is typed', () => {
    const p = shoot('23S')
    expect(p).toMatchObject({ number: 23, skill: null, skillCode: 'S', error: null, complete: false, expects: 'skill' })
    expect(shoot('11R')).toMatchObject({ skill: null, error: null, complete: false, expects: 'skill' })
    expect(shoot('a4s')).toMatchObject({ side: 'them', error: null, complete: false, expects: 'skill' })
  })

  it('rejects a grade on a skill that takes none', () => {
    expect(shoot('11RO+')).toMatchObject({ skillCode: 'RO', grade: null, error: 'noGrade', complete: false })
    expect(shoot('5F-')).toMatchObject({ error: 'noGrade', complete: false })
  })

  it('has no zones', () => {
    expect(shoot('23S3+4')).toMatchObject({ zoneFrom: null, error: 'unexpected', complete: false })
    expect(shoot('23S34')).toMatchObject({ error: 'unexpected', complete: false })
    expect(shoot('11RO4')).toMatchObject({ error: 'unexpected', complete: false })
  })

  it('rejects anything after the grade', () => {
    expect(shoot('23S3++')).toMatchObject({ error: 'unexpected', complete: false })
    expect(shoot('23S3+x')).toMatchObject({ error: 'unexpected', complete: false })
  })

  it('flags codes that are not skills', () => {
    expect(shoot('7X')).toMatchObject({ skillCode: 'X', skill: null, error: 'unknownSkill', complete: false })
    expect(shoot('23S4')).toMatchObject({ skillCode: 'S4', error: 'unknownSkill', complete: false })
    expect(shoot('7XY+')).toMatchObject({ skillCode: 'XY', error: 'unknownSkill', complete: false })
  })

  it('rejects volleyball grades', () => {
    expect(shoot('23S3#')).toMatchObject({ error: 'unexpected', complete: false })
    expect(isEntryChar('#', basketball)).toBe(false)
    for (const key of ['2', 'S', 'f', '+', '-']) expect(isEntryChar(key, basketball)).toBe(true)
  })

  it('gives a useful partial result while typing', () => {
    expect(shoot('2')).toMatchObject({ number: 2, expects: 'skill', complete: false })
    const typed = ['23', '23S', '23S3', '23S3+'].map((text) => shoot(text).expects)
    expect(typed).toEqual(['skill', 'skill', 'grade', 'none'])
  })

  it('round-trips through formatEntry', () => {
    for (const text of ['23S3+', 'a4S2+', '23S2-', '11RO', '7TO', '5FT+', 'a4F', 'aS3-']) {
      const p = shoot(text)
      const attributes = p.grade ? { grade: p.grade } : {}
      expect(formatEntry({ side: p.side, number: p.number, code: p.skill?.code ?? '', attributes })).toBe(text)
    }
  })

  it('never ends a rally', () => {
    for (const s of basketball.skills) {
      expect(rallyWinner(s, '+', 'us')).toBeNull()
      expect(rallyWinner(s, '-', 'them')).toBeNull()
    }
    // A taxonomy served without the field.
    const bare = { ...(skillByCode(basketball, 'S3') as object), ends_rally_on: undefined } as unknown as Parameters<typeof rallyWinner>[0]
    expect(rallyWinner(bare, '+', 'us')).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { cluster } from '../test/players'
import { BRIEF_SECONDS, clusterTitle, cropStyle, groupClusters, isPieces, parseNumber, sheetUrl, type Sheets } from './players'

const sheets: Sheets = { count: 2, cell_w: 80, cell_h: 160, cols: 10, rows: 8, version: 'abcd1234' }

describe('players', () => {
  it('places a crop of a contact sheet as a scaled CSS background', () => {
    expect(cropStyle(sheets, [1, 23], 0.5)).toEqual({
      width: '40px',
      height: '80px',
      backgroundSize: '400px auto',
      backgroundPosition: '-120px -160px',
    })
    expect(sheetUrl('v1', sheets, 1)).toBe('/api/videos/v1/players/sheets/1?v=abcd1234')
  })

  it('groups the cards by team, most seen first, not-players and brief ones apart', () => {
    const grouped = groupClusters([
      cluster({ key: 1, seconds: 30 }),
      cluster({ key: 2, seconds: 90 }),
      cluster({ key: 3, team: 'them' }),
      cluster({ key: 4, not_player: true }),
      cluster({ key: 5, seconds: BRIEF_SECONDS - 1 }),
      cluster({ key: 6, seconds: 2, number: 7 }), // named: never folded away
    ])
    expect(grouped.us.map((c) => c.key)).toEqual([2, 1, 6])
    expect(grouped.them.map((c) => c.key)).toEqual([3])
    expect(grouped.others.map((c) => c.key)).toEqual([4])
    expect(grouped.brief.map((c) => c.key)).toEqual([5])
  })

  it('puts pieces cards with the cards to check, however brief: they are not one person', () => {
    const grouped = groupClusters([
      cluster({ key: 1, seconds: 90 }),
      cluster({ key: 2, kind: 'pieces', seconds: BRIEF_SECONDS - 1 }),
      cluster({ key: 3, kind: 'pieces', seconds: 300, number: 7 }), // the coach named it: a person
    ])
    expect(grouped.check.map((c) => c.key)).toEqual([2])
    expect(grouped.us.map((c) => c.key)).toEqual([3, 1])
    expect(isPieces({ kind: 'pieces', number: null })).toBe(true)
    expect(isPieces({ kind: 'person', number: null })).toBe(false)
  })

  it('titles a card by its number and name, else as an unnamed player', () => {
    const unnamed = (key: number) => `Player ${key}`
    expect(clusterTitle({ number: 7, name: 'Alex Muster', key: 3 }, unnamed)).toBe('#7 Alex Muster')
    expect(clusterTitle({ number: 12, name: null, key: 3 }, unnamed)).toBe('#12')
    expect(clusterTitle({ number: null, name: null, key: 3 }, unnamed)).toBe('Player 3')
    const pieces = (key: number) => `Pieces ${key}`
    expect(clusterTitle({ number: null, name: null, key: 3, kind: 'pieces' }, unnamed, pieces)).toBe('Pieces 3')
    expect(clusterTitle({ number: 7, name: null, key: 3, kind: 'pieces' }, unnamed, pieces)).toBe('#7')
  })

  it('parses a typed number', () => {
    expect(parseNumber(' 7 ')).toBe(7)
    expect(parseNumber('#12')).toBe(12)
    expect(parseNumber('0')).toBe(0)
    expect(parseNumber('')).toBeNull()
    expect(parseNumber('100')).toBeUndefined()
    expect(parseNumber('7a')).toBeUndefined()
  })
})

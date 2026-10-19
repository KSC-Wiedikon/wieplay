import { describe, expect, it } from 'vitest'
import { boxAt, isSure, numbersToOffer, zoomOnto, type Moment } from './identify'
import type { RosterEntry } from './types'

const roster: RosterEntry[] = [
  { side: 'us', number: 7, member_id: 101, position: null, is_libero: false, name: 'Alex Muster' },
  { side: 'us', number: 3, member_id: 102, position: 'setter', is_libero: false, name: 'Sam Beispiel' },
  { side: 'us', number: 11, member_id: 103, position: 'middle', is_libero: false, name: 'Robin Test' },
]

describe('identifying the players', () => {
  it('offers the numbers on court first, then the rest of our roster', () => {
    expect(numbersToOffer('us', [11, 7], roster).map((o) => [o.number, o.onCourt, o.name])).toEqual([
      [11, true, 'Robin Test'],
      [7, true, 'Alex Muster'],
      [3, false, 'Sam Beispiel'],
    ])
    // Theirs: what the sheet has on court; any other number is typed.
    expect(numbersToOffer('them', [4, 8], roster).map((o) => o.number)).toEqual([4, 8])
  })

  it('follows the person through the moment’s clip', () => {
    const moment: Moment = {
      t_ms: 10_000,
      track: 1,
      box: [0.1, 0.5, 0.15, 0.8],
      clip: [
        [9_000, 0.1, 0.5, 0.15, 0.8],
        [10_000, 0.2, 0.5, 0.25, 0.8],
      ],
    }
    expect(boxAt(moment, 9_900)).toEqual([0.2, 0.5, 0.25, 0.8])
    expect(boxAt({ ...moment, clip: [] }, 9_900)).toEqual(moment.box)
  })

  it('zooms the video so that the person fills the picture', () => {
    // A 16:9 frame, a portrait view; the box is 0.3 of the frame tall.
    const style = zoomOnto([0.4, 0.4, 0.45, 0.7], 16 / 9, 3 / 4, 1)
    expect(parseFloat(style.height)).toBeCloseTo((1 / 0.3) * 100)
    // The box's middle lands in the view's middle.
    const top = parseFloat(style.top) / 100
    const height = parseFloat(style.height) / 100
    expect(top + 0.55 * height).toBeCloseTo(0.5)
  })

  it('counts a card as sure once the coach answered it', () => {
    expect(isSure({ number: null, not_player: false, uncertain: true })).toBe(false)
    expect(isSure({ number: 7, not_player: false, uncertain: false })).toBe(true)
    expect(isSure({ number: null, not_player: true, uncertain: false })).toBe(true)
  })
})

import { describe, expect, it } from 'vitest'
import { opponentOf, scoreOf, setsOf, splitGames } from './games'
import type { Game } from './types'

const game = (patch: Partial<Game>): Game => ({
  id: 1,
  date: '2026-10-18',
  time: '14:00',
  home_name: 'KSC Wiedikon H3',
  away_name: 'VBC Zürich',
  our_side: 'home',
  league: '2L',
  hall_name: 'Sihlhölzli',
  home_score: null,
  away_score: null,
  sets: null,
  ...patch,
})

describe('games', () => {
  it('splits upcoming and played games', () => {
    const today = '2026-10-15'
    const { upcoming, played } = splitGames(
      [
        game({ id: 1, date: '2026-10-25' }),
        game({ id: 2, date: '2026-10-18' }),
        game({ id: 3, date: '2026-10-01', home_score: 3, away_score: 1 }),
        game({ id: 4, date: '2026-10-08' }),
        game({ id: 5, date: '2026-10-15', home_score: 0, away_score: 3 }),
      ],
      today,
    )
    expect(upcoming.map((g) => g.id)).toEqual([2, 1])
    expect(played.map((g) => g.id)).toEqual([5, 4, 3])
  })

  it('names the side that is not ours', () => {
    expect(opponentOf(game({ our_side: 'home' }))).toBe('VBC Zürich')
    expect(opponentOf(game({ our_side: 'away' }))).toBe('KSC Wiedikon H3')
    expect(opponentOf(game({ our_side: null }))).toBe('KSC Wiedikon H3 – VBC Zürich')
  })

  it('shows scores and sets from our side', () => {
    const away = game({ our_side: 'away', home_score: 1, away_score: 3, sets: [[20, 25], [25, 23], [19, 25], [22, 25]] })
    expect(scoreOf(away)).toBe('3:1')
    expect(setsOf(away)).toBe('25:20, 23:25, 25:19, 25:22')
    expect(scoreOf(game({ home_score: 3, away_score: 0 }))).toBe('3:0')
    expect(scoreOf(game({}))).toBe('')
  })
})

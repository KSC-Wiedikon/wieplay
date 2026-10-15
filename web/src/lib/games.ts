import type { Game, Training } from './types'

function hasResult(game: Game): boolean {
  return game.home_score !== null && game.away_score !== null
}

const byDateTime = (a: { date: string; time: string | null }, b: { date: string; time: string | null }) =>
  a.date.localeCompare(b.date) || (a.time ?? '').localeCompare(b.time ?? '')

/** Upcoming (soonest first) and played (latest first). A game counts as played once it has a result or its day is over. */
export function splitGames(games: readonly Game[], today: string): { upcoming: Game[]; played: Game[] } {
  const upcoming: Game[] = []
  const played: Game[] = []
  for (const game of games) {
    if (hasResult(game) || game.date < today) played.push(game)
    else upcoming.push(game)
  }
  upcoming.sort(byDateTime)
  played.sort((a, b) => byDateTime(b, a))
  return { upcoming, played }
}

export function upcomingTrainings(trainings: readonly Training[], today: string): Training[] {
  return trainings
    .filter((t) => t.date >= today)
    .sort((a, b) => byDateTime({ date: a.date, time: a.start_time }, { date: b.date, time: b.start_time }))
}

/** The side that isn't ours; both names when we don't know which side we are. */
export function opponentOf(game: Game): string {
  if (game.our_side === 'home') return game.away_name
  if (game.our_side === 'away') return game.home_name
  return `${game.home_name} – ${game.away_name}`
}

function ours(game: Game, [home, away]: [number, number]): string {
  return game.our_side === 'away' ? `${away}:${home}` : `${home}:${away}`
}

/** Final score from our side ("3:1" = we won 3 sets to 1); home:away when our side is unknown. */
export function scoreOf(game: Game): string {
  if (game.home_score === null || game.away_score === null) return ''
  return ours(game, [game.home_score, game.away_score])
}

/** Set scores from our side, e.g. "25:20, 23:25". */
export function setsOf(game: Game): string {
  return (game.sets ?? []).map((set) => ours(game, set)).join(', ')
}

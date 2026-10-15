import type { SportName } from './types'

export const SPORTS: readonly SportName[] = ['volleyball', 'basketball']

const collator = new Intl.Collator('de-CH', { numeric: true, sensitivity: 'base' })

/** Newest season first, then by name ("H2" before "H10"). */
export function compareTeams(a: { season: string; name: string }, b: { season: string; name: string }): number {
  return b.season.localeCompare(a.season) || collator.compare(a.name, b.name)
}

/** Volleyball first, then basketball; sports without teams are left out. */
export function groupBySport<T extends { sport: SportName; season: string; name: string }>(
  teams: readonly T[],
): { sport: SportName; teams: T[] }[] {
  return SPORTS.map((sport) => ({
    sport,
    teams: teams.filter((t) => t.sport === sport).sort(compareTeams),
  })).filter((group) => group.teams.length > 0)
}

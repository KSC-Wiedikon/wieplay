import type { Me, SportName, Team } from './types'

export const SPORTS: readonly SportName[] = ['volleyball', 'basketball']

const collator = new Intl.Collator('de-CH', { numeric: true, sensitivity: 'base' })

/** Newest season first, then by name ("H2" before "H10"). */
export function compareTeams(a: { season: string; name: string }, b: { season: string; name: string }): number {
  return b.season.localeCompare(a.season) || collator.compare(a.name, b.name)
}

/** One entry per team (lineage): its newest season, then the earlier ones, newest first. */
export function groupBySeasons<T extends { lineage_id: string; season: string; name: string }>(
  teams: readonly T[],
): { latest: T; earlier: T[] }[] {
  const byLineage = new Map<string, T[]>()
  for (const team of teams) byLineage.set(team.lineage_id, [...(byLineage.get(team.lineage_id) ?? []), team])
  return [...byLineage.values()]
    .map((seasons) => {
      const [latest, ...earlier] = [...seasons].sort(compareTeams) as [T, ...T[]]
      return { latest, earlier }
    })
    .sort((a, b) => compareTeams(a.latest, b.latest))
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

/**
 * Whether the viewer may upload for this team row, as the API decides it
 * (core/permissions.can_upload): its coaches and team managers, sport admins and the team's
 * helpers. Only used to offer the upload; the upload page asks the server (`can_upload`).
 */
export function mayUpload(team: Team, me: Pick<Me, 'sport_admin'>): boolean {
  return (
    me.sport_admin.includes(team.sport) ||
    team.rel.some((rel) => rel === 'coach' || rel === 'team_responsible') ||
    team.helper === true
  )
}

/** Volleyball first, then basketball; within a sport one entry per team, newest season first. */
export function teamEntries<T extends { sport: SportName; lineage_id: string; season: string; name: string }>(
  teams: readonly T[],
): { latest: T; earlier: T[] }[] {
  return groupBySport(teams).flatMap((group) => groupBySeasons(group.teams))
}

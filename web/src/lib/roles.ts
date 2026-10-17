import type { TFunction } from 'i18next'
import type { Team } from './types'

/** A team relation from Wiedisync as a label; unknown ones as they are. */
export function roleLabel(t: TFunction, rel: string): string {
  switch (rel) {
    case 'player':
      return t('roles.player')
    case 'coach':
      return t('roles.coach')
    case 'captain':
      return t('roles.captain')
    case 'team_responsible':
      return t('roles.team_responsible')
    default:
      return rel
  }
}

/** The viewer's roles in a team as labels: the Wiedisync relations, then "Helper" (a WiePlay grant). */
export function teamRoleLabels(t: TFunction, team: Pick<Team, 'rel' | 'helper'>): string[] {
  const labels = team.rel.map((rel) => roleLabel(t, rel))
  if (team.helper) labels.push(t('roles.helper'))
  return labels
}

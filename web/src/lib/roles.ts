import type { TFunction } from 'i18next'

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

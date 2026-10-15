import type { TFunction } from 'i18next'
import type { Blocker } from './types'

/** A filming blocker as one plain sentence; the admin's reason is appended for the policy codes. */
export function blockerMessage(t: TFunction, blocker: Blocker): string {
  const { count } = blocker
  const reason = blocker.detail?.trim() ? ` ${t('blockers.reason', { detail: blocker.detail.trim() })}` : ''
  switch (blocker.code) {
    case 'policy_matches':
      return t('blockers.policy_matches') + reason
    case 'policy_trainings':
      return t('blockers.policy_trainings') + reason
    case 'under_18':
      return count == null ? t('blockers.under_18_unknown') : t('blockers.under_18', { count })
    case 'no_birthdate':
      return count == null ? t('blockers.no_birthdate_unknown') : t('blockers.no_birthdate', { count })
    case 'no_roster':
      return t('blockers.no_roster')
    case 'adult_check_unavailable':
      return t('blockers.adult_check_unavailable')
    case 'consent_missing':
      return count == null ? t('blockers.consent_missing_unknown') : t('blockers.consent_missing', { count })
    case 'roster_unavailable':
      return t('blockers.roster_unavailable')
    default:
      return t('blockers.unknown', { code: blocker.code })
  }
}

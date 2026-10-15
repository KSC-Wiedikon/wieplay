import { describe, expect, it } from 'vitest'
import i18n from '../i18n'
import { blockerMessage } from './blockers'
import type { Blocker } from './types'

const en = i18n.getFixedT('en')
const de = i18n.getFixedT('de')
const b = (code: string, count: number | null = null, detail: string | null = null): Blocker => ({ code, count, detail })

describe('blockerMessage', () => {
  it('adds the admin reason to the policy codes', () => {
    expect(blockerMessage(en, b('policy_matches', null, 'U23 opponents may be under 18'))).toBe(
      'Matches are not filmed for this team. Reason: U23 opponents may be under 18',
    )
    expect(blockerMessage(en, b('policy_trainings'))).toBe('Trainings are not filmed for this team.')
    expect(blockerMessage(en, b('policy_trainings', null, '  '))).toBe('Trainings are not filmed for this team.')
  })

  it('counts people, with singular and plural', () => {
    expect(blockerMessage(en, b('under_18', 1))).toBe('1 person on the roster is under 18 on that day.')
    expect(blockerMessage(en, b('under_18', 3))).toBe('3 people on the roster are under 18 on that day.')
    expect(blockerMessage(en, b('no_birthdate', 2))).toBe('2 people on the roster have no birthdate in Wiedisync.')
  })

  it('works without a count', () => {
    expect(blockerMessage(en, b('under_18'))).toBe('Someone on the roster is under 18 on that day.')
    expect(blockerMessage(en, b('no_birthdate'))).toBe('Someone on the roster has no birthdate in Wiedisync.')
  })

  it('explains the other codes', () => {
    expect(blockerMessage(en, b('no_roster'))).toBe('The team has no roster in Wiedisync.')
    expect(blockerMessage(en, b('adult_check_unavailable'))).toBe(
      "Wiedisync couldn't be asked whether everyone is 18 or older. Try again later.",
    )
    expect(blockerMessage(en, b('roster_unavailable'))).toBe(
      "The rosters couldn't be refreshed from Wiedisync. Try again later.",
    )
    expect(blockerMessage(en, b('something_new', 2))).toBe('Filming is not allowed for another reason (something_new).')
  })

  it('has German plurals', () => {
    expect(blockerMessage(de, b('no_birthdate', 1))).toBe('1 Person im Kader hat kein Geburtsdatum in Wiedisync.')
    expect(blockerMessage(de, b('under_18', 2))).toBe('2 Personen im Kader sind an diesem Tag unter 18.')
    expect(blockerMessage(de, b('policy_matches', null, 'U23-Liga'))).toBe(
      'Spiele dieses Teams werden nicht gefilmt. Grund: U23-Liga',
    )
  })
})

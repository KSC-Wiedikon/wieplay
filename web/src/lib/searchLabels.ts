// What the search bar understood, in words (src/lib/search.ts terms as chips). Needs the search
// strings (src/i18n/pages.ts).

import type { TFunction } from 'i18next'
import { skillByCode } from './grammar'
import { gradeLabel, playerLabel, sideLabel, skillLabel } from './labels'
import type { SearchTerm } from './search'
import type { SideNames } from './sides'
import type { RosterEntry, Taxonomy } from './types'

/** A term in words for its chip: "#7 Luca Brunner", "Attack", "# Kill", "Set 3". */
export function termLabel(t: TFunction, term: SearchTerm, ctx: { taxonomy: Taxonomy | null; roster: readonly RosterEntry[]; names: SideNames | null; skills: readonly string[] }): string {
  switch (term.kind) {
    case 'side':
      return sideLabel(t, term.side, ctx.names)
    case 'player':
      return playerLabel(t, term.side, term.number, ctx.roster, undefined, ctx.names)
    case 'skill':
      return skillLabel(t, ctx.taxonomy ? skillByCode(ctx.taxonomy, term.code) : null, term.code)
    case 'grade': {
      if (term.grade === null) return t('search.chips.ungraded')
      // With one skill named, the grade says what it means for it.
      const only = ctx.skills.length === 1 && ctx.taxonomy ? skillByCode(ctx.taxonomy, ctx.skills[0] as string) : null
      const meaning = only ? gradeLabel(t, only, term.grade) : ''
      return meaning && meaning !== term.grade ? t('search.chips.gradeMeaning', { grade: term.grade, meaning }) : t('search.chips.grade', { grade: term.grade })
    }
    case 'zone':
      return t(term.end === 'from' ? 'search.chips.zoneFrom' : term.end === 'to' ? 'search.chips.zoneTo' : 'search.chips.zone', { zone: term.zone })
    case 'outcome':
      return t(`search.chips.${term.outcome}`)
    case 'set':
      return t('search.chips.set', { no: term.no })
    case 'rotation':
      return t('search.chips.rotation', { rotation: term.rotation })
    case 'won':
      return t(term.side === 'us' ? 'search.chips.won' : 'search.chips.lost')
    case 'serving':
      return t(term.side === 'us' ? 'search.chips.break' : 'search.chips.sideOut')
  }
}

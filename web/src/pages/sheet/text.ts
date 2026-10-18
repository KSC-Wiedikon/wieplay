import type { TFunction } from 'i18next'
import { sideLabel } from '../../lib/labels'
import { boxName, targetFor, type Issue, type RepairOut } from '../../lib/scoresheet'
import type { SideNames } from '../../lib/sides'
import type { Side } from '../../lib/types'

function num(value: unknown): string {
  return typeof value === 'number' ? String(value) : ''
}

/** A check that failed, in words, naming the box it doubts ("KSCW H3 IV/2"). */
export function issueText(t: TFunction, issue: Issue, names: SideNames | null, deciding: boolean): string {
  const p = issue.params
  const team = (side: unknown) => sideLabel(t, side === 'us' || side === 'them' ? (side as Side) : null, names)
  const boxTeam = issue.box ? team(issue.box.side) : ''
  const box = issue.box ? boxName(issue.box) : ''
  switch (issue.code) {
    case 'low':
      return t('sheet.check.issue.low', { team: boxTeam, box, value: num(p.value), score: num(p.score) })
    case 'over':
      return t('sheet.check.issue.over', { team: boxTeam, box, us: num(p.us), them: num(p.them) })
    case 'hole':
      return t('sheet.check.issue.hole', { team: boxTeam, box })
    case 'missing':
      return t('sheet.check.issue.missing', { team: boxTeam, box })
    case 'not_over':
      return t('sheet.check.issue.not_over', { us: num(p.us), them: num(p.them), target: targetFor(deciding) })
    case 'final_missing':
      return t('sheet.check.issue.final_missing', { team: boxTeam, box })
    case 'circled':
      return t('sheet.check.issue.circled', { team: boxTeam, box, final: num(p.final) })
    case 'official':
      return t('sheet.check.issue.official', {
        us: num(p.us),
        them: num(p.them),
        officialUs: num(p.official_us),
        officialThem: num(p.official_them),
      })
    case 'first_serve':
    case 'empty':
      return t(`sheet.check.issue.${issue.code}`)
    case 'timeout_off':
    case 'timeout_mirror':
    case 'sanction_off':
    case 'penalty_off':
      return t(`sheet.check.issue.${issue.code}`, { team: team(p.side), own: num(p.own), opp: num(p.opp) })
    case 'timeouts_many':
    case 'lineup_twice':
      return t(`sheet.check.issue.${issue.code}`, { team: team(p.side) })
    case 'sub_off':
      return t('sheet.check.issue.sub_off', { team: team(p.side), position: num(p.position), own: num(p.own), opp: num(p.opp) })
    case 'sub_order':
      return t('sheet.check.issue.sub_order', { team: team(p.side), position: num(p.position) })
    case 'sub_on_court':
      return t('sheet.check.issue.sub_on_court', { team: team(p.side), number: num(p.number) })
    default:
      return issue.code
  }
}

/** The server's suggested repair, in words. */
export function repairText(t: TFunction, repair: RepairOut, names: SideNames | null): string {
  const args = { team: sideLabel(t, repair.box.side, names), box: boxName(repair.box), old: num(repair.old), new: num(repair.new) }
  if (repair.kind === 'insert') return t('sheet.check.repairInsert', args)
  if (repair.kind === 'delete') return t('sheet.check.repairDelete', args)
  return repair.old === null ? t('sheet.check.repairFill', args) : t('sheet.check.repair', args)
}

import type { TFunction } from 'i18next'
import { sideLabel } from '../../lib/labels'
import { boxName, targetFor, type CorrectionDoc, type CorrectionOut, type CorrectionSource, type Issue, type RepairOut } from '../../lib/scoresheet'
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

/** "the board log and the video", "your calls": who a correction rests on. */
export function sourcesText(t: TFunction, sources: readonly CorrectionSource[]): string {
  const parts = (sources.length > 0 ? sources : (['log'] as const)).map((x) => t(`sheet.fix.by.${x}`))
  if (parts.length === 1) return parts[0] ?? ''
  return `${parts.slice(0, -1).join(', ')} ${t('sheet.fix.and')} ${parts[parts.length - 1]}`
}

function valueText(t: TFunction, value: number | null): string {
  return value === null ? t('sheet.fix.empty') : String(value)
}

/**
 * The boxes the board log and the video say otherwise, team by team: "Volero's box III/1 is 2,
 * not 5, and IV/1 is 5, not 6".
 */
export function fixBoxesText(t: TFunction, boxes: readonly Pick<RepairOut, 'box' | 'old' | 'new'>[], names: SideNames | null): string {
  const out: string[] = []
  for (const side of ['us', 'them'] as const) {
    const mine = boxes.filter((b) => b.box.side === side)
    if (mine.length === 0) continue
    const items = mine.map((b, k) =>
      t(k === 0 ? 'sheet.fix.firstBox' : 'sheet.fix.box', { box: boxName(b.box), new: valueText(t, b.new), old: valueText(t, b.old) }),
    )
    // Each item has a comma of its own ("is 2, not 5"): a comma before the last one too.
    const list = items.length > 1 ? `${items.slice(0, -1).join(', ')}, ${t('sheet.fix.and')} ${items[items.length - 1]}` : (items[0] ?? '')
    out.push(t('sheet.fix.team', { team: sideLabel(t, side, names), boxes: list }))
  }
  return out.join('; ')
}

/** The correction the scoring found for a set, in one sentence. */
export function correctionText(t: TFunction, c: CorrectionOut, names: SideNames | null): string {
  return t('sheet.fix.says', {
    from: `${c.from[0]}:${c.from[1]}`,
    to: `${c.to[0]}:${c.to[1]}`,
    sources: sourcesText(t, c.sources),
    boxes: fixBoxesText(t, c.boxes, names),
    count: c.sources.length,
  })
}

/** A set corrected from the paper earlier: "Volero III/1 5 → 2, IV/1 6 → 5", on whose word. */
export function correctedText(t: TFunction, notes: readonly CorrectionDoc[], names: SideNames | null): string {
  const out: string[] = []
  for (const side of ['us', 'them'] as const) {
    const mine = notes.filter((n) => n.side === side)
    if (mine.length === 0) continue
    const items = mine.map((n) => `${boxName(n)} ${valueText(t, n.paper)} → ${valueText(t, n.value)}`)
    out.push(`${sideLabel(t, side, names)} ${items.join(', ')}`)
  }
  const by = [...new Set(notes.flatMap((n) => n.by))]
  return t('sheet.fix.done', { sources: sourcesText(t, by), boxes: out.join('; ') })
}

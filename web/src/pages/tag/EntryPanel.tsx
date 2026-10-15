import '../../i18n/pages'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { cx } from '../../lib/cx'
import { parseEntry, type EntryError, type ParsedEntry } from '../../lib/grammar'
import { formatClock, gradeLabel, playerLabel, segmentKindLabel, sideLabel, skillLabel } from '../../lib/labels'
import { addPoint, scoreBefore } from '../../lib/rallies'
import type { RosterEntry, Taxonomy } from '../../lib/types'
import { useUi, useView, useWorkspace } from './context'
import type { Notice } from './controller'

function previewParts(t: TFunction, parsed: ParsedEntry, roster: readonly RosterEntry[]): string[] {
  const parts: string[] = []
  if (parsed.number !== null || parsed.side === 'them') parts.push(playerLabel(t, parsed.side, parsed.number, roster))
  else parts.push(t('tagging.entry.noNumber'))
  if (parsed.skill) parts.push(skillLabel(t, parsed.skill))
  if (parsed.grade) parts.push(`${parsed.grade} ${gradeLabel(t, parsed.skill, parsed.grade)}`)
  if (parsed.zoneFrom !== null) {
    parts.push(
      parsed.zoneTo !== null
        ? t('tagging.entry.zones', { from: parsed.zoneFrom, to: parsed.zoneTo })
        : t('tagging.entry.zone', { from: parsed.zoneFrom }),
    )
  }
  return parts
}

function nextHint(t: TFunction, parsed: ParsedEntry, taxonomy: Taxonomy): string {
  const basketball = taxonomy.sport === 'basketball'
  switch (parsed.expects) {
    case 'number':
      return t('tagging.entry.next.number')
    case 'skill':
      return t(basketball ? 'tagging.basketball.next.skill' : 'tagging.entry.next.skill', { skills: taxonomy.skills.map((s) => s.code).join(' ') })
    case 'grade':
      return basketball ? t('tagging.basketball.next.grade') : t('tagging.entry.next.grade', { grades: taxonomy.grades.join(' ') })
    case 'zone':
      return t('tagging.entry.next.zone')
    default:
      return t('tagging.entry.next.none')
  }
}

/** Why an entry can't be saved; basketball's codes have no letters-only or zone wording. */
function errorText(t: TFunction, error: EntryError | 'incomplete', code: string, taxonomy: Taxonomy): string {
  if (taxonomy.sport === 'basketball' && (error === 'unexpected' || error === 'skillMissing' || error === 'incomplete')) {
    return t(`tagging.basketball.errors.${error}`)
  }
  return t(`tagging.entry.errors.${error}`, { code })
}

function noticeText(t: TFunction, notice: Notice, taxonomy: Taxonomy): string {
  const p = notice.params ?? {}
  switch (notice.key) {
    case 'saved':
      return t('tagging.notice.saved', { entry: p.entry })
    case 'invalid':
      return t('tagging.notice.invalid', {
        entry: p.entry,
        reason: errorText(t, String(p.reason) as EntryError | 'incomplete', String(p.code ?? ''), taxonomy),
      })
    case 'rallyStarted':
      return t('tagging.notice.rallyStarted', { time: formatClock(Number(p.time)) })
    case 'rallyEnded':
      return p.score ? t('tagging.notice.rallyEndedScore', { score: p.score }) : t('tagging.notice.rallyEnded')
    case 'segmentAdded':
      return t('tagging.notice.segmentAdded', { kind: segmentKindLabel(t, String(p.kind) as 'timeout', taxonomy.sport) })
    case 'clipIn':
    case 'clipOut':
      return t(`tagging.notice.${notice.key}`, { time: formatClock(Number(p.time)) })
    case 'clipSet':
    case 'snapped':
    case 'deleted':
      return t(`tagging.notice.${notice.key}`, { entry: p.entry })
    default:
      return t(`tagging.notice.${notice.key}`)
  }
}

export function EntryPanel() {
  const { t } = useTranslation()
  const { taxonomy, controller, store } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const entry = ui.entry
  const parsed = entry ? parseEntry(entry.text, taxonomy, view.roster) : null
  const at = entry && parsed ? Math.max(0, entry.t_ms - (parsed.skill?.lead_ms ?? 0)) : null

  return (
    <section aria-label={t('tagging.entry.title')} className="flex flex-col gap-2 rounded-2xl border border-hairline bg-surface px-4 py-3">
      <div className="flex items-center gap-2">
        <div
          className={cx(
            'flex min-h-12 flex-1 items-center rounded-xl border-2 px-3 font-mono text-2xl font-bold tracking-wide',
            entry ? 'border-accent' : 'border-hairline text-muted',
          )}
          aria-hidden="true"
        >
          {entry ? (
            <>
              <span>{entry.text}</span>
              <span className="ml-0.5 inline-block h-7 w-0.5 bg-fg motion-safe:animate-pulse" />
            </>
          ) : (
            <span className="text-base font-normal">
              {taxonomy.sport === 'basketball' ? t('tagging.basketball.placeholder') : t('tagging.entry.placeholder')}
            </span>
          )}
        </div>
        {entry && (
          <Button variant="primary" disabled={!parsed?.complete} onClick={controller.saveEntry}>
            {t('tagging.entry.save')}
          </Button>
        )}
      </div>

      <div aria-live="polite" className="flex min-h-10 flex-col gap-1 text-sm">
        {entry && parsed ? (
          <>
            <p className="font-semibold">
              <span className="sr-only">{`${entry.text}: `}</span>
              {previewParts(t, parsed, view.roster).join(' · ')}
              {at !== null && <span className="font-normal text-muted tabular-nums">{` · ${formatClock(at)}`}</span>}
            </p>
            <p className="flex flex-wrap items-center gap-2">
              {parsed.unknownNumber && <Badge tone="danger">{t('tagging.entry.unknownNumber')}</Badge>}
              {parsed.error ? (
                <span className="text-danger">{errorText(t, parsed.error, parsed.skillCode ?? '', taxonomy)}</span>
              ) : (
                <span className="text-muted">{nextHint(t, parsed, taxonomy)}</span>
              )}
            </p>
          </>
        ) : (
          <p className="text-muted">{t('tagging.entry.hint')}</p>
        )}
      </div>

      {ui.proposal && <Proposal />}

      <NoticeLine notice={ui.notice} taxonomy={taxonomy} onUndo={() => store.getView().canUndo && controller.undo()} />

      <ul className="flex flex-wrap gap-x-3 gap-y-1 border-t border-hairline pt-2 text-xs text-muted" aria-label={t('tagging.entry.skills')}>
        {taxonomy.skills.map((s) => (
          <li key={s.code}>
            <span className="font-mono font-bold text-fg">{s.code}</span> {skillLabel(t, s)}
          </li>
        ))}
        <li>
          <span className="font-mono font-bold text-fg">{taxonomy.grades.join(' ')}</span>
        </li>
      </ul>
    </section>
  )
}

function NoticeLine({ notice, taxonomy, onUndo }: { notice: Notice | null; taxonomy: Taxonomy; onUndo: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="status" className="flex min-h-6 items-center justify-between gap-2 text-sm">
      {notice && (
        <>
          <span className={cx(notice.tone === 'error' ? 'text-danger' : 'text-muted')}>{noticeText(t, notice, taxonomy)}</span>
          {notice.undo && (
            <Button variant="ghost" onClick={onUndo}>
              {t('tagging.notice.undo')}
            </Button>
          )}
        </>
      )}
    </div>
  )
}

/** Ending the rally: winner preset from the tags, u/t to switch, Enter to confirm, Esc to keep it. */
function Proposal() {
  const { t } = useTranslation()
  const { controller } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const proposal = ui.proposal
  if (!proposal) return null
  const before = scoreBefore(proposal.rally.start_ms, view.periods, view.segments)
  const after = addPoint(before, proposal.winner)

  return (
    <div role="group" aria-labelledby="proposal-title" className="flex flex-col gap-2 rounded-xl border-2 border-accent bg-sunken p-3">
      <h3 id="proposal-title" className="font-semibold">
        {t('tagging.proposal.title')}
      </h3>
      <p className="text-sm tabular-nums">
        {proposal.winner ? t('tagging.proposal.point', { side: sideLabel(t, proposal.winner) }) : t('tagging.proposal.noPoint')}
        {before.period && ` · ${t('tagging.proposal.score', { us: after.us, them: after.them })}`}
        {` · ${t('tagging.proposal.at', { time: formatClock(proposal.end_ms) })}`}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button aria-pressed={proposal.winner === 'us'} variant={proposal.winner === 'us' ? 'primary' : 'secondary'} onClick={() => controller.setWinner('us')}>
          {t('tagging.proposal.us')}
        </Button>
        <Button aria-pressed={proposal.winner === 'them'} variant={proposal.winner === 'them' ? 'primary' : 'secondary'} onClick={() => controller.setWinner('them')}>
          {t('tagging.proposal.them')}
        </Button>
        <Button variant="primary" onClick={controller.confirmProposal}>
          {t('tagging.proposal.confirm')}
        </Button>
        <Button variant="ghost" onClick={controller.keepRallyOpen}>
          {t('tagging.proposal.keep')}
        </Button>
      </div>
      <p className="text-xs text-muted">{t('tagging.proposal.keys')}</p>
    </div>
  )
}

import '../i18n/pages'
import type { CSSProperties, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { EMPTY, formatStat, SETTER_ZONES, type SetFilter, type SetterLine, type SetterStats as Setter, type SetterZone } from '../lib/stats'
import { Card } from './Card'
import { ChartIcon } from './icons'
import { EmptyState } from './Message'
import { GROUP_START, H, NumberCell, RowName, STICKY, type CellTarget } from './StatsTable'
import { Table, TBody, THead } from './Table'

/** What a setter number does: play the sets matching the filter; null: just the number. */
export type SetterTarget = (filter: SetFilter) => CellTarget | null

interface SetterStatsProps {
  setter: Setter | null | undefined
  target: SetterTarget
  hint: string
  /** "Set 2" when narrowed to a set. */
  scope: string | null
  selected?: string | null
  onSelect?: (key: string) => void
}

type Group = 'by_attacker' | 'by_rotation' | 'by_reception' | 'by_setter'

/**
 * "Setter" (PLAN §9.7, step 5b): where our sets went (the zone and the player of the attack
 * after each) per rotation and per pass, and what came of them. Every number plays its sets.
 */
export function SetterStats({ setter, target, hint, scope, selected = null, onSelect }: SetterStatsProps) {
  const { t } = useTranslation()
  if (!setter || setter.sets === 0) {
    return (
      <EmptyState icon={<ChartIcon />} title={t('stats.setter.emptyTitle')}>
        {t('stats.setter.emptyBody')}
      </EmptyState>
    )
  }
  const title = scope === null ? t('stats.setter.title') : `${t('stats.setter.title')} · ${scope}`
  const without = setter.sets - setter.attacks
  const common = { setter, target, selected, onSelect }
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h3 className="font-semibold">{title}</h3>
        <p className="text-sm text-muted">{hint}</p>
        <p className="flex flex-wrap items-center gap-x-1.5 text-sm font-semibold tabular-nums" role="status">
          {t('stats.setter.count', { sets: setter.sets, attacks: setter.attacks })}
          {without > 0 && (
            <>
              <span aria-hidden="true" className="text-muted">
                ·
              </span>
              <PlayNumber
                text={`${t('stats.setter.outcomes.none')}: ${without}`}
                label={t('stats.setter.noAttack')}
                value={String(without)}
                target={target({ attacked: false })}
                selected={selected === 'setter|none'}
                onSelect={() => onSelect?.('setter|none')}
              />
            </>
          )}
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,21rem)_minmax(0,1fr)] lg:items-start">
        <ZoneCourt {...common} />
        <div className="flex min-w-0 flex-col gap-2">
          <h4 className="text-sm font-semibold">{t('stats.setter.byAttacker')}</h4>
          <LineTable group="by_attacker" lines={setter.by_attacker} first={t('stats.setter.attacker')} zones={false} {...common} />
        </div>
      </div>
      <div className="grid gap-6 2xl:grid-cols-2">
        {setter.by_rotation.length > 0 && (
          <div className="flex min-w-0 flex-col gap-2">
            <h4 className="text-sm font-semibold">{t('stats.setter.byRotation')}</h4>
            <LineTable group="by_rotation" lines={setter.by_rotation} first={t('stats.rotations.rotation')} zones {...common} />
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-2">
          <h4 className="text-sm font-semibold">{t('stats.setter.byReception')}</h4>
          <LineTable group="by_reception" lines={setter.by_reception} first={t('stats.setter.firstContact')} zones {...common} />
          <p className="text-xs text-muted">{t('stats.setter.fromQualities')}</p>
        </div>
      </div>
      {setter.by_setter.length > 1 && (
        <div className="flex min-w-0 flex-col gap-2">
          <h4 className="text-sm font-semibold">{t('stats.setter.bySetter')}</h4>
          <LineTable group="by_setter" lines={setter.by_setter} first={t('stats.setter.setterName')} zones {...common} />
        </div>
      )}
    </div>
  )
}

interface CommonProps {
  setter: Setter
  target: SetterTarget
  selected: string | null
  onSelect?: (key: string) => void
}

/** A number outside a table: a button when there are plays behind it. */
function PlayNumber({ text, label, value, target, selected, onSelect, className }: { text: ReactNode; label: string; value: string; target: CellTarget | null; selected: boolean; onSelect?: () => void; className?: string }) {
  const { t } = useTranslation()
  if (target === null || target.kind !== 'play') return <span className={className}>{text}</span>
  return (
    <button
      type="button"
      aria-label={t('stats.play', { what: label, value })}
      aria-pressed={selected}
      onClick={() => {
        onSelect?.()
        target.onPlay()
      }}
      className={cx('link inline-flex min-h-11 cursor-pointer items-center font-semibold sm:min-h-8', selected && 'rounded-sm bg-accent-soft text-fg', className)}
    >
      {text}
    </button>
  )
}

/** The zone's tint from its `--heat` (0..1): the points colour mixed into the surface. */
const HEAT =
  'bg-[color-mix(in_oklab,var(--color-outcome-win)_calc(10%+var(--heat)*42%),var(--color-surface))] dark:bg-[color-mix(in_oklab,var(--color-outcome-win)_calc(8%+var(--heat)*30%),var(--color-surface))]'

/** Our half from behind, the net at the top: the share of the attacks after a set from each zone. */
function ZoneCourt({ setter, target, selected, onSelect }: CommonProps) {
  const { t } = useTranslation()
  const lines = new Map(setter.by_zone.map((line) => [line.key, line]))
  const unknown = lines.get('unknown')
  const known = SETTER_ZONES.some((zone) => (lines.get(zone)?.attacks ?? 0) > 0)
  const most = Math.max(1, ...SETTER_ZONES.map((zone) => lines.get(zone)?.attacks ?? 0))
  const zone = (key: SetterZone, className: string) => {
    const line = lines.get(key)
    const count = line?.attacks ?? 0
    const share = setter.attacks > 0 ? count / setter.attacks : 0
    // A heat map: the zone with the most attacks is tinted most (HEAT, lighter in dark mode).
    const heat = count / most
    const name = t(`stats.setter.zones.${key}`)
    const label = t('stats.setter.zoneLabel', {
      zone: name,
      share: formatStat(share, 'pct'),
      count,
      kill: formatStat(line?.kill_pct ?? null, 'pct'),
      hit: formatStat(line?.hit_pct ?? null, 'pct'),
    })
    const cell = `setter|zone|${key}`
    const play = count > 0 ? target({ attacked: true, zone: key }) : null
    const body = (
      <span className="flex flex-col items-center gap-0.5">
        <span className="text-[0.7rem] font-semibold tracking-wide uppercase">{name}</span>
        <span className="text-xl leading-tight font-bold tabular-nums sm:text-2xl">{count > 0 ? formatStat(share, 'pct') : EMPTY}</span>
        {count > 0 ? (
          <>
            <span className="text-xs tabular-nums">{t('stats.setter.attacksN', { count })}</span>
            <span className="text-xs tabular-nums">{t('stats.setter.killShort', { pct: formatStat(line?.kill_pct ?? null, 'pct') })}</span>
          </>
        ) : (
          <span className="text-xs text-muted">{t(`stats.setter.zoneHint.${key}`)}</span>
        )}
      </span>
    )
    const classes = cx('relative flex items-center justify-center overflow-hidden p-1.5 text-center', count > 0 ? HEAT : 'bg-surface', className)
    const style = { '--heat': heat } as CSSProperties
    if (play === null || play.kind !== 'play') {
      return (
        <div className={classes} style={style} role="img" aria-label={label}>
          {body}
        </div>
      )
    }
    return (
      <button
        type="button"
        style={style}
        aria-label={t('stats.play', { what: label, value: count })}
        aria-pressed={selected === cell}
        onClick={() => {
          onSelect?.(cell)
          play.onPlay()
        }}
        className={cx(
          classes,
          'cursor-pointer hover:bg-hover focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
          selected === cell && 'outline-2 -outline-offset-2 outline-ring',
        )}
      >
        {body}
      </button>
    )
  }
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="text-sm font-semibold">{t('stats.setter.court')}</figcaption>
      {known ? (
        <div className="rounded-card bg-sunken p-2.5">
          <div aria-hidden="true" className="mx-[-0.25rem] mb-0.5 h-1.5 rounded-full bg-fg" />
          <div className="grid grid-cols-3 border-x border-b border-court-line">
            {zone('left', 'aspect-square border-r border-b-2 border-court-line')}
            {zone('middle', 'aspect-square border-r border-b-2 border-court-line')}
            {zone('right', 'aspect-square border-b-2 border-court-line')}
            {zone('back', 'col-span-3 min-h-28')}
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted">{t('stats.setter.noZones')}</p>
      )}
      {unknown && unknown.attacks > 0 && (
        <p className="text-sm text-muted">
          {t('stats.setter.zones.unknown')}:{' '}
          <PlayNumber
            text={String(unknown.attacks)}
            label={t('stats.setter.zones.unknown')}
            value={String(unknown.attacks)}
            target={target({ attacked: true, zone: null })}
            selected={selected === 'setter|zone|unknown'}
            onSelect={() => onSelect?.('setter|zone|unknown')}
            className="tabular-nums"
          />
        </p>
      )}
    </figure>
  )
}

const OUTCOME_COLUMNS = [
  ['kills', { outcome: 'kill' }],
  ['errors', { outcome: 'error' }],
  ['blocked', { outcome: 'blocked' }],
] as const

/** The rows of one breakdown (attackers, rotations, passes, setters) with their numbers. */
function LineTable({ group, lines, first, zones, target, selected, onSelect }: Omit<CommonProps, 'setter'> & { group: Group; lines: readonly SetterLine[]; first: string; zones: boolean }) {
  const { t } = useTranslation()
  const unknownZone = zones && lines.some((l) => (l.zones.unknown ?? 0) > 0)
  const zoneKeys: readonly (SetterZone | 'unknown')[] = zones ? [...SETTER_ZONES, ...(unknownZone ? (['unknown'] as const) : [])] : []
  const caption = t(`stats.setter.${{ by_attacker: 'byAttacker', by_rotation: 'byRotation', by_reception: 'byReception', by_setter: 'bySetter' }[group]}` as 'stats.setter.byAttacker')
  const label = (line: SetterLine): ReactNode => {
    if (group === 'by_rotation') return line.key === 'unknown' ? t('stats.setter.rotationUnknown') : t('stats.rotations.rotationNo', { n: line.key })
    if (group === 'by_reception') return t(`stats.setter.qualities.${line.key}` as 'stats.setter.qualities.perfect')
    const kind = line.number !== null ? 'player' : line.key === 'none' ? 'no_number' : 'other'
    return <RowName row={{ kind, number: line.number, name: line.name }} />
  }
  const text = (line: SetterLine): string => {
    if (group === 'by_rotation' || group === 'by_reception') return String(label(line))
    return line.number !== null ? [`#${line.number}`, line.name].filter(Boolean).join(' ') : String(t(`stats.rows.${line.key === 'none' ? 'no_number' : 'other'}`))
  }
  /** A number; `plays`: how many sets are behind it (none: just the number). */
  const cell = (line: SetterLine, key: string, value: number | null, format: 'count' | 'pct', plays: number, filter: SetFilter, column: string, extra?: string) => {
    const id = `setter|${group}|${line.key}|${key}`
    const shown = formatStat(value, format)
    return (
      <NumberCell
        key={key}
        value={value}
        text={extra ? `${shown} ${extra}` : shown}
        label={t('stats.playLabel', { row: text(line), column })}
        target={value !== null && plays > 0 ? target({ ...line.filter, ...filter }) : null}
        selected={selected === id}
        onSelect={() => onSelect?.(id)}
        className={cx('px-2', format === 'pct' && 'font-semibold', (key === 'kills' || key === 'kill_pct' || key === zoneKeys[0]) && GROUP_START)}
      />
    )
  }
  return (
    <Card className="w-fit max-w-full overflow-hidden">
      <Table caption={caption}>
        <THead>
          <tr>
            <H className={cx(STICKY, 'border-r bg-sunken pr-3 pl-4 text-left')}>{first}</H>
            {(['sets', 'attacks', 'share'] as const).map((key) => (
              <H key={key} className="px-2 text-right">
                <abbr title={t(`stats.setter.long.${key}`)} className="no-underline">
                  {t(`stats.setter.columns.${key}`)}
                </abbr>
              </H>
            ))}
            {zoneKeys.map((zone, i) => (
              <H key={zone} className={cx('px-2 text-right', i === 0 && GROUP_START)}>
                <abbr title={t('stats.setter.long.zone')} className="no-underline">
                  {t(`stats.setter.zones.${zone}`)}
                </abbr>
              </H>
            ))}
            {(['kills', 'errors', 'blocked', 'kill_pct', 'hit_pct'] as const).map((key) => (
              <H key={key} className={cx('px-2 text-right', key === 'kills' && GROUP_START)}>
                <abbr title={t(`stats.setter.long.${key}`)} className="no-underline">
                  {t(`stats.setter.columns.${key}`)}
                </abbr>
              </H>
            ))}
          </tr>
        </THead>
        <TBody>
          {lines.map((line) => (
            <tr key={line.key} className={cx(line.sets === 0 && 'text-muted')}>
              <th scope="row" className={cx(STICKY, 'max-w-44 border-r border-hairline bg-surface px-3 py-2.5 text-left font-normal whitespace-nowrap first:pl-4 sm:max-w-60', 'in-[tr:hover]:tint-hover')}>
                <span className="flex min-w-0">{label(line)}</span>
              </th>
              {cell(line, 'sets', line.sets, 'count', line.sets, {}, t('stats.setter.long.sets'))}
              {cell(line, 'attacks', line.attacks, 'count', line.attacks, { attacked: true }, t('stats.setter.long.attacks'))}
              <td className="px-2 py-2.5 text-right whitespace-nowrap tabular-nums">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className="hidden h-1.5 w-12 overflow-hidden rounded-full bg-sunken sm:inline-block">
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round((line.share ?? 0) * 100)}%` }} />
                  </span>
                  {formatStat(line.attacks > 0 ? line.share : null, 'pct')}
                </span>
              </td>
              {zoneKeys.map((zone) => {
                const count = line.zones[zone] ?? 0
                const share = line.attacks > 0 ? count / line.attacks : null
                return cell(line, zone, share, 'pct', count, { attacked: true, zone: zone === 'unknown' ? null : zone }, t(`stats.setter.zones.${zone}`), count > 0 ? `(${count})` : undefined)
              })}
              {OUTCOME_COLUMNS.map(([key, filter]) => cell(line, key, line.attacks > 0 ? line[key] : null, 'count', line[key], { attacked: true, ...filter }, t(`stats.setter.long.${key}`)))}
              {cell(line, 'kill_pct', line.kill_pct, 'pct', line.attacks, { attacked: true }, t('stats.setter.long.kill_pct'))}
              {cell(line, 'hit_pct', line.hit_pct, 'pct', line.attacks, { attacked: true }, t('stats.setter.long.hit_pct'))}
            </tr>
          ))}
        </TBody>
      </Table>
    </Card>
  )
}

import '../i18n/pages'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { initialName, ROMAN, signed, ZONE_SPOTS } from '../lib/rotations'
import { columnGroups, EMPTY, formatStat, type RotationLine, type StatColumn, type StatValue } from '../lib/stats'
import { Card } from './Card'
import { SyncIcon } from './icons'
import { EmptyState } from './Message'
import { GROUP_START, H, NumberCell, STICKY, type CellTarget, type TeamKey } from './StatsTable'
import { Table, TBody, THead } from './Table'

/** The team numbers per rotation, in groups: points, side-out, break-point. */
const TEAM_GROUPS: readonly { group: string; keys: readonly TeamKey[] }[] = [
  { group: 'points', keys: ['won', 'lost'] },
  { group: 'side_out', keys: ['side_out_of', 'side_out_won', 'side_out_pct'] },
  { group: 'break_point', keys: ['break_point_of', 'break_point_won', 'break_point_pct'] },
]

export interface RotationTargets {
  /** What a team number does (won, side-outs, …); null: just the number. */
  team: (line: RotationLine, key: TeamKey) => CellTarget | null
  /** What a number of our tags does (serve efficiency, hitting, …). */
  skill: (line: RotationLine, column: StatColumn) => CellTarget | null
}

interface RotationStatsProps extends RotationTargets {
  rotations: readonly RotationLine[] | null | undefined
  /** The numbers of our tags per rotation (the server's `rotation_columns`). */
  columns: readonly StatColumn[]
  /** "Set 2" when narrowed to a set. */
  scope: string | null
  /** Shirt number → name, for the charts. */
  names: ReadonlyMap<number, string>
  hint: string
  /** Next to the empty state: where the starting rotations are entered. */
  emptyAction?: ReactNode
  /** Whether the numbers play their rallies (the match), or are plain (the season). */
  playable?: boolean
  selected?: string | null
  onSelect?: (key: string) => void
}

/**
 * "Rotations" (PLAN §9.7, step 5a): our points, side-out and break-point per rotation (our
 * setter's zone), with our serve, reception and attack in it; a court per rotation shows our
 * six at the serve. Every number plays its rallies (or our tags in them).
 */
export function RotationStats({ rotations, columns, scope, names, hint, emptyAction, playable = true, selected = null, onSelect, ...targets }: RotationStatsProps) {
  const { t } = useTranslation()
  if (!rotations) {
    return (
      <EmptyState icon={<SyncIcon />} title={t('stats.rotations.emptyTitle')} action={emptyAction}>
        {t('stats.rotations.emptyBody')}
      </EmptyState>
    )
  }
  const caption = scope === null ? t('stats.rotations.title') : `${t('stats.rotations.title')} · ${scope}`
  const lineups = rotations.some((r) => r.lineup)
  const varies = rotations.some((r) => r.lineup_varies)
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h3 className="font-semibold">{caption}</h3>
        <p className="text-sm text-muted">{hint}</p>
        <RotationTable caption={caption} rotations={rotations} columns={columns} selected={selected} onSelect={onSelect} {...targets} />
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="font-semibold">{t('stats.rotations.charts')}</h3>
        <p className="text-sm text-muted">
          {[t('stats.rotations.chartsHint'), playable ? t('stats.rotations.chartsPlay') : null, lineups ? (varies ? t('stats.rotations.varies') : null) : t('stats.rotations.noLineup')]
            .filter(Boolean)
            .join(' ')}
        </p>
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label={t('stats.rotations.charts')}>
          {rotations.map((line) => (
            <li key={line.rotation}>
              <RotationChart line={line} columns={columns} names={names} selected={selected} onSelect={onSelect} {...targets} />
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/** The rotation table: a row per rotation, the team numbers, then our serve, reception and attack. */
function RotationTable({
  caption,
  rotations,
  columns,
  team,
  skill,
  selected,
  onSelect,
}: { caption: string; rotations: readonly RotationLine[]; columns: readonly StatColumn[]; selected: string | null; onSelect?: (key: string) => void } & RotationTargets) {
  const { t } = useTranslation()
  const groups = columnGroups(columns)
  const head = (key: string) => (
    <abbr title={t(`stats.long.${key}` as 'stats.long.points')} className="no-underline">
      {t(`stats.columns.${key}` as 'stats.columns.points')}
    </abbr>
  )
  return (
    <Card className="w-fit max-w-full overflow-hidden">
      <Table caption={caption}>
        <THead>
          <tr>
            <H rowSpan={2} className={cx(STICKY, 'border-r bg-sunken pr-3 pl-4 text-left align-bottom')}>
              {t('stats.rotations.rotation')}
            </H>
            <H rowSpan={2} className="px-3 text-right align-bottom">
              <abbr title={t('stats.rotations.rallies')} className="no-underline">
                {t('stats.rotations.ralliesShort')}
              </abbr>
            </H>
            {TEAM_GROUPS.map(({ group, keys }) => (
              <H key={group} scope="colgroup" colSpan={keys.length} className={cx(GROUP_START, 'px-1.5 pb-1 text-left text-fg sm:text-center')}>
                {t(`stats.rotations.groups.${group}` as 'stats.rotations.groups.points')}
              </H>
            ))}
            {groups.map((group) => (
              <H key={group.group} scope="colgroup" colSpan={group.columns.length} className={cx(GROUP_START, 'px-1.5 pb-1 text-left text-fg sm:text-center')}>
                {t(`stats.groups.${group.group}` as 'stats.groups.serve')}
              </H>
            ))}
          </tr>
          <tr>
            {TEAM_GROUPS.map(({ keys }) =>
              keys.map((key, i) => (
                <H key={key} className={cx('px-2 pt-1 text-right', i === 0 && GROUP_START)}>
                  <abbr title={t(`stats.team.long.${key}`)} className="no-underline">
                    {t(`stats.team.columns.${key}`)}
                  </abbr>
                </H>
              )),
            )}
            {groups.map((group) =>
              group.columns.map((column, i) => (
                <H key={column.key} className={cx('px-1.5 pt-1 text-right', i === 0 && GROUP_START)}>
                  {head(column.key)}
                </H>
              )),
            )}
          </tr>
        </THead>
        <TBody>
          {rotations.map((line) => {
            const name = t('stats.rotations.rotationNo', { n: line.rotation })
            const rowKey = `rot${line.rotation}`
            return (
              <tr key={line.rotation}>
                <th scope="row" className={cx(STICKY, 'border-r border-hairline bg-surface px-3 py-2.5 text-left font-normal whitespace-nowrap first:pl-4', 'in-[tr:hover]:tint-hover')}>
                  {name}
                </th>
                <td className="px-3 py-2.5 text-right tabular-nums">{line.rallies}</td>
                {TEAM_GROUPS.map(({ keys }) =>
                  keys.map((key, i) => {
                    const value = line[key]
                    const pct = key.endsWith('_pct')
                    const cell = `${rowKey}|${key}`
                    return (
                      <NumberCell
                        key={key}
                        value={value}
                        text={formatStat(value, pct ? 'pct' : 'count')}
                        label={t('stats.playLabel', { row: name, column: t(`stats.team.long.${key}`) })}
                        target={value !== null && value !== 0 ? team(line, key) : null}
                        selected={selected === cell}
                        onSelect={() => onSelect?.(cell)}
                        className={cx('px-2', i === 0 && GROUP_START, pct && 'font-semibold')}
                      />
                    )
                  }),
                )}
                {groups.map((group) =>
                  group.columns.map((column, i) => {
                    const value = line.values?.[column.key] ?? null
                    const cell = `${rowKey}|${column.key}`
                    return (
                      <NumberCell
                        key={column.key}
                        value={value}
                        text={formatStat(value, column.format)}
                        label={t('stats.playLabel', { row: name, column: t(`stats.long.${column.key}` as 'stats.long.points') })}
                        target={playable(value, column) ? skill(line, column) : null}
                        selected={selected === cell}
                        onSelect={() => onSelect?.(cell)}
                        className={cx('px-1.5', i === 0 && GROUP_START, column.format !== 'count' && 'font-semibold')}
                      />
                    )
                  }),
                )}
              </tr>
            )
          })}
        </TBody>
      </Table>
    </Card>
  )
}

/** A number has moments behind it unless it is empty or counts none of them. */
function playable(value: StatValue, column: Pick<StatColumn, 'format' | 'grades'>): boolean {
  if (value === null) return false
  return value !== 0 || (column.grades === null && column.format !== 'count')
}

/** A number in a chart: a button when it has moments behind it. */
function ChartNumber({ text, label, target, selected, onSelect, className }: { text: string; label: string; target: CellTarget | null; selected: boolean; onSelect?: () => void; className?: string }) {
  const { t } = useTranslation()
  if (target === null || target.kind !== 'play') return <span className={cx('tabular-nums', className)}>{text}</span>
  return (
    <button
      type="button"
      aria-label={t('stats.play', { what: label, value: text })}
      aria-pressed={selected}
      onClick={() => {
        onSelect?.()
        target.onPlay()
      }}
      className={cx(
        'link -my-1 inline-flex min-h-11 cursor-pointer items-center px-0.5 font-semibold tabular-nums sm:min-h-8',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        selected && 'rounded-sm bg-accent-soft text-fg',
        className,
      )}
    >
      {text}
    </button>
  )
}

const KEY_SKILLS = ['serve_eff', 'rec_positive', 'att_hit'] as const

/** One rotation: our six on the court at the serve and the rotation's key numbers. */
function RotationChart({
  line,
  columns,
  names,
  team,
  skill,
  selected,
  onSelect,
}: { line: RotationLine; columns: readonly StatColumn[]; names: ReadonlyMap<number, string>; selected: string | null; onSelect?: (key: string) => void } & RotationTargets) {
  const { t } = useTranslation()
  const r = line.rotation
  const lineup = line.lineup ?? null
  const setter = lineup?.[r - 1] ?? null
  const name = t('stats.rotations.rotationNo', { n: r })
  const rowKey = `rot${r}`
  const diff = line.won - line.lost
  const num = (key: TeamKey, text?: string) => {
    const value = line[key]
    const cell = `${rowKey}|${key}`
    return (
      <ChartNumber
        text={text ?? formatStat(value, key.endsWith('_pct') ? 'pct' : 'count')}
        label={t('stats.playLabel', { row: name, column: t(`stats.team.long.${key}`) })}
        target={value !== null && value !== 0 ? team(line, key) : null}
        selected={selected === cell}
        onSelect={() => onSelect?.(cell)}
      />
    )
  }
  const share = (pct: 'side_out_pct' | 'break_point_pct', won: 'side_out_won' | 'break_point_won', of: 'side_out_of' | 'break_point_of') => (
    <span className="inline-flex items-baseline justify-end gap-x-1.5 whitespace-nowrap">
      {num(pct)}
      <span className="text-xs text-muted">
        {line[of] > 0 ? (
          <>
            {num(won, String(line[won]))}/{num(of, String(line[of]))}
          </>
        ) : (
          '0/0'
        )}
      </span>
    </span>
  )
  return (
    <Card className="flex h-full flex-col gap-3 p-3 sm:p-4" data-rotation={r}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <h4 className="flex items-baseline gap-2">
            <span className="text-2xl leading-none font-bold tabular-nums">{`P${r}`}</span>
            <span className="text-sm font-semibold">{name}</span>
          </h4>
          <p className="text-xs text-muted">
            {setter !== null ? t('stats.rotations.setterNo', { number: setter }) : t('stats.rotations.setterIn', { zone: ROMAN[r - 1] })}
          </p>
        </div>
        <span
          className={cx(
            'rounded-full px-2 py-0.5 text-sm font-bold tabular-nums',
            line.rallies === 0 ? 'bg-sunken text-muted' : diff > 0 ? 'bg-accent-soft text-on-accent-soft' : diff < 0 ? 'bg-danger/10 text-danger' : 'bg-sunken text-fg',
          )}
          title={t('stats.rotations.diff')}
        >
          {line.rallies === 0 ? EMPTY : signed(diff)}
        </span>
      </div>
      <div className="flex items-start gap-2.5 sm:gap-4">
        <Court rotation={r} lineup={lineup} names={names} />
        <dl className="grid min-w-0 flex-1 grid-cols-[auto_1fr] items-baseline gap-x-2 gap-y-0.5 text-sm sm:gap-x-3">
          <dt className="text-muted">{t('stats.rotations.key.won')}</dt>
          <dd className="text-right">{num('won')}</dd>
          <dt className="text-muted">{t('stats.rotations.key.lost')}</dt>
          <dd className="text-right">{num('lost')}</dd>
          <dt className="text-muted">{t('stats.rotations.key.side_out_pct')}</dt>
          <dd className="text-right">{share('side_out_pct', 'side_out_won', 'side_out_of')}</dd>
          <dt className="text-muted">{t('stats.rotations.key.break_point_pct')}</dt>
          <dd className="text-right">{share('break_point_pct', 'break_point_won', 'break_point_of')}</dd>
          {KEY_SKILLS.map((key) => {
            const column = columns.find((c) => c.key === key)
            if (!column) return null
            const value = line.values?.[key] ?? null
            const cell = `${rowKey}|${key}`
            return [
              <dt key={`${key}-t`} className="text-muted">
                {t(`stats.rotations.key.${key}`)}
              </dt>,
              <dd key={`${key}-d`} className="text-right">
                <ChartNumber
                  text={formatStat(value, column.format)}
                  label={t('stats.playLabel', { row: name, column: t(`stats.long.${key}`) })}
                  target={playable(value, column) ? skill(line, column) : null}
                  selected={selected === cell}
                  onSelect={() => onSelect?.(cell)}
                  className={value === null ? 'text-muted' : undefined}
                />
              </dd>,
            ]
          })}
        </dl>
      </div>
    </Card>
  )
}

/** Our half seen from behind our end line, the net at the top: who stands in which zone. */
function Court({ rotation, lineup, names }: { rotation: number; lineup: readonly (number | null)[] | null; names: ReadonlyMap<number, string> }) {
  const { t } = useTranslation()
  const who = (zone: number): string | null => {
    const number = lineup?.[zone] ?? null
    if (number === null) return null
    const name = names.get(number)
    return name ? `#${number} ${initialName(name)}` : `#${number}`
  }
  return (
    <svg
      viewBox="-0.6 -1 10.2 7.6"
      role="img"
      aria-label={[
        t('stats.rotations.court', { n: rotation }),
        ...ZONE_SPOTS.map((_, zone) => {
          const w = who(zone)
          const label = w ? t('stats.rotations.position', { zone: ROMAN[zone], who: w }) : t('stats.rotations.positionEmpty', { zone: ROMAN[zone] })
          return zone === rotation - 1 ? `${label} (${t('stats.setter.setterName')})` : label
        }),
      ].join('. ')}
      className="w-28 shrink-0 select-none sm:w-36"
      preserveAspectRatio="xMidYMin meet"
      data-testid="rotation-court"
    >
      <rect x={-0.6} y={-1} width={10.2} height={7.6} rx={0.5} fill="var(--color-sunken)" />
      <rect x={0} y={0} width={9} height={6} fill="var(--color-court-floor)" stroke="var(--color-court-line)" strokeWidth={0.08} />
      <line x1={0} y1={3} x2={9} y2={3} stroke="var(--color-court-line)" strokeWidth={0.08} />
      {[3, 6].map((x) => (
        <line key={x} x1={x} y1={0} x2={x} y2={6} stroke="var(--color-court-line)" strokeWidth={0.04} strokeDasharray="0.25 0.25" opacity={0.6} />
      ))}
      <line x1={-0.4} y1={-0.05} x2={9.4} y2={-0.05} stroke="var(--color-fg)" strokeWidth={0.22} strokeLinecap="round" />
      {ZONE_SPOTS.map(([x, y], zone) => {
        const setter = zone === rotation - 1
        const number = lineup?.[zone] ?? null
        const known = number !== null
        return (
          <g key={zone} data-zone={zone + 1} data-setter={setter || undefined}>
            <text x={x - 1.32} y={y + 1.28} fontSize={0.6} fill="var(--color-muted)">
              {ROMAN[zone]}
            </text>
            <circle
              cx={x}
              cy={y}
              r={1}
              fill={setter ? 'var(--color-accent)' : known ? 'var(--color-surface)' : 'transparent'}
              stroke={setter ? 'var(--color-accent)' : 'var(--color-court-line)'}
              strokeWidth={0.09}
              strokeDasharray={!setter && !known ? '0.3 0.2' : undefined}
            />
            {(known || setter) && (
              <text
                data-label
                x={x}
                y={y}
                dy="0.36em"
                textAnchor="middle"
                fontSize={known ? (String(number).length > 1 ? 0.95 : 1.1) : 0.9}
                fontWeight={700}
                fill={setter ? 'var(--color-on-accent)' : 'var(--color-fg)'}
              >
                {known ? number : t('stats.rotations.setter')}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

import '../i18n/pages'
import type { ComponentProps, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { cx } from '../lib/cx'
import {
  columnGroups,
  EMPTY,
  formatStat,
  rowKey,
  rowLabel,
  type StatColumn,
  type StatRow,
  type StatValue,
  type TeamLine,
} from '../lib/stats'
import { Card } from './Card'
import { Table, TBody, THead } from './Table'

/** What a number does when clicked: play its moments here, or open a page (a reel). */
export type CellTarget = { kind: 'play'; onPlay: () => void } | { kind: 'link'; to: string }

const STICKY = 'sticky left-0 z-[1]'

/** A header cell; unlike Th it brings no padding of its own, so the numbers can sit tight. */
function H({ className, scope = 'col', ...props }: ComponentProps<'th'>) {
  return <th scope={scope} className={cx('border-b border-hairline py-2.5 text-xs font-semibold whitespace-nowrap text-muted last:pr-4', className)} {...props} />
}
const GROUP_START = 'border-l border-hairline'

/** Counts sit close together; percentages and ratings get a little more air. */
function pad(column: Pick<StatColumn, 'format'>): string {
  return column.format === 'count' ? 'px-1' : 'px-1.5'
}

/** A number has moments to show unless it is empty or counts none of them. */
function hasPlays(value: StatValue, column: Pick<StatColumn, 'format' | 'grades'>): boolean {
  if (value === null) return false
  return value !== 0 || (column.grades === null && column.format !== 'count')
}

interface NumberCellProps {
  value: StatValue
  text: string
  label: string
  target: CellTarget | null
  selected: boolean
  className?: string
  onSelect?: () => void
}

/** A number in a table: a link when it has moments behind it, "–" when there were no attempts. */
export function NumberCell({ value, text, label, target, selected, className, onSelect }: NumberCellProps) {
  const { t } = useTranslation()
  const base = cx('text-right tabular-nums whitespace-nowrap first:pl-4 last:pr-4', selected && 'bg-accent-soft', className)
  if (target === null) return <td className={cx(base, 'py-2.5', value === null && 'text-muted')}>{text}</td>
  const classes = cx(
    'link inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-end px-0.5 font-semibold sm:min-h-9 sm:min-w-0',
    selected && 'text-fg',
  )
  return (
    <td className={cx(base, 'py-0')}>
      {target.kind === 'play' ? (
        <button
          type="button"
          className={classes}
          aria-label={t('stats.play', { what: label, value: text })}
          aria-pressed={selected}
          onClick={() => {
            onSelect?.()
            target.onPlay()
          }}
        >
          {text}
        </button>
      ) : (
        <Link to={target.to} className={classes} aria-label={t('stats.open', { what: label, value: text })}>
          {text}
        </Link>
      )}
    </td>
  )
}

/** "#7 Alex Muster", "#11", "Team", "Opponent". */
/** "Jonas Keller" → "J. Keller"; a single name stays as it is. */
function shortName(name: string): string {
  const parts = name.trim().split(/\s+/)
  if (parts.length < 2) return name
  return `${parts[0]?.charAt(0) ?? ''}. ${parts.slice(1).join(' ')}`
}

export function RowName({ row }: { row: Pick<StatRow, 'kind' | 'number' | 'name'> }) {
  const { t } = useTranslation()
  if (row.kind !== 'player') return <span className="font-semibold">{t(`stats.rows.${row.kind}`)}</span>
  const short = row.name ? shortName(row.name) : null
  return (
    <span className="inline-flex min-w-0 items-baseline gap-1.5" title={row.name ?? undefined}>
      {row.number !== null && <span className="text-muted tabular-nums">{`#${row.number}`}</span>}
      {row.name && (
        <>
          {/* Phones: "J. Keller", so the name column leaves room for the numbers. */}
          <span className="min-w-0 truncate font-semibold sm:hidden">{short}</span>
          <span className="hidden min-w-0 truncate font-semibold sm:inline">{row.name}</span>
        </>
      )}
    </span>
  )
}

interface StatsTableProps {
  caption: string
  columns: readonly StatColumn[]
  players: readonly StatRow[]
  team: StatRow
  opponent: StatRow
  /** Season: the number of matches each row has tags in. */
  showMatches?: boolean
  /** What a number does; null: just the number. */
  target: (row: StatRow, column: StatColumn) => CellTarget | null
  /** The number last opened (`rowKey|column`), highlighted. */
  selected?: string | null
  onSelect?: (key: string) => void
}

/**
 * The box score: a row per player, the team and the opponent at the bottom; the columns in
 * groups (serve, reception, attack, block, other). The name column stays put while the numbers
 * scroll sideways inside the table on narrow screens.
 */
export function StatsTable({ caption, columns, players, team, opponent, showMatches = false, target, selected = null, onSelect }: StatsTableProps) {
  const { t } = useTranslation()
  const groups = columnGroups(columns)
  const head = (column: { key: string }) => (
    <abbr title={t(`stats.long.${column.key}` as 'stats.long.points')} className="no-underline">
      {t(`stats.columns.${column.key}` as 'stats.columns.points')}
    </abbr>
  )

  const line = (row: StatRow, tone: 'player' | 'team' | 'opponent') => {
    const key = rowKey(row)
    const name = rowLabel(t, row)
    return (
      <tr key={key} className={cx(tone === 'team' && 'bg-sunken', tone === 'opponent' && 'text-muted')}>
        <th
          scope="row"
          className={cx(
            STICKY,
            'max-w-40 border-r border-hairline px-3 py-2.5 text-left font-normal whitespace-nowrap first:pl-4 sm:max-w-60',
            tone === 'team' ? 'bg-sunken' : 'bg-surface',
            'in-[tr:hover]:tint-hover',
          )}
        >
          <span className="flex min-w-0">
            <RowName row={row} />
          </span>
        </th>
        {showMatches && <td className="px-1 py-2.5 text-right text-muted tabular-nums">{row.matches ?? EMPTY}</td>}
        {groups.map((group) =>
          group.columns.map((column, i) => {
            const value = row.values[column.key] ?? null
            const cell = `${key}|${column.key}`
            return (
              <NumberCell
                key={column.key}
                value={value}
                text={formatStat(value, column.format)}
                label={t('stats.playLabel', { row: name, column: t(`stats.long.${column.key}` as 'stats.long.points') })}
                target={hasPlays(value, column) ? target(row, column) : null}
                selected={selected === cell}
                onSelect={() => onSelect?.(cell)}
                className={cx(pad(column), group.group !== null && i === 0 && GROUP_START, column.key === 'points' && 'font-semibold')}
              />
            )
          }),
        )}
      </tr>
    )
  }

  return (
    <Card className="overflow-hidden">
      <Table caption={caption}>
        <THead>
          <tr>
            <H rowSpan={2} className={cx(STICKY, 'border-r bg-sunken pr-3 pl-4 text-left align-bottom')}>
              {t('stats.player')}
            </H>
            {showMatches && (
              <H rowSpan={2} className="px-1 text-right align-bottom">
                {head({ key: 'matches' })}
              </H>
            )}
            {groups.map((group) =>
              group.group === null ? (
                group.columns.map((column) => (
                  <H key={column.key} rowSpan={2} className={cx(pad(column), 'text-right align-bottom')}>
                    {head(column)}
                  </H>
                ))
              ) : (
                <H key={group.group} scope="colgroup" colSpan={group.columns.length} className={cx(GROUP_START, 'px-1.5 pb-1 text-left text-fg sm:text-center')}>
                  {t(`stats.groups.${group.group}` as 'stats.groups.serve')}
                </H>
              ),
            )}
          </tr>
          <tr>
            {groups
              .filter((group) => group.group !== null)
              .map((group) =>
                group.columns.map((column, i) => (
                  <H key={column.key} className={cx(pad(column), 'pt-1 text-right', i === 0 && GROUP_START)}>
                    {head(column)}
                  </H>
                )),
              )}
          </tr>
        </THead>
        <TBody>{players.map((row) => line(row, 'player'))}</TBody>
        <tbody className="border-t-2 border-hairline-strong *:hover:tint-hover">
          {line(team, 'team')}
          {line(opponent, 'opponent')}
        </tbody>
      </Table>
    </Card>
  )
}

const TEAM_KEYS = [
  'won',
  'lost',
  'side_out_of',
  'side_out_won',
  'side_out_pct',
  'break_point_of',
  'break_point_won',
  'break_point_pct',
] as const
export type TeamKey = (typeof TEAM_KEYS)[number]

export interface TeamTableRow {
  key: string
  label: ReactNode
  /** Text for the play button's label ("Set 2"). */
  name: string
  score: string | null
  line: TeamLine
  total?: boolean
}

/** Points won and lost, side-out and break-point: per set and the match, or per match and the season. */
export function TeamTable({
  caption,
  first,
  scoreLabel,
  rows,
  target,
  selected = null,
  onSelect,
}: {
  caption: string
  first: string
  scoreLabel: string
  rows: readonly TeamTableRow[]
  target: (row: TeamTableRow, key: TeamKey) => CellTarget | null
  selected?: string | null
  onSelect?: (key: string) => void
}) {
  const { t } = useTranslation()
  const body = rows.filter((r) => !r.total)
  const totals = rows.filter((r) => r.total)
  const line = (row: TeamTableRow) => (
    <tr key={row.key} className={cx(row.total && 'bg-sunken font-semibold')}>
      <th
        scope="row"
        className={cx(STICKY, 'max-w-44 border-r border-hairline px-3 py-2.5 text-left font-normal whitespace-nowrap first:pl-4 sm:max-w-80', row.total ? 'bg-sunken' : 'bg-surface', 'in-[tr:hover]:tint-hover')}
      >
        {row.label}
      </th>
      <td className="px-3 py-2.5 text-right font-semibold whitespace-nowrap tabular-nums">{row.score ?? EMPTY}</td>
      {TEAM_KEYS.map((key, i) => {
        const value = row.line[key]
        const pct = key.endsWith('_pct')
        const text = formatStat(value, pct ? 'pct' : 'count')
        const cell = `${row.key}|${key}`
        return (
          <NumberCell
            key={key}
            value={value}
            text={text}
            label={t('stats.playLabel', { row: row.name, column: t(`stats.team.long.${key}`) })}
            target={value !== null && value !== 0 ? target(row, key) : null}
            selected={selected === cell}
            onSelect={() => onSelect?.(cell)}
            className={cx('px-3', (i === 0 || i === 2 || i === 5) && GROUP_START, pct && 'font-semibold')}
          />
        )
      })}
    </tr>
  )
  return (
    <Card className="w-fit max-w-full overflow-hidden">
      <Table caption={caption}>
        <THead>
          <tr>
            <H className={cx(STICKY, 'border-r bg-sunken pr-3 pl-4 text-left')}>{first}</H>
            <H className="px-3 text-right">{scoreLabel}</H>
            {TEAM_KEYS.map((key, i) => (
              <H key={key} className={cx('px-3 text-right', (i === 0 || i === 2 || i === 5) && GROUP_START)}>
                <abbr title={t(`stats.team.long.${key}`)} className="no-underline">
                  {t(`stats.team.columns.${key}`)}
                </abbr>
              </H>
            ))}
          </tr>
        </THead>
        <TBody>{body.map(line)}</TBody>
        {totals.length > 0 && <tbody className="border-t-2 border-hairline-strong *:hover:tint-hover">{totals.map(line)}</tbody>}
      </Table>
    </Card>
  )
}

/** The abbreviations spelled out, the rating scales and how empty numbers read. */
export function StatsLegend({ columns, matches = false, team = true }: { columns: readonly StatColumn[]; matches?: boolean; team?: boolean }) {
  const { t } = useTranslation()
  const groups = columnGroups(columns)
  const entry = (abbr: string, long: string, key: string) => (
    <div key={key} className="flex gap-2 py-0.5">
      <dt className="w-14 shrink-0 font-semibold whitespace-nowrap tabular-nums">{abbr}</dt>
      <dd className="text-muted">{long}</dd>
    </div>
  )
  return (
    <details className="group rounded-card border border-hairline bg-surface px-4 text-sm shadow-card">
      <summary className="flex min-h-11 cursor-pointer items-center font-semibold select-none">{t('stats.legend.title')}</summary>
      <div className="flex flex-col gap-4 pb-4">
        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
          {matches && <dl>{entry(t('stats.columns.matches'), t('stats.long.matches'), 'matches')}</dl>}
          {groups.map((group) => (
            <section key={group.group ?? group.columns[0]?.key}>
              {group.group && <h3 className="mb-1 font-semibold">{t(`stats.groups.${group.group}` as 'stats.groups.serve')}</h3>}
              <dl>
                {group.columns.map((c) =>
                  entry(t(`stats.columns.${c.key}` as 'stats.columns.points'), t(`stats.long.${c.key}` as 'stats.long.points'), c.key),
                )}
              </dl>
            </section>
          ))}
          {team && (
            <section>
              <h3 className="mb-1 font-semibold">{t('stats.team.title')}</h3>
              <dl>{TEAM_KEYS.map((key) => entry(t(`stats.team.columns.${key}`), t(`stats.team.long.${key}`), key))}</dl>
            </section>
          )}
        </div>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-muted">
          <li>{t('stats.legend.ratings')}</li>
          <li>{t('stats.legend.ungraded')}</li>
          <li>{t('stats.legend.dash')}</li>
        </ul>
      </div>
    </details>
  )
}

import '../i18n/pages'
import { useId, useMemo, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { cx } from '../lib/cx'
import { useSideNames } from '../lib/sides'
import { EMPTY, formatStat, rowKey, rowLabel, type ZoneCell, type ZoneEnd, type ZoneRow, type ZoneSkill, type ZoneStats as Zones } from '../lib/stats'
import { Card } from './Card'
import { Select } from './Field'
import { ChartIcon } from './icons'
import { EmptyState, LoadError } from './Message'
import { Segmented } from './Segmented'
import { Spinner } from './Spinner'
import { NumberCell, type CellTarget, H } from './StatsTable'
import { Table, TBody, THead } from './Table'

/** What a zone's number does: play its tags (video page) or open them as a reel (team page). */
export type ZoneTarget = (row: ZoneRow, skill: ZoneSkill, end: ZoneEnd, cell: ZoneCell, code: string) => CellTarget | null

interface ZoneStatsProps {
  zones: Zones | undefined
  pending: boolean
  error: boolean
  onRetry: () => void
  /** The set shown (the video page's scope buttons); null: the match or the season. */
  setNo: number | null
  target: ZoneTarget
  selected?: string | null
  onSelect?: (key: string) => void
}

/**
 * Where the zones are on the drawing, row by row from the top. "from": the acting team's own
 * half with the net at the top (front row 4 3 2); "to": the other half seen from the acting
 * team, the net at the bottom (their front row 2 3 4 near it, their 1 far left).
 */
const LAYOUT: Record<ZoneEnd, readonly (readonly number[])[]> = {
  from: [
    [4, 3, 2],
    [7, 8, 9],
    [5, 6, 1],
  ],
  to: [
    [1, 6, 5],
    [9, 8, 7],
    [2, 3, 4],
  ],
}

/** Where a skill's zones are read first: serves where they went, attacks where they started. */
const DEFAULT_END: Record<ZoneSkill, ZoneEnd> = { serve: 'to', attack: 'from' }

type Colour = 'volume' | 'eff'

/** Whole percent on the court, where a zone is small: "+13%", "−8%", "33%". */
function shortPct(value: number | null, signed = false): string {
  if (value === null) return EMPTY
  const n = Math.round(value * 100)
  if (!signed || n === 0) return `${Math.abs(n)}%`
  return n > 0 ? `+${n}%` : `−${Math.abs(n)}%`
}

/** Signed percent for efficiencies: "+12.5%", "−8.0%". */
function signedPct(value: number | null): string {
  if (value === null) return EMPTY
  const text = formatStat(Math.abs(value), 'pct')
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text
}

/**
 * Zone maps (PLAN §9.7): a team's or a player's serves and attacks per zone 1–9, where they went
 * or where they started, as a heat map on the court (darker: more of them, or by efficiency),
 * with the numbers in a table below. Every zone plays its tags.
 */
export function ZoneStats({ zones, pending, error, onRetry, setNo, target, selected = null, onSelect }: ZoneStatsProps) {
  const { t } = useTranslation()
  const id = useId()
  const names = useSideNames()
  const [skill, setSkill] = useState<ZoneSkill>('serve')
  const [end, setEnd] = useState<ZoneEnd>(DEFAULT_END.serve)
  const [who, setWho] = useState('team:')
  const [colour, setColour] = useState<Colour>('volume')

  const scope = zones?.scopes.find((s) => s.set_no === setNo) ?? null
  const rows = useMemo(() => scope?.rows ?? [], [scope])
  const row = rows.find((r) => rowKey(r) === who) ?? rows.find((r) => r.kind === 'team') ?? rows[0] ?? null
  const code = zones?.codes[skill] ?? null

  if (pending) return <Spinner />
  if (error || !zones) return <LoadError onRetry={onRetry} />
  if (rows.length === 0 || !row || !code) {
    return (
      <EmptyState icon={<ChartIcon />} title={t('stats.zones.emptyTitle')}>
        {t('stats.zones.emptyBody')}
      </EmptyState>
    )
  }

  const cells = row.zones[`${skill}_${end}`] ?? []
  const known = cells.filter((c) => c.zone !== null)
  const unknown = cells.find((c) => c.zone === null) ?? null
  const label = rowLabel(t, row, names)
  const skillName = t(skill === 'serve' ? 'stats.zones.serves' : 'stats.zones.attacks')
  const cellKey = (cell: ZoneCell) => `zones|${skill}|${end}|${rowKey(row)}|${cell.zone ?? 'none'}`
  const describe = (cell: ZoneCell) =>
    t(skill === 'serve' ? 'stats.zones.serveLabel' : 'stats.zones.attackLabel', {
      zone: cell.zone,
      count: cell.ta,
      points: cell.points,
      errors: cell.errors,
      blocked: cell.blocked,
      eff: signedPct(cell.eff),
    })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <Segmented
          label={t('stats.zones.skill')}
          value={skill}
          options={[
            { value: 'serve', label: t('stats.zones.serves') },
            { value: 'attack', label: t('stats.zones.attacks') },
          ]}
          onChange={(next) => {
            setSkill(next)
            setEnd(DEFAULT_END[next])
          }}
        />
        <Segmented
          label={t('stats.zones.end')}
          value={end}
          options={[
            { value: 'to', label: t('stats.zones.to') },
            { value: 'from', label: t('stats.zones.from') },
          ]}
          onChange={setEnd}
        />
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-who`}>
          {t('stats.zones.who')}
          <Select id={`${id}-who`} inline value={rowKey(row)} onChange={(e) => setWho(e.target.value)}>
            {rows.map((r) => (
              <option key={rowKey(r)} value={rowKey(r)}>
                {rowLabel(t, r, names)}
              </option>
            ))}
          </Select>
        </label>
        <Segmented
          label={t('stats.zones.colour')}
          value={colour}
          options={[
            { value: 'volume', label: t('stats.zones.volume') },
            { value: 'eff', label: t('stats.zones.efficiency') },
          ]}
          onChange={setColour}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:items-start">
        <figure className="flex flex-col gap-2">
          <figcaption className="text-sm font-semibold">
            {t(end === 'to' ? 'stats.zones.courtTo' : 'stats.zones.courtFrom', { skill: skillName, who: label })}
          </figcaption>
          <div className="rounded-card border border-hairline bg-surface p-3 shadow-card">
            <ZoneCourt
              end={end}
              cells={known}
              colour={colour}
              label={t('stats.zones.courtLabel', { skill: skillName, who: label })}
              netLabel={t('court.tendencies.net')}
              describe={describe}
              target={(cell) => target(row, skill, end, cell, code)}
              selected={selected}
              cellKey={cellKey}
              onSelect={onSelect}
            />
          </div>
          <p className="text-xs text-muted">{colour === 'volume' ? t(`stats.zones.volumeLegend.${skill}`) : t('stats.zones.effLegend')}</p>
        </figure>
        <div className="flex min-w-0 flex-col gap-2">
          {known.length === 0 ? (
            <p className="text-sm text-muted">{t(`stats.zones.noZones.${skill}`)}</p>
          ) : (
            <ZoneTable skill={skill} cells={known} label={label} target={(cell) => target(row, skill, end, cell, code)} selected={selected} cellKey={cellKey} onSelect={onSelect} />
          )}
          {unknown && (
            <ZoneTable
              skill={skill}
              cells={[unknown]}
              label={label}
              target={(cell) => target(row, skill, end, cell, code)}
              selected={selected}
              cellKey={cellKey}
              onSelect={onSelect}
              caption={t('stats.zones.unknownCaption')}
            />
          )}
          <p className="text-xs text-muted">{t(skill === 'serve' ? 'stats.zones.serveNote' : 'stats.zones.attackNote')}</p>
        </div>
      </div>
    </div>
  )
}

interface CourtProps {
  end: ZoneEnd
  cells: readonly ZoneCell[]
  colour: Colour
  label: string
  netLabel: string
  describe: (cell: ZoneCell) => string
  target: (cell: ZoneCell) => CellTarget | null
  selected: string | null
  cellKey: (cell: ZoneCell) => string
  onSelect?: (key: string) => void
}

/** The half court with its nine zones tinted: by how many (volume) or how well (efficiency). */
function ZoneCourt({ end, cells, colour, label, netLabel, describe, target, selected, cellKey, onSelect }: CourtProps) {
  const { t } = useTranslation()
  const byZone = new Map(cells.map((c) => [c.zone, c]))
  const total = cells.reduce((sum, c) => sum + c.ta, 0)
  const most = Math.max(1, ...cells.map((c) => c.ta))
  const netAtTop = end === 'from'
  const netY = netAtTop ? 0 : 9
  // Room for the net's label above the court or below it.
  const top = netAtTop ? -1.4 : -0.6
  const attackY = netAtTop ? 3 : 6
  const navigate = useNavigate()
  // A zone plays its tags (the video page) or opens them in the reel builder (the team page).
  const fire = (cell: ZoneCell) => {
    const found = target(cell)
    if (!found) return
    onSelect?.(cellKey(cell))
    if (found.kind === 'play') found.onPlay()
    else void navigate(found.to)
  }
  return (
    <svg viewBox={`-0.6 ${top} 10.2 11`} role="group" aria-label={label} className="h-auto w-full select-none" data-testid="zone-court">
      <rect x={-0.6} y={top} width={10.2} height={11} rx={0.4} fill="var(--color-sunken)" />
      <rect x={0} y={0} width={9} height={9} fill="var(--color-court-floor)" />
      {LAYOUT[end].map((line, row) =>
        line.map((zone, col) => {
          const cell = byZone.get(zone) ?? null
          const x = col * 3
          const y = row * 3
          const count = cell?.ta ?? 0
          let fill = 'transparent'
          let opacity = 0
          if (cell && count > 0) {
            if (colour === 'volume') {
              fill = 'var(--color-outcome-win)'
              opacity = 0.12 + 0.55 * (count / most)
            } else {
              const eff = cell.eff ?? 0
              fill = eff < 0 ? 'var(--color-outcome-error)' : 'var(--color-outcome-win)'
              opacity = 0.1 + 0.6 * Math.min(1, Math.abs(eff) / 0.5)
            }
          }
          const found = cell && count > 0 ? target(cell) : null
          const playable = found !== null
          const on = cell !== null && selected === cellKey(cell)
          const main = cell && count > 0 ? (colour === 'volume' ? String(count) : shortPct(cell.eff, true)) : ''
          const sub = cell && count > 0 ? (colour === 'volume' ? (total > 0 ? shortPct(count / total) : '') : t('stats.zones.attempts', { count })) : ''
          const onKey = (event: KeyboardEvent) => {
            if (!cell || (event.key !== 'Enter' && event.key !== ' ')) return
            event.preventDefault()
            fire(cell)
          }
          return (
            <g
              key={zone}
              data-zone={zone}
              {...(playable && cell
                ? {
                    role: found.kind === 'play' ? 'button' : 'link',
                    tabIndex: 0,
                    'aria-label': t(found.kind === 'play' ? 'stats.play' : 'stats.open', { what: describe(cell), value: count }),
                    'aria-pressed': found.kind === 'play' ? on : undefined,
                    onClick: () => fire(cell),
                    onKeyDown: onKey,
                  }
                : { role: 'img', 'aria-label': cell && count > 0 ? describe(cell) : t('stats.zones.emptyZone', { zone }) })}
              className={cx(playable && 'cursor-pointer outline-none [&:focus-visible_.ring]:opacity-100 [&:hover_.ring]:opacity-60')}
            >
              <rect x={x} y={y} width={3} height={3} fill={fill} fillOpacity={opacity} />
              <rect className="ring opacity-0" x={x + 0.08} y={y + 0.08} width={2.84} height={2.84} fill="none" stroke="var(--color-ring)" strokeWidth={0.12} style={on ? { opacity: 1 } : undefined} />
              <text x={x + 0.22} y={y + 0.55} fontSize={0.42} fontWeight={700} fill="var(--color-muted)" fontFamily="ui-monospace, monospace">
                {zone}
              </text>
              {main && (
                <text x={x + 1.5} y={y + 1.72} fontSize={colour === 'volume' ? 0.9 : 0.72} fontWeight={800} textAnchor="middle" fill="var(--color-fg)" className="tabular-nums">
                  {main}
                </text>
              )}
              {sub && (
                <text x={x + 1.5} y={y + 2.42} fontSize={0.4} fontWeight={600} textAnchor="middle" fill="var(--color-fg)" opacity={0.75} className="tabular-nums">
                  {sub}
                </text>
              )}
            </g>
          )
        }),
      )}
      {/* The zones' lines, the 3 m line a little stronger, the court's outline. */}
      {[3, 6].map((v) => (
        <line key={`v${v}`} x1={v} y1={0} x2={v} y2={9} stroke="var(--color-court-line)" strokeWidth={0.03} />
      ))}
      {[3, 6].map((v) => (
        <line key={`h${v}`} x1={0} y1={v} x2={9} y2={v} stroke="var(--color-court-line)" strokeWidth={v === attackY ? 0.08 : 0.03} />
      ))}
      <rect x={0} y={0} width={9} height={9} fill="none" stroke="var(--color-court-line)" strokeWidth={0.08} />
      <line x1={-0.45} y1={netY} x2={9.45} y2={netY} stroke="var(--color-fg)" strokeWidth={0.16} strokeLinecap="round" />
      <text x={4.5} y={netAtTop ? -0.55 : 9.95} fontSize={0.45} textAnchor="middle" fill="var(--color-muted)">
        {netLabel}
      </text>
    </svg>
  )
}

interface TableProps {
  skill: ZoneSkill
  cells: readonly ZoneCell[]
  label: string
  target: (cell: ZoneCell) => CellTarget | null
  selected: string | null
  cellKey: (cell: ZoneCell) => string
  onSelect?: (key: string) => void
  caption?: string
}

/** The zones' numbers: attempts, points, errors (blocked), efficiency; the counts play their tags. */
function ZoneTable({ skill, cells, label, target, selected, cellKey, onSelect, caption }: TableProps) {
  const { t } = useTranslation()
  const attack = skill === 'attack'
  const columns = [
    ['ta', t('stats.zones.columns.ta'), t('stats.zones.long.ta')],
    ['points', t(attack ? 'stats.zones.columns.kills' : 'stats.zones.columns.aces'), t(attack ? 'stats.zones.long.kills' : 'stats.zones.long.aces')],
    ['errors', t('stats.zones.columns.errors'), t('stats.zones.long.errors')],
    ...(attack ? [['blocked', t('stats.zones.columns.blocked'), t('stats.zones.long.blocked')]] : []),
    ['point_pct', t(attack ? 'stats.zones.columns.killPct' : 'stats.zones.columns.acePct'), t(attack ? 'stats.zones.long.killPct' : 'stats.zones.long.acePct')],
    ['eff', t(attack ? 'stats.zones.columns.hitPct' : 'stats.zones.columns.eff'), t(attack ? 'stats.zones.long.hitPct' : 'stats.zones.long.eff')],
  ] as [keyof ZoneCell, string, string][]
  return (
    <Card className="w-fit max-w-full overflow-hidden">
      <Table caption={caption ?? t('stats.zones.tableCaption', { who: label })}>
        <THead>
          <tr>
            <H className="pr-2 pl-4 text-left">{t('stats.zones.zone')}</H>
            {columns.map(([key, short, long]) => (
              <H key={key} className="px-1.5 text-right sm:px-2">
                <abbr title={long} className="no-underline">
                  {short}
                </abbr>
              </H>
            ))}
          </tr>
        </THead>
        <TBody>
          {cells.map((cell) => {
            const key = cellKey(cell)
            const playable = target(cell)
            return (
              <tr key={key} className={cx(selected === key && 'bg-accent-soft')}>
                <th scope="row" className="py-2.5 pr-2 pl-4 text-left font-semibold whitespace-nowrap">
                  {cell.zone === null ? t('stats.zones.noZone') : t('stats.zones.zoneNo', { zone: cell.zone })}
                </th>
                {columns.map(([column, , long]) => {
                  const value = cell[column] as number | null
                  const pct = column === 'point_pct' || column === 'eff'
                  const text = column === 'eff' ? signedPct(value) : formatStat(value, pct ? 'pct' : 'count')
                  const plays = column === 'ta' && value !== null && value > 0
                  return (
                    <NumberCell
                      key={column}
                      value={value}
                      text={text}
                      label={t('stats.playLabel', { row: cell.zone === null ? t('stats.zones.noZone') : t('stats.zones.zoneNo', { zone: cell.zone }), column: long })}
                      target={plays ? playable : null}
                      selected={selected === key && plays}
                      onSelect={() => onSelect?.(key)}
                      className={cx('px-1.5 sm:px-2', pct && 'font-semibold')}
                    />
                  )
                })}
              </tr>
            )
          })}
        </TBody>
      </Table>
    </Card>
  )
}

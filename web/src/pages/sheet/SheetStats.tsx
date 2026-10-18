import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '../../components/Card'
import { SectionHeader } from '../../components/PageHeader'
import { Segmented } from '../../components/Segmented'
import { Table, TBody, Td, Th, THead } from '../../components/Table'
import { sideLabel } from '../../lib/labels'
import type { LineOut, SetCheckOut, SetStatsOut, SideStatsOut } from '../../lib/scoresheet'
import type { SideNames } from '../../lib/sides'
import { formatPercent } from '../../lib/suggestions'
import type { Side } from '../../lib/types'

/** A column's name: short on a phone ("BP"), in full from sm up. */
function Head({ full, short }: { full: string; short: string }) {
  return (
    <>
      <abbr title={full} className="no-underline sm:hidden">
        {short}
      </abbr>
      <span className="hidden sm:inline">{full}</span>
    </>
  )
}

/** "57% (27/47)"; on a phone the share alone (the counts in its tooltip). */
function Pct({ won, of, value }: { won: number; of: number; value: number | null }) {
  if (!of) return <>–</>
  return (
    <span title={`${won}/${of}`}>
      {formatPercent(value)}
      <span className="hidden text-muted sm:inline">{` (${won}/${of})`}</span>
    </span>
  )
}

/**
 * The sheet's own numbers (docs/PLAN.md §10.3a): points, side-out and break point per team, per
 * rotation for both teams, longest runs; the match (the sets that fit) or one set. No tag needed.
 */
export function SheetStats({ match, checks, bySetter, names }: { match: SetStatsOut | null; checks: SetCheckOut[]; bySetter: boolean; names: SideNames | null }) {
  const { t } = useTranslation()
  const withStats = checks.filter((c) => c.stats)
  const [scope, setScope] = useState<string>('match')
  const chosen = scope === 'match' ? match : (checks.find((c) => String(c.no) === scope)?.stats ?? null)
  return (
    <Card className="flex flex-col gap-4 p-4" role="region" aria-labelledby="sheet-stats-title">
      <SectionHeader id="sheet-stats-title" title={t('sheet.stats.title')} hint={t('sheet.stats.hint')} />
      {withStats.length > 0 && (
        <Segmented
          label={t('sheet.stats.scope')}
          value={scope}
          options={[
            ...(match ? [{ value: 'match', label: t('sheet.stats.match') }] : []),
            ...withStats.map((c) => ({ value: String(c.no), label: t('sheet.stats.set', { no: c.no }) })),
          ]}
          onChange={setScope}
        />
      )}
      {chosen ? (
        <>
          <Table caption={t('sheet.stats.title')}>
            <THead>
              <tr>
                <Th>{t('sheet.stats.team')}</Th>
                <Th className="text-right">{t('sheet.stats.points')}</Th>
                <Th className="text-right">
                  <Head full={t('sheet.stats.sideOut')} short={t('sheet.stats.short.sideOut')} />
                </Th>
                <Th className="text-right">
                  <Head full={t('sheet.stats.breakPoint')} short={t('sheet.stats.short.breakPoint')} />
                </Th>
                <Th className="hidden text-right sm:table-cell">{t('sheet.stats.longestRun')}</Th>
              </tr>
            </THead>
            <TBody>
              {(['us', 'them'] as const).map((side) => {
                const line = chosen[side].team
                return (
                  <tr key={side}>
                    <Td className="font-semibold whitespace-nowrap">{sideLabel(t, side, names)}</Td>
                    <Td className="text-right tabular-nums">{line.points}</Td>
                    <Td className="text-right tabular-nums"><Pct won={line.side_out_won} of={line.received} value={line.side_out_pct} /></Td>
                    <Td className="text-right tabular-nums"><Pct won={line.break_won} of={line.served} value={line.break_point_pct} /></Td>
                    <Td className="hidden text-right tabular-nums sm:table-cell">{chosen[side].longest_run}</Td>
                  </tr>
                )
              })}
            </TBody>
          </Table>
          <div className="grid gap-4 lg:grid-cols-2">
            {(['us', 'them'] as const).map((side) => (
              <Rotations key={side} side={side} stats={chosen[side]} names={names} />
            ))}
          </div>
          <p className="text-xs text-muted">{scope === 'match' && bySetter ? t('sheet.stats.bySetter') : t('sheet.stats.byStart')}</p>
        </>
      ) : (
        <p className="text-sm text-muted">{t('sheet.stats.none')}</p>
      )}
    </Card>
  )
}

function Rotations({ side, stats, names }: { side: Side; stats: SideStatsOut; names: SideNames | null }) {
  const { t } = useTranslation()
  const rows = stats.rotations.filter((r) => r.line.points + r.line.lost > 0)
  const servers = rows.some((r) => r.server !== null) // a set's lineup; the match mixes them
  const name = (r: { rotation: number; setter_zone: number | null }) =>
    r.setter_zone ? (
      `P${r.setter_zone}`
    ) : (
      <Head full={t('sheet.stats.rotationLabel', { n: r.rotation })} short={t('sheet.stats.short.rotation', { n: r.rotation })} />
    )
  return (
    <Table caption={`${sideLabel(t, side, names)}: ${t('sheet.stats.rotations')}`}>
      <THead>
        <tr>
          <Th>
            <span className="sm:hidden">{sideLabel(t, side, names)}</span>
            <span className="hidden sm:inline">{`${sideLabel(t, side, names)} · ${t('sheet.stats.rotation')}`}</span>
          </Th>
          {servers && <Th className="hidden sm:table-cell">{t('sheet.stats.server')}</Th>}
          <Th className="text-right">
            <Head full={t('sheet.stats.wonLost')} short={t('sheet.stats.short.wonLost')} />
          </Th>
          <Th className="text-right">
            <Head full={t('sheet.stats.sideOut')} short={t('sheet.stats.short.sideOut')} />
          </Th>
          <Th className="text-right">
            <Head full={t('sheet.stats.breakPoint')} short={t('sheet.stats.short.breakPoint')} />
          </Th>
        </tr>
      </THead>
      <TBody>
        {rows.map((r) => {
          const line: LineOut = r.line
          return (
            <tr key={r.rotation}>
              <Td className="font-semibold whitespace-nowrap" title={r.setter_zone ? t('sheet.stats.setterZone', { zone: `P${r.setter_zone}` }) : undefined}>
                {name(r)}
              </Td>
              {servers && <Td className="hidden tabular-nums sm:table-cell">{r.server !== null ? `#${r.server}` : '–'}</Td>}
              <Td className="text-right tabular-nums">{`${line.points}–${line.lost}`}</Td>
              <Td className="text-right tabular-nums"><Pct won={line.side_out_won} of={line.received} value={line.side_out_pct} /></Td>
              <Td className="text-right tabular-nums"><Pct won={line.break_won} of={line.served} value={line.break_point_pct} /></Td>
            </tr>
          )
        })}
      </TBody>
    </Table>
  )
}

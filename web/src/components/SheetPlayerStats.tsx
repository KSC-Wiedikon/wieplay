import { useTranslation } from 'react-i18next'
import { formatPercent } from '../lib/suggestions'
import { formatPlusMinus, type SheetPlayer, type SheetPlayers } from '../lib/stats'
import { useSideNames } from '../lib/sides'
import type { Side } from '../lib/types'
import { Table, TBody, Td, Th, THead, Tr } from './Table'

/**
 * Per player from the official scoresheet (docs/PLAN.md §10.3a): serves, points won on them, the
 * longest run, aces and errors (where a serve tag graded it), points on court and the
 * plus/minus, also per rotation. Whole match; both teams, by shirt number.
 */
export function SheetPlayerStats({ data }: { data: SheetPlayers }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const sides: Side[] = ['us', 'them']
  const rotations = [1, 2, 3, 4, 5, 6]
  const rotationLabel = (r: number, side: Side) => (side === 'us' && data.by_setter ? `P${r}` : `R${r}`)
  return (
    <div className="flex flex-col gap-2" data-testid="sheet-players">
      <h3 className="font-semibold">{t('stats.sheetPlayers.title')}</h3>
      <p className="text-sm text-muted">{t('stats.sheetPlayers.hint')}</p>
      {sides.map((side) => {
        const rows = data.players.filter((p) => p.side === side && (p.served > 0 || p.on_court > 0))
        if (rows.length === 0) return null
        const team = names ? names[side] : t(side === 'us' ? 'sheetCourt.us' : 'sheetCourt.them')
        return (
          <Table key={side} caption={`${t('stats.sheetPlayers.title')} · ${team}`}>
            <THead>
              <tr>
                <Th>{team}</Th>
                <Th className="text-right">{t('stats.sheetPlayers.served')}</Th>
                <Th className="text-right">{t('stats.sheetPlayers.won')}</Th>
                <Th className="hidden text-right sm:table-cell">{t('stats.sheetPlayers.run')}</Th>
                <Th className="text-right">{t('stats.sheetPlayers.aces')}</Th>
                <Th className="text-right">{t('stats.sheetPlayers.errors')}</Th>
                <Th className="hidden text-right sm:table-cell">{t('stats.sheetPlayers.onCourt')}</Th>
                <Th className="text-right">{t('stats.sheetPlayers.plusMinus')}</Th>
                {rotations.map((r) => (
                  <Th key={r} className="hidden text-right md:table-cell">
                    {rotationLabel(r, side)}
                  </Th>
                ))}
              </tr>
            </THead>
            <TBody>
              {rows.map((p) => (
                <Row key={`${p.side}-${p.number}`} player={p} />
              ))}
            </TBody>
          </Table>
        )
      })}
      <p className="text-xs text-muted">{t('stats.sheetPlayers.legend', { sets: data.sets.join(', ') })}</p>
    </div>
  )
}

function Row({ player: p }: { player: SheetPlayer }) {
  const { t } = useTranslation()
  const won = p.won_on_serve_pct === null ? '–' : `${p.won_on_serve} (${formatPercent(p.won_on_serve_pct)})`
  const tagged = (value: number) => (p.tagged > 0 ? String(value) : '–')
  return (
    <Tr>
      <Td className="font-semibold tabular-nums">
        #{p.number}
        {p.name && <span className="ml-2 font-normal">{p.name}</span>}
      </Td>
      <Td className="text-right tabular-nums">{p.served}</Td>
      <Td className="text-right tabular-nums">{won}</Td>
      <Td className="hidden text-right tabular-nums sm:table-cell">{p.longest_run}</Td>
      <Td className="text-right tabular-nums" title={t('stats.sheetPlayers.taggedHint', { count: p.tagged })}>
        {tagged(p.aces)}
      </Td>
      <Td className="text-right tabular-nums" title={t('stats.sheetPlayers.taggedHint', { count: p.tagged })}>
        {tagged(p.errors)}
      </Td>
      <Td className="hidden text-right tabular-nums sm:table-cell">{p.on_court}</Td>
      <Td className="text-right font-semibold tabular-nums">{formatPlusMinus(p.plus_minus)}</Td>
      {p.by_rotation.map((v, i) => (
        <Td key={i} className="hidden text-right tabular-nums md:table-cell">
          {p.on_court > 0 ? formatPlusMinus(v) : '–'}
        </Td>
      ))}
    </Tr>
  )
}

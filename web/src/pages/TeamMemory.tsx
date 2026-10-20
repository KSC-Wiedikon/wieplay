import '../i18n/pages'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge, Chip } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Select } from '../components/Field'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { NotFoundError } from '../lib/api'
import { formatPercent } from '../lib/suggestions'
import {
  romanZone,
  shownPlayers,
  startZones,
  useFixNumber,
  useTeamMemory,
  type MemoryPlayer,
  type ModelScore,
  type TeamMemory,
} from '../lib/teamMemory'
import type { TeamDetail } from '../lib/types'

/**
 * "What WiePlay knows about this team" (docs/PLAN.md §10.5b): our players with their numbers and
 * how many games named each, the usual service orders and where the setter starts, the models'
 * scores for the team. Coaches (and helpers) fix a wrong number ↔ player link. Loaded on demand
 * by the team page, volleyball only.
 */
export function TeamMemorySection({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const id = useId()
  const memory = useTeamMemory(team.id)
  if (memory.isError && memory.error instanceof NotFoundError) return null
  if (memory.data && !memory.data.available) return null
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader id={id} title={t('memory.title')} hint={t('memory.hint')} />
      {memory.isPending ? (
        <Spinner />
      ) : memory.isError ? (
        <LoadError onRetry={() => void memory.refetch()} />
      ) : (
        <MemoryBody teamId={team.id} memory={memory.data} />
      )}
    </section>
  )
}

function MemoryBody({ teamId, memory }: { teamId: number; memory: TeamMemory }) {
  const { t } = useTranslation()
  const [fixing, setFixing] = useState<MemoryPlayer | null>(null)
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{t('memory.learntFrom', { count: memory.games })}</p>
      <PlayersCard memory={memory} onFix={setFixing} />
      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
        <RotationsCard memory={memory} />
        <ModelsCard memory={memory} />
      </div>
      {fixing && <FixDialog teamId={teamId} memory={memory} player={fixing} onClose={() => setFixing(null)} />}
    </div>
  )
}

function PlayersCard({ memory, onFix }: { memory: TeamMemory; onFix: (p: MemoryPlayer) => void }) {
  const { t } = useTranslation()
  const players = shownPlayers(memory.players)
  if (players.length === 0) return <Message>{t('memory.noPlayers')}</Message>
  return (
    <Card className="overflow-hidden">
      <Table caption={t('memory.players')}>
        <THead>
          <tr>
            <Th>{t('memory.number')}</Th>
            <Th>{t('memory.player')}</Th>
            <Th className="text-right">{t('memory.games')}</Th>
            <Th className="hidden text-right sm:table-cell">{t('memory.sets')}</Th>
            {memory.can_fix && (
              <Th>
                <span className="sr-only">{t('memory.change')}</span>
              </Th>
            )}
          </tr>
        </THead>
        <TBody>
          {players.map((p) => (
            <tr key={p.number} className={p.hidden ? 'text-muted' : undefined}>
              <Td className="font-bold tabular-nums">#{p.number}</Td>
              <Td>
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="font-semibold">{p.hidden ? t('memory.hiddenBadge') : (p.name ?? t('memory.unknownPlayer'))}</span>
                  {p.libero && <Badge>{t('memory.libero')}</Badge>}
                  {p.fixed && <Badge tone="accent">{t('memory.fixedBadge')}</Badge>}
                  {p.source === 'learned' && !p.fixed && <Badge>{t('memory.learnedBadge')}</Badge>}
                  {p.ambiguous && <Badge tone="danger">{t('memory.unclear', { number: p.number })}</Badge>}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{p.games}</Td>
              <Td className="hidden text-right tabular-nums sm:table-cell">{p.sets}</Td>
              {memory.can_fix && (
                <Td tight className="text-right">
                  <Button variant="ghost" className="px-3" aria-label={t('memory.fix', { number: p.number })} onClick={() => onFix(p)}>
                    {t('memory.change')}
                  </Button>
                </Td>
              )}
            </tr>
          ))}
        </TBody>
      </Table>
    </Card>
  )
}

function RotationsCard({ memory }: { memory: TeamMemory }) {
  const { t } = useTranslation()
  const rot = memory.rotations
  const id = useId()
  return (
    <Card className="flex flex-col gap-3 p-4" role="region" aria-labelledby={id}>
      <h3 id={id} className="font-semibold">
        {t('memory.rotations')}
      </h3>
      {!rot || rot.orders.length === 0 ? (
        <p className="text-sm text-muted">{t('memory.noRotations')}</p>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <p className="text-sm font-semibold">{t('memory.orders')}</p>
            <ul className="flex flex-col gap-1.5">
              {rot.orders.slice(0, 4).map((o) => (
                <li key={o.order.join('-')} className="flex flex-wrap items-center gap-1.5">
                  {o.order.map((n, k) => (
                    <Chip key={`${n}-${k}`} accent={k === 0}>
                      #{n}
                    </Chip>
                  ))}
                  <span className="text-xs text-muted">{t('memory.orderShare', { value: formatPercent(o.share) })}</span>
                </li>
              ))}
            </ul>
          </div>
          {Object.keys(rot.start_zones).length > 0 && (
            <div className="flex flex-col gap-1.5">
              <p className="text-sm font-semibold">{t('memory.startZones')}</p>
              <p className="flex flex-wrap gap-1.5">
                {startZones(rot.start_zones).map((z) => (
                  <Chip key={z.zone}>
                    {t('memory.zone', { zone: romanZone(z.zone) })} · {formatPercent(z.share)}
                  </Chip>
                ))}
              </p>
            </div>
          )}
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            {rot.setters.length > 0 && (
              <>
                <dt className="font-semibold">{t('memory.setters')}</dt>
                <dd>{rot.setters.map((n) => `#${n}`).join(', ')}</dd>
              </>
            )}
            {rot.liberos.length > 0 && (
              <>
                <dt className="font-semibold">{t('memory.liberos')}</dt>
                <dd>{rot.liberos.map((n) => `#${n}`).join(', ')}</dd>
              </>
            )}
          </dl>
          {(rot.check.serves ?? 0) > 0 && (
            <p className="text-sm text-muted">{t('memory.check', { right: rot.check.right ?? 0, serves: rot.check.serves ?? 0 })}</p>
          )}
        </>
      )}
    </Card>
  )
}

function Score({ label, score }: { label: string; score: ModelScore | undefined }) {
  const { t } = useTranslation()
  const f = (v: number | null) => formatPercent(v)
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="font-semibold">{label}</dt>
      {!score || score.games === 0 ? (
        <dd className="text-muted">{t('memory.notReviewed')}</dd>
      ) : (
        <dd className="flex flex-col gap-0.5">
          <span className="tabular-nums">
            {score.v1 !== null ? t('memory.scores', { v0: f(score.v0), v1: f(score.v1) }) : t('memory.scoresRules', { v0: f(score.v0) })}
          </span>
          <span className="text-xs text-muted">{t('memory.reviewed', { count: score.games })}</span>
          {score.team && score.team.games >= 2 && (
            <span className="text-xs text-muted">
              {t('memory.own', {
                variant: t(`memory.variant.${(['weighted', 'calibrated'].includes(score.team.variant) ? score.team.variant : 'pooled') as 'weighted' | 'calibrated' | 'pooled'}`),
              })}
            </span>
          )}
        </dd>
      )}
    </div>
  )
}

function ModelsCard({ memory }: { memory: TeamMemory }) {
  const { t } = useTranslation()
  const id = useId()
  const players = memory.models.players
  return (
    <Card className="flex flex-col gap-3 p-4" role="region" aria-labelledby={id}>
      <h3 id={id} className="font-semibold">
        {t('memory.models')}
      </h3>
      <dl className="flex flex-col gap-3 text-sm">
        <Score label={t('memory.rallies')} score={memory.models.rallies} />
        <Score label={t('memory.actions')} score={memory.models.actions} />
        <div className="flex flex-col gap-0.5">
          <dt className="font-semibold">{t('memory.playersModel')}</dt>
          <dd className={players && players.games > 0 ? 'tabular-nums' : 'text-muted'}>
            {players && players.games > 0
              ? t('memory.playersSure', { value: formatPercent(players.sure_share), count: players.games })
              : t('memory.noPlayersRun')}
          </dd>
        </div>
      </dl>
    </Card>
  )
}

const HIDDEN = 'hidden'
const NOBODY = ''

function FixDialog({ teamId, memory, player, onClose }: { teamId: number; memory: TeamMemory; player: MemoryPlayer; onClose: () => void }) {
  const { t } = useTranslation()
  const fix = useFixNumber(teamId)
  const selectId = useId()
  const [value, setValue] = useState(player.hidden ? HIDDEN : player.member_id !== null ? String(player.member_id) : NOBODY)
  const save = () => {
    const body =
      value === HIDDEN
        ? { number: player.number, member_id: null, hidden: true }
        : { number: player.number, member_id: value === NOBODY ? null : Number(value), hidden: false }
    fix.mutate(body, { onSuccess: onClose })
  }
  return (
    <Modal open onClose={onClose} title={t('memory.fix', { number: player.number })}>
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">{t('memory.fixHint', { number: player.number })}</p>
        <label htmlFor={selectId} className="sr-only">
          {t('memory.player')}
        </label>
        <Select id={selectId} value={value} onChange={(e) => setValue(e.target.value)}>
          <option value={NOBODY}>{t('memory.unknownPlayer')}</option>
          {memory.members.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.number !== null ? `${m.name} (#${m.number})` : m.name}
            </option>
          ))}
          <option value={HIDDEN}>{t('memory.notOurs')}</option>
        </Select>
        <div className="flex flex-wrap justify-end gap-2">
          {(player.fixed || player.hidden) && (
            <Button variant="ghost" disabled={fix.isPending} onClick={() => fix.mutate({ number: player.number, reset: true }, { onSuccess: onClose })}>
              {t('memory.reset')}
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" disabled={fix.isPending} onClick={save}>
            {t('common.save')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

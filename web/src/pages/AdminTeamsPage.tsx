import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { AdminShell } from '../components/AdminShell'
import { SportBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Field, Select } from '../components/Field'
import { SyncIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ForbiddenError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { useAdminTeams, useRunSync, useSetTeamEnabled, useSyncStatus } from '../lib/queries'
import { groupBySport } from '../lib/teams'
import type { AdminTeam, SyncStatus } from '../lib/types'
import { GuestClubsSection } from './AdminGuestClubs'

export function AdminTeamsPage() {
  const { t } = useTranslation()
  const teams = useAdminTeams()
  const [season, setSeason] = useSeason(teams.data ?? [])
  return (
    <AdminShell title={t('admin.title')}>
      <div className="flex flex-col gap-6">
        <SeasonPicker teams={teams.data ?? []} season={season} onChange={setSeason} />
        {/* Wide screens: the sync on the left, the teams beside it. */}
        <div className="grid gap-8 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] xl:items-start">
          <SyncPanel />
          <TeamsPanel season={season} />
        </div>
        <GuestClubsSection />
      </div>
    </AdminShell>
  )
}

/** Seasons that have teams, newest first. */
function seasonsOf(teams: AdminTeam[]): string[] {
  return [...new Set(teams.map((team) => team.season))].sort().reverse()
}

/** One season at a time: each season is its own team row (players, games and coaches change),
 * so the page shows the chosen one, in the address (?season=2025/26), newest by default. */
function useSeason(teams: AdminTeam[]): [string | null, (season: string) => void] {
  const [params, setParams] = useSearchParams()
  const seasons = seasonsOf(teams)
  const wanted = params.get('season')
  const season = wanted && seasons.includes(wanted) ? wanted : (seasons[0] ?? null)
  return [season, (next) => setParams(next === seasons[0] ? {} : { season: next }, { replace: true })]
}

function SeasonPicker({
  teams,
  season,
  onChange,
}: {
  teams: AdminTeam[]
  season: string | null
  onChange: (season: string) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const seasons = seasonsOf(teams)
  if (!season) return null
  return (
    <Field label={t('columns.season')} htmlFor={id} className="sm:max-w-48">
      <Select id={id} value={season} onChange={(e) => onChange(e.target.value)}>
        {seasons.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </Select>
    </Field>
  )
}

function SyncPanel() {
  const { t } = useTranslation()
  const status = useSyncStatus()
  const sync = useRunSync()

  return (
    <section aria-labelledby="sync-title" className="flex flex-col gap-3">
      <h2 id="sync-title" className="text-lg font-semibold">
        {t('admin.sync.title')}
      </h2>
      {status.isPending ? (
        <Spinner />
      ) : status.isError ? (
        status.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void status.refetch()} />
        )
      ) : (
        <Card className="flex flex-col gap-5 p-5">
          <SyncDetails status={status.data} />
          <Button variant="primary" className="self-start" disabled={sync.isPending} onClick={() => sync.mutate()}>
            <SyncIcon className={cx('size-5 shrink-0', sync.isPending && 'motion-safe:animate-spin')} />
            {sync.isPending ? t('admin.sync.running') : t('admin.sync.now')}
          </Button>
        </Card>
      )}
    </section>
  )
}

function SyncDetails({ status }: { status: SyncStatus }) {
  const { t } = useTranslation()
  const counts = [
    [t('admin.sync.teams'), status.counts.teams],
    [t('admin.sync.members'), status.counts.members],
    [t('admin.sync.games'), status.counts.games],
  ] as const

  return (
    <>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-muted">{t('admin.sync.lastRun')}</dt>
        <dd className="tabular-nums">{formatDateTime(status.last_run) || t('admin.sync.never')}</dd>
        <dt className="text-muted">{t('admin.sync.lastOk')}</dt>
        <dd className="tabular-nums">{formatDateTime(status.last_ok) || t('admin.sync.never')}</dd>
        {status.error && (
          <>
            <dt className="text-muted">{t('admin.sync.error')}</dt>
            <dd className="wrap-anywhere text-danger">{status.error}</dd>
          </>
        )}
      </dl>
      <dl className="grid grid-cols-3 gap-3">
        {counts.map(([label, value]) => (
          <div key={label} className="flex flex-col-reverse rounded-control border border-hairline bg-sunken px-3 py-2.5">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="text-xl font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </>
  )
}

function TeamsPanel({ season }: { season: string | null }) {
  const { t } = useTranslation()
  const teams = useAdminTeams()

  return (
    <section aria-labelledby="teams-title" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="teams-title" className="text-lg font-semibold">
          {t('admin.teams')}
        </h2>
        <p className="text-sm text-muted">{t('admin.teamsHint')}</p>
      </div>
      {teams.isPending ? (
        <PageSpinner />
      ) : teams.isError ? (
        teams.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void teams.refetch()} />
        )
      ) : teams.data.length === 0 ? (
        <Message>{t('admin.noTeams')}</Message>
      ) : (
        groupBySport(teams.data.filter((team) => team.season === season)).map(({ sport, teams }) => (
          <div key={sport} className="flex flex-col gap-2">
            <h3 className="font-semibold">{t(`sport.${sport}`)}</h3>
            <Card className="overflow-hidden">
              <AdminTeamTable teams={teams} caption={t(`sport.${sport}`)} />
            </Card>
          </div>
        ))
      )}
    </section>
  )
}

function AdminTeamTable({ teams, caption }: { teams: AdminTeam[]; caption: string }) {
  const { t } = useTranslation()
  const setEnabled = useSetTeamEnabled()
  return (
    <Table caption={caption}>
      <THead>
        <tr>
          <Th>{t('columns.team')}</Th>
          <Th>{t('columns.league')}</Th>
          <Th className="text-right">{t('admin.usesWiePlay')}</Th>
        </tr>
      </THead>
      <TBody>
        {teams.map((team) => (
          <tr key={team.id} className="[&>td]:align-middle">
            <Td>
              <span className="flex items-start gap-2">
                <SportBadge sport={team.sport} />
                <span className="font-semibold">{team.name}</span>
              </span>
            </Td>
            <Td>{team.league || '–'}</Td>
            <Td tight className="text-right">
              <Switch
                checked={team.enabled}
                label={t('admin.usesWiePlayFor', { team: `${team.name} ${team.season}` })}
                onChange={(enabled) => setEnabled.mutate({ team, enabled })}
              />
            </Td>
          </tr>
        ))}
      </TBody>
    </Table>
  )
}

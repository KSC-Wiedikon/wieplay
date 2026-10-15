import { useTranslation } from 'react-i18next'
import { SportBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { SyncIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ForbiddenError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { useAdminTeams, useRunSync, useSetTeamEnabled, useSyncStatus } from '../lib/queries'
import { useSession } from '../lib/session'
import { groupBySport } from '../lib/teams'
import type { AdminTeam, SyncStatus } from '../lib/types'

export function AdminTeamsPage() {
  const { t } = useTranslation()
  const me = useSession()
  return (
    <div className="flex flex-col gap-8">
      <title>{`${t('admin.title')} · nomal`}</title>
      <h1 className="text-2xl font-bold">{t('admin.title')}</h1>
      {me.is_admin ? (
        <>
          <SyncPanel />
          <TeamsPanel />
        </>
      ) : (
        <Message>{t('admin.forbidden')}</Message>
      )}
    </div>
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
    [t('admin.sync.trainings'), status.counts.trainings],
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
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {counts.map(([label, value]) => (
          <div key={label} className="flex flex-col-reverse rounded-xl bg-sunken px-3 py-2">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="text-xl font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </>
  )
}

function TeamsPanel() {
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
        groupBySport(teams.data).map(({ sport, teams }) => (
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
          <Th className="hidden sm:table-cell">{t('columns.season')}</Th>
          <Th className="text-right">{t('admin.usesNomal')}</Th>
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
              <span className="mt-1 block text-xs text-muted sm:hidden">{team.season}</span>
            </Td>
            <Td>{team.league || '–'}</Td>
            <Td className="hidden whitespace-nowrap sm:table-cell">{team.season}</Td>
            <Td tight className="text-right">
              <Switch
                checked={team.enabled}
                label={t('admin.usesNomalFor', { team: `${team.name} ${team.season}` })}
                onChange={(enabled) => setEnabled.mutate({ team, enabled })}
              />
            </Td>
          </tr>
        ))}
      </TBody>
    </Table>
  )
}

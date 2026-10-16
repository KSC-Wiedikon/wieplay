import type { TFunction } from 'i18next'
import { Fragment, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { SportBadge } from '../components/Badge'
import { ChevronDownIcon } from '../components/icons'
import { Card } from '../components/Card'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead, Tr } from '../components/Table'
import { useTeams } from '../lib/queries'
import { cx } from '../lib/cx'
import { groupBySeasons, groupBySport } from '../lib/teams'
import type { Team } from '../lib/types'

function roleLabel(t: TFunction, rel: string): string {
  switch (rel) {
    case 'player':
      return t('roles.player')
    case 'coach':
      return t('roles.coach')
    case 'captain':
      return t('roles.captain')
    case 'team_responsible':
      return t('roles.team_responsible')
    default:
      return rel
  }
}

export function HomePage() {
  const { t } = useTranslation()
  const teams = useTeams()

  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('home.title')} · nomal`}</title>
      <h1 className="text-2xl font-bold">{t('home.title')}</h1>
      {teams.isPending ? (
        <PageSpinner />
      ) : teams.isError ? (
        <LoadError onRetry={() => void teams.refetch()} />
      ) : teams.data.length === 0 ? (
        <Message>{t('home.empty')}</Message>
      ) : (
        groupBySport(teams.data).map(({ sport, teams }) => (
          <section key={sport} aria-labelledby={`sport-${sport}`} className="flex flex-col gap-3">
            <h2 id={`sport-${sport}`} className="text-lg font-semibold">
              {t(`sport.${sport}`)}
            </h2>
            <Card className="overflow-hidden">
              <TeamTable teams={teams} caption={t(`sport.${sport}`)} />
            </Card>
          </section>
        ))
      )}
    </div>
  )
}

function TeamTable({ teams, caption }: { teams: Team[]; caption: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const toggle = (lineage: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(lineage)) next.delete(lineage)
      else next.add(lineage)
      return next
    })
  return (
    <Table caption={caption}>
      <THead>
        <tr>
          <Th>{t('columns.team')}</Th>
          <Th>{t('columns.league')}</Th>
          <Th className="hidden sm:table-cell">{t('columns.season')}</Th>
          <Th>{t('columns.roles')}</Th>
        </tr>
      </THead>
      <TBody>
        {groupBySeasons(teams).map(({ latest, earlier }) => {
          const expanded = open.has(latest.lineage_id)
          const panel = `earlier-${latest.lineage_id}`
          return (
            <Fragment key={latest.lineage_id}>
              <TeamRow
                team={latest}
                toggle={
                  earlier.length > 0 ? (
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-controls={panel}
                      aria-label={t(expanded ? 'home.hideEarlier' : 'home.showEarlier', { name: latest.name })}
                      title={t(expanded ? 'home.hideEarlier' : 'home.showEarlier', { name: latest.name })}
                      onClick={() => toggle(latest.lineage_id)}
                      className="-my-1 inline-flex size-8 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-fg"
                    >
                      <ChevronDownIcon className={cx('size-4 transition-transform', expanded && 'rotate-180')} />
                    </button>
                  ) : null
                }
              />
              {expanded &&
                earlier.map((team) => <TeamRow key={team.id} team={team} earlier id={panel} />)}
            </Fragment>
          )
        })}
      </TBody>
    </Table>
  )
}

function TeamRow({ team, toggle, earlier, id }: { team: Team; toggle?: ReactNode; earlier?: boolean; id?: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <Tr id={id} onActivate={() => navigate(`/teams/${team.id}`)} className={earlier ? 'bg-sunken/50' : undefined}>
      <Td>
        <span className={cx('flex items-start gap-2', earlier && 'pl-6')}>
          {!earlier && <SportBadge sport={team.sport} />}
          <Link
            to={`/teams/${team.id}`}
            className={cx('text-link underline-offset-2 hover:underline', earlier ? 'text-sm' : 'font-semibold')}
          >
            {team.name}
          </Link>
          {earlier && <span className="text-sm text-muted sm:hidden">{team.season}</span>}
          {toggle}
        </span>
      </Td>
      <Td className={earlier ? 'text-sm text-muted' : undefined}>{team.league || '–'}</Td>
      <Td className={cx('hidden whitespace-nowrap sm:table-cell', earlier && 'text-sm text-muted')}>{team.season}</Td>
      <Td className={cx('text-muted', earlier && 'text-sm')}>
        {team.rel.length > 0 ? team.rel.map((rel) => roleLabel(t, rel)).join(', ') : t('roles.none')}
      </Td>
    </Tr>
  )
}

import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { SportBadge } from '../components/Badge'
import { Card } from '../components/Card'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead, Tr } from '../components/Table'
import { useTeams } from '../lib/queries'
import { groupBySport } from '../lib/teams'
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
  const navigate = useNavigate()
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
        {teams.map((team) => (
          <Tr key={team.id} onActivate={() => navigate(`/teams/${team.id}`)}>
            <Td>
              <span className="flex items-start gap-2">
                <SportBadge sport={team.sport} />
                <Link to={`/teams/${team.id}`} className="font-semibold text-link underline-offset-2 hover:underline">
                  {team.name}
                </Link>
              </span>
            </Td>
            <Td>{team.league || '–'}</Td>
            <Td className="hidden whitespace-nowrap sm:table-cell">{team.season}</Td>
            <Td className="text-muted">
              {team.rel.length > 0 ? team.rel.map((rel) => roleLabel(t, rel)).join(', ') : t('roles.none')}
            </Td>
          </Tr>
        ))}
      </TBody>
    </Table>
  )
}

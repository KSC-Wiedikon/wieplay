import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router'
import { SportBadge } from '../components/Badge'
import { Card } from '../components/Card'
import { LoadError, Message } from '../components/Message'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ForbiddenError, NotFoundError } from '../lib/api'
import { formatDate, formatTime, todayIso } from '../lib/format'
import { opponentOf, scoreOf, setsOf, splitGames } from '../lib/games'
import { useTeam, useTeamGames } from '../lib/queries'
import { sportOf, useSportAccent } from '../lib/sport'
import type { Game } from '../lib/types'
import { NotFoundPage } from './NotFoundPage'
import { FilmingSection, VideosSection } from './TeamSections'

export function TeamPage() {
  const { id } = useParams()
  if (!id || !/^\d+$/.test(id)) return <NotFoundPage />
  return <TeamView teamId={Number(id)} />
}

function TeamView({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const team = useTeam(teamId)
  const games = useTeamGames(teamId)
  useSportAccent(team.data ? sportOf(team.data.sport) : 'vb')

  if (team.isPending) return <PageSpinner />
  if (team.isError) {
    if (team.error instanceof NotFoundError) return <Message>{t('team.notFound')}</Message>
    if (team.error instanceof ForbiddenError) return <Message>{t('team.forbidden')}</Message>
    return <LoadError onRetry={() => void team.refetch()} />
  }

  const today = todayIso()
  const { name, league, season, sport } = team.data

  return (
    <div className="flex flex-col gap-8">
      <title>{`${name} · nomal`}</title>
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <SportBadge sport={sport} />
          <h1 className="text-2xl font-bold">{name}</h1>
        </div>
        <p className="text-muted">{[league, season].filter(Boolean).join(' · ')}</p>
      </div>

      <VideosSection team={team.data} />
      <FilmingSection teamId={teamId} />

      <Section title={t('team.upcomingGames')} query={games}>
        {(data) => <UpcomingGames games={splitGames(data, today).upcoming} />}
      </Section>
      <Section title={t('team.playedGames')} query={games}>
        {(data) => <PlayedGames games={splitGames(data, today).played} />}
      </Section>

    </div>
  )
}

interface SectionQuery<T> {
  data: T | undefined
  isPending: boolean
  isError: boolean
  refetch: () => unknown
}

function Section<T>({ title, query, children }: { title: string; query: SectionQuery<T>; children: (data: T) => ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {query.isPending ? (
        <Spinner />
      ) : query.isError || query.data === undefined ? (
        <LoadError onRetry={() => void query.refetch()} />
      ) : (
        children(query.data)
      )}
    </section>
  )
}

function UpcomingGames({ games }: { games: Game[] }) {
  const { t } = useTranslation()
  if (games.length === 0) return <Message>{t('team.noUpcomingGames')}</Message>
  return (
    <Card className="overflow-hidden">
      <Table caption={t('team.upcomingGames')}>
        <THead>
          <tr>
            <Th>{t('columns.date')}</Th>
            <Th>{t('columns.time')}</Th>
            <Th>{t('columns.opponent')}</Th>
            <Th className="hidden sm:table-cell">{t('columns.side')}</Th>
            <Th className="hidden sm:table-cell">{t('columns.hall')}</Th>
          </tr>
        </THead>
        <TBody>
          {games.map((game) => (
            <tr key={game.id}>
              <Td className="whitespace-nowrap tabular-nums">{formatDate(game.date)}</Td>
              <Td className="whitespace-nowrap tabular-nums">{formatTime(game.time) || '–'}</Td>
              <Td className="font-semibold">{opponentOf(game)}</Td>
              <Td className="hidden sm:table-cell">
                {game.our_side === 'home' ? t('team.home') : game.our_side === 'away' ? t('team.away') : '–'}
              </Td>
              <Td className="hidden sm:table-cell">{game.hall_name || '–'}</Td>
            </tr>
          ))}
        </TBody>
      </Table>
    </Card>
  )
}

function PlayedGames({ games }: { games: Game[] }) {
  const { t } = useTranslation()
  if (games.length === 0) return <Message>{t('team.noPlayedGames')}</Message>
  return (
    <Card className="overflow-hidden">
      <Table caption={t('team.playedGames')}>
        <THead>
          <tr>
            <Th>{t('columns.date')}</Th>
            <Th>{t('columns.opponent')}</Th>
            <Th>{t('columns.score')}</Th>
            <Th className="hidden sm:table-cell">{t('columns.sets')}</Th>
          </tr>
        </THead>
        <TBody>
          {games.map((game) => (
            <tr key={game.id}>
              <Td className="whitespace-nowrap tabular-nums">{formatDate(game.date)}</Td>
              <Td className="font-semibold">{opponentOf(game)}</Td>
              <Td className="font-semibold whitespace-nowrap tabular-nums">{scoreOf(game) || '–'}</Td>
              <Td className="hidden text-muted tabular-nums sm:table-cell">{setsOf(game) || '–'}</Td>
            </tr>
          ))}
        </TBody>
      </Table>
    </Card>
  )
}

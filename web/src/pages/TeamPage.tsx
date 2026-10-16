import { lazy, Suspense, useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router'
import { SportBadge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ChevronRightIcon, PlayIcon } from '../components/icons'
import { Card } from '../components/Card'
import { LoadError, Message } from '../components/Message'
import { PageHeader, SectionHeader } from '../components/PageHeader'
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

// The season's numbers are a wide table with strings of their own: loaded when shown.
const SeasonStats = lazy(() => import('./SeasonStats').then((m) => ({ default: m.SeasonStats })))

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
    <div className="flex flex-col gap-8 sm:gap-10">
      <title>{`${name} · nomal`}</title>
      <PageHeader
        eyebrow={
          <>
            <Link to="/" className="link">
              {t('home.title')}
            </Link>
            <ChevronRightIcon className="size-4" />
            <SportBadge sport={sport} />
            <span>{t(`sport.${sport}`)}</span>
          </>
        }
        title={name}
        subtitle={[league, season].filter(Boolean).join(' · ')}
      />

      <VideosSection team={team.data} />
      {team.data.rel.includes('player') && <HighlightsSection teamId={teamId} />}
      {sport === 'volleyball' && (
        <Suspense fallback={<Spinner />}>
          <SeasonStats team={team.data} />
        </Suspense>
      )}

      {/* Wide screens: what's coming on the left, what was played on the right. */}
      <div className="grid gap-8 sm:gap-10 xl:grid-cols-2 xl:items-start">
        <div className="flex min-w-0 flex-col gap-8 sm:gap-10">
          <FilmingSection teamId={teamId} />
          <Section title={t('team.upcomingGames')} query={games}>
            {(data) => <UpcomingGames games={splitGames(data, today).upcoming} />}
          </Section>
        </div>
        <Section title={t('team.playedGames')} query={games}>
          {(data) => <PlayedGames games={splitGames(data, today).played} />}
        </Section>
      </div>
    </div>
  )
}

/** Players: their own tagged moments of this season, played as a reel (a smart reel preview). */
function HighlightsSection({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  return (
    <section
      aria-labelledby="team-highlights"
      className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-card border border-hairline bg-accent-soft p-4 sm:p-5"
    >
      <span className="grid size-11 shrink-0 place-items-center rounded-full bg-accent text-on-accent">
        <PlayIcon className="size-5" />
      </span>
      <div className="flex min-w-0 flex-1 basis-56 flex-col gap-0.5">
        <h2 id="team-highlights" className="font-semibold">
          {t('team.highlights')}
        </h2>
        <p className="text-sm text-muted">{t('team.highlightsHint')}</p>
      </div>
      <ButtonLink to={`/teams/${teamId}/highlights`} variant="primary">
        {t('team.highlightsWatch')}
      </ButtonLink>
    </section>
  )
}

interface SectionQuery<T> {
  data: T | undefined
  isPending: boolean
  isError: boolean
  refetch: () => unknown
}

function Section<T>({ title, query, children }: { title: string; query: SectionQuery<T>; children: (data: T) => ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader id={id} title={title} />
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

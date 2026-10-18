import { useTranslation } from 'react-i18next'
import { Link, Navigate, useParams } from 'react-router'
import { SportBadge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ChevronRightIcon, HomeIcon, UploadIcon, VideoIcon } from '../components/icons'
import { EmptyState, LoadError } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { PageSpinner } from '../components/Spinner'
import { VIDEO_GRID, VideoCard } from '../components/VideoCard'
import { formatDate, todayIso } from '../lib/format'
import { useGameVideos, useVideoConfig } from '../lib/queries'
import type { LinkedGame } from '../lib/types'
import { NotFoundPage } from './NotFoundPage'

/** Wiedisync's game ids: Postgres integers. */
const MAX_ID = 2 ** 31 - 1

/**
 * Where a Wiedisync game card's "Video" link lands (`/games/:id`, PLAN §4.4), after the login
 * if there was none. One video: straight to it. Several (more than one camera): a short list.
 * None, or a game the viewer may not see (the API answers both alike): a friendly note, with the
 * way to the team and, for those who may upload, to the upload with the game picked.
 */
export function GamePage() {
  const { gameId } = useParams()
  const id = gameId && /^\d{1,10}$/.test(gameId) ? Number(gameId) : 0
  if (id < 1 || id > MAX_ID) return <NotFoundPage />
  return <GameView gameId={id} />
}

function GameView({ gameId }: { gameId: number }) {
  const { t } = useTranslation()
  const found = useGameVideos(gameId)

  if (found.isPending) return <PageSpinner />
  if (found.isError) return <LoadError onRetry={() => void found.refetch()} />

  const { game, videos } = found.data
  const [only] = videos
  if (videos.length === 1 && only) return <Navigate to={`/videos/${only.id}`} replace />

  const title = game ? `${game.home_name ?? '?'} – ${game.away_name ?? '?'}` : t('game.title')
  // A game between two KSCW teams can have videos from both: then say whose each one is.
  const teams = new Set(videos.map((v) => v.team_id))

  return (
    <div className="flex flex-col gap-6">
      <title>{`${title} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          game ? (
            <>
              <SportBadge sport={game.team.sport} />
              <Link to={`/teams/${game.team.id}`} className="font-semibold link">
                {`${game.team.name} ${game.team.season}`}
              </Link>
              <ChevronRightIcon className="size-4" />
              {game.date && <span className="tabular-nums">{formatDate(game.date)}</span>}
            </>
          ) : undefined
        }
        title={title}
        subtitle={videos.length > 1 ? t('game.several') : undefined}
      />
      {videos.length > 1 ? (
        <ul className={VIDEO_GRID}>
          {videos.map((v) => (
            <li key={v.id} className="min-w-0">
              <VideoCard video={v} team={teams.size > 1} />
            </li>
          ))}
        </ul>
      ) : (
        <NoVideo game={game} />
      )}
    </div>
  )
}

function NoVideo({ game }: { game: LinkedGame | null }) {
  const { t } = useTranslation()
  const config = useVideoConfig()
  // The upload page offers played games only (today's included).
  const played = game?.date != null && game.date <= todayIso()
  const upload = game !== null && game.team.can_upload && played && config.data?.enabled === true

  return (
    <EmptyState
      icon={<VideoIcon className="size-6" />}
      title={t('game.none')}
      action={
        game ? (
          <>
            {upload && (
              <ButtonLink to={`/teams/${game.team.id}/upload?game=${game.id}`} variant="primary">
                <UploadIcon className="size-4" />
                {t('game.upload')}
              </ButtonLink>
            )}
            <ButtonLink to={`/teams/${game.team.id}`} variant={upload ? 'secondary' : 'primary'}>
              {t('game.toTeam', { team: game.team.name })}
            </ButtonLink>
          </>
        ) : (
          <ButtonLink to="/" variant="primary">
            <HomeIcon className="size-4" />
            {t('notFound.back')}
          </ButtonLink>
        )
      }
    >
      {t('game.noneHint')}
    </EmptyState>
  )
}

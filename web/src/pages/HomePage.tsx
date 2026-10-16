import { useQueries, type UseQueryResult } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { SportBadge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { ChevronDownIcon, FilmIcon, ReelIcon, ScissorsIcon, SlidersIcon, UploadIcon, UsersIcon } from '../components/icons'
import { EmptyState, LoadError } from '../components/Message'
import { PageHeader, SectionHeader } from '../components/PageHeader'
import { PageSpinner } from '../components/Spinner'
import { cx } from '../lib/cx'
import { firstName } from '../lib/names'
import { teamVideosQuery, useTeams, useVideoConfig } from '../lib/queries'
import { useSession } from '../lib/session'
import { mayUpload, teamEntries } from '../lib/teams'
import type { Team, Video } from '../lib/types'
import { newestFirst } from '../lib/video'
import { RecentVideoGrid, RecentVideoSkeleton } from './home/RecentVideos'
import { TeamCard } from './home/TeamCard'

/** How many videos "Recent videos" shows. */
const RECENT = 8

/** The start page: a greeting, the viewer's teams as cards and the newest videos of those teams. */
export function HomePage() {
  const { t } = useTranslation()
  const me = useSession()
  const teams = useTeams()
  const config = useVideoConfig()
  const name = firstName(me.member.name)

  const entries = useMemo(() => teamEntries(teams.data ?? []), [teams.data])
  // Each team's newest season, and the one before it: right after a new season starts, the
  // recent videos are still in last season's team.
  const videoTeams = useMemo(() => entries.flatMap(({ latest, earlier }) => [latest, ...earlier.slice(0, 1)]), [entries])
  const videoQueries = useQueries({ queries: videoTeams.map((team) => teamVideosQuery(team.id)) })
  const videosOf = new Map<number, UseQueryResult<Video[]>>(videoTeams.map((team, i) => [team.id, videoQueries[i] as UseQueryResult<Video[]>]))

  const videoEnabled = config.data?.enabled !== false
  const uploadTeams = videoEnabled && config.data ? entries.map((e) => e.latest).filter((team) => mayUpload(team, me)) : []

  return (
    <div className="flex flex-col gap-8 sm:gap-10">
      <title>{`${t('home.title')} · nomal`}</title>
      <PageHeader
        title={name ? t('home.greeting', { name }) : t('home.greetingAnon')}
        subtitle={t('home.subtitle')}
        actions={
          <>
            <UploadAction teams={uploadTeams} />
            {/* On phones the tab row below the bar already has these. */}
            <ButtonLink to="/clips" className="max-sm:hidden">
              <ScissorsIcon className="size-4" />
              {t('home.myClips')}
            </ButtonLink>
            <ButtonLink to="/reels" className="max-sm:hidden">
              <ReelIcon className="size-4" />
              {t('home.reels')}
            </ButtonLink>
          </>
        }
      />

      {teams.isPending ? (
        <PageSpinner />
      ) : teams.isError ? (
        <LoadError onRetry={() => void teams.refetch()} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={<UsersIcon />}
          title={t('home.empty')}
          action={
            me.is_admin ? (
              <ButtonLink to="/admin/teams" variant="primary">
                <SlidersIcon className="size-4" />
                {t('home.emptyAdmin')}
              </ButtonLink>
            ) : null
          }
        >
          {t('home.emptyHint')}
        </EmptyState>
      ) : (
        <>
          <section aria-labelledby="home-teams" className="flex flex-col gap-4">
            <SectionHeader id="home-teams" title={t('home.title')} />
            <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {entries.map(({ latest, earlier }) => (
                <li key={latest.lineage_id} className="flex">
                  <TeamCard latest={latest} earlier={earlier} videos={videosOf.get(latest.id)} />
                </li>
              ))}
            </ul>
          </section>
          {videoEnabled && (
            <RecentSection queries={videoQueries} canUpload={uploadTeams.length > 0} uploadAction={<UploadAction teams={uploadTeams} />} />
          )}
        </>
      )}
    </div>
  )
}

function RecentSection({
  queries,
  canUpload,
  uploadAction,
}: {
  queries: UseQueryResult<Video[]>[]
  canUpload: boolean
  uploadAction: ReactNode
}) {
  const { t } = useTranslation()
  const loading = queries.some((q) => q.isPending)
  const videos = newestFirst(queries.flatMap((q) => q.data ?? []).filter((video) => video.status !== 'refused')).slice(0, RECENT)
  const failed = !loading && queries.length > 0 && queries.every((q) => q.isError)

  return (
    <section aria-labelledby="home-recent" className="flex flex-col gap-4">
      <SectionHeader id="home-recent" title={t('home.recent')} hint={t('home.recentHint')} />
      {failed ? (
        <LoadError onRetry={() => queries.forEach((q) => void q.refetch())} />
      ) : loading && videos.length === 0 ? (
        <RecentVideoSkeleton />
      ) : videos.length === 0 ? (
        <EmptyState icon={<FilmIcon />} title={t('home.noVideos')} action={canUpload ? uploadAction : null}>
          {canUpload ? t('home.noVideosUpload') : t('home.noVideosPlayer')}
        </EmptyState>
      ) : (
        <RecentVideoGrid videos={videos} />
      )}
    </section>
  )
}

/** "Upload a video": straight to the upload page for one team, a short team list for several. */
function UploadAction({ teams }: { teams: Team[] }) {
  const { t } = useTranslation()
  const id = useId()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        button.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const [only] = teams
  if (!only) return null
  if (teams.length === 1) {
    return (
      <ButtonLink to={`/teams/${only.id}/upload`} variant="primary">
        <UploadIcon className="size-4" />
        {t('home.upload')}
      </ButtonLink>
    )
  }
  return (
    <div ref={root} className="relative">
      <Button ref={button} variant="primary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        <UploadIcon className="size-4" />
        {t('home.upload')}
        <ChevronDownIcon className={cx('size-4 transition-transform motion-reduce:transition-none', open && 'rotate-180')} />
      </Button>
      {open && (
        <div
          id={id}
          className="absolute top-full left-0 z-30 mt-2 w-64 max-w-[calc(100vw-2rem)] rounded-card border border-hairline bg-surface p-1.5 shadow-raised"
        >
          <p className="px-2.5 pt-1.5 pb-1 text-xs font-semibold text-muted">{t('home.uploadFor')}</p>
          <ul className="flex flex-col">
            {teams.map((team) => (
              <li key={team.id}>
                <Link
                  to={`/teams/${team.id}/upload`}
                  onClick={() => setOpen(false)}
                  className="flex min-h-11 items-center gap-2 rounded-control px-2.5 text-sm font-semibold hover:bg-hover sm:min-h-9"
                >
                  <SportBadge sport={team.sport} />
                  <span className="truncate">{team.name}</span>
                  <span className="ml-auto text-xs font-normal text-muted tabular-nums">{team.season}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

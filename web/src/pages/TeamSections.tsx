import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { CalendarIcon, FilmIcon, UploadIcon } from '../components/icons'
import { EmptyState, LoadError, Message } from '../components/Message'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { BlockerList, FilmingBadge } from '../components/VideoBadges'
import { VIDEO_GRID, VideoCard } from '../components/VideoCard'
import { formatDate } from '../lib/format'
import { useTeamVideos, useUpcomingFilming, useVideoConfig } from '../lib/queries'
import type { TeamDetail, UpcomingFilming, Video } from '../lib/types'
import { byMonth, monthLabel } from '../lib/video'

// Videos

export function VideosSection({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const id = useId()
  const config = useVideoConfig()
  const videos = useTeamVideos(team.id)
  const disabled = config.data?.enabled === false

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader
        id={id}
        title={t('team.videos')}
        action={
          team.can_upload && config.data?.enabled ? (
            <ButtonLink to={`/teams/${team.id}/upload`} variant="primary">
              <UploadIcon className="size-4" />
              {t('team.upload')}
            </ButtonLink>
          ) : null
        }
      />
      {disabled ? (
        <Message>{t('video.notConfigured')}</Message>
      ) : videos.isPending ? (
        <Spinner />
      ) : videos.isError ? (
        <LoadError onRetry={() => void videos.refetch()} />
      ) : videos.data.length === 0 ? (
        <EmptyState icon={<FilmIcon />} title={t('team.noVideos')}>
          {team.can_upload ? t('home.noVideosUpload') : t('home.noVideosPlayer')}
        </EmptyState>
      ) : (
        <VideoMonths videos={videos.data} />
      )}
    </section>
  )
}

/** The team's videos as poster cards, one group per month, newest first. */
function VideoMonths({ videos }: { videos: Video[] }) {
  const { i18n } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      {byMonth(videos).map(({ month, videos }) => {
        const label = monthLabel(month, i18n.language)
        return (
          <section key={month} aria-label={label} className="flex flex-col gap-2.5">
            <h3 className="text-sm font-semibold text-muted">{label}</h3>
            <ul aria-label={label} className={VIDEO_GRID}>
              {videos.map((video) => (
                <li key={video.id}>
                  <VideoCard video={video} />
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

// Filming allowed / blocked for the next games

export function FilmingSection({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const id = useId()
  const upcoming = useUpcomingFilming(teamId)
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader id={id} title={t('team.filming')} hint={t('team.filmingHint')} />
      {upcoming.isPending ? (
        <Spinner />
      ) : upcoming.isError ? (
        <LoadError onRetry={() => void upcoming.refetch()} />
      ) : upcoming.data.length === 0 ? (
        <Message icon={<CalendarIcon />}>{t('team.noUpcoming')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <FilmingTable items={upcoming.data} />
        </Card>
      )}
    </section>
  )
}

function FilmingTable({ items }: { items: UpcomingFilming[] }) {
  const { t } = useTranslation()
  return (
    <Table caption={t('team.filming')}>
      <THead>
        <tr>
          <Th>{t('columns.date')}</Th>
          <Th>{t('columns.what')}</Th>
          <Th className="hidden text-right sm:table-cell">{t('columns.filming')}</Th>
        </tr>
      </THead>
      <TBody>
        {items.map((item) => (
          <tr key={item.id}>
            <Td className="whitespace-nowrap tabular-nums">{formatDate(item.date)}</Td>
            <Td>
              <span className="mb-1 block sm:hidden">
                <FilmingBadge allowed={item.allowed} />
              </span>
              <span className="block font-semibold">{item.label}</span>
              {!item.allowed && item.blockers.length > 0 && (
                <div className="mt-2">
                  <BlockerList blockers={item.blockers} />
                </div>
              )}
            </Td>
            <Td className="hidden text-right sm:table-cell">
              <FilmingBadge allowed={item.allowed} />
            </Td>
          </tr>
        ))}
      </TBody>
    </Table>
  )
}

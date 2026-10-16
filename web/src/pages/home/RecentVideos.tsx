import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { SportBadge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { VideoStatusBadge } from '../../components/VideoBadges'
import { VideoPoster } from '../../components/VideoPoster'
import { formatDate, formatDuration } from '../../lib/format'
import type { Video } from '../../lib/types'

/** The newest videos of the viewer's teams, as poster tiles. */
export function RecentVideoGrid({ videos }: { videos: Video[] }) {
  const { t } = useTranslation()
  return (
    <ul aria-label={t('home.recent')} className="grid gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 xl:grid-cols-4">
      {videos.map((video) => (
        <li key={video.id}>
          <VideoTile video={video} />
        </li>
      ))}
    </ul>
  )
}

function VideoTile({ video }: { video: Video }) {
  const duration = video.status === 'ready' ? formatDuration(video.duration_ms) : ''
  return (
    <Card className="group relative flex h-full overflow-hidden hover:border-hairline-strong hover:shadow-raised sm:flex-col">
      <div className="relative w-36 shrink-0 self-start overflow-hidden sm:w-full">
        <VideoPoster video={video} className="w-full transition-transform duration-500 motion-safe:group-hover:scale-[1.03]" />
        {duration && (
          <span className="absolute right-1.5 bottom-1.5 rounded-md bg-navy/85 px-1.5 py-0.5 text-xs font-semibold text-light tabular-nums">
            {duration}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5 p-3">
        <Link
          to={`/videos/${video.id}`}
          className="line-clamp-2 rounded-sm font-semibold wrap-break-word decoration-link-line underline-offset-4 group-hover:underline focus-visible:outline-none after:absolute after:inset-0 after:rounded-card focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
        >
          {video.title}
        </Link>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
          <SportBadge sport={video.sport} />
          <span className="font-semibold text-fg">{video.team_name}</span>
          <span className="tabular-nums">{formatDate(video.recorded_on)}</span>
        </p>
        {video.status !== 'ready' && (
          <span>
            <VideoStatusBadge status={video.status} />
          </span>
        )}
      </div>
    </Card>
  )
}

/** Grey tiles while the videos load. */
export function RecentVideoSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div aria-hidden="true" className="grid gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex gap-3 rounded-card border border-hairline bg-surface p-0 sm:flex-col">
          <div className="aspect-video w-36 shrink-0 rounded-l-card bg-sunken motion-safe:animate-pulse sm:w-full sm:rounded-t-card sm:rounded-bl-none" />
          <div className="flex flex-1 flex-col gap-2 p-3">
            <div className="h-4 w-3/4 rounded bg-sunken motion-safe:animate-pulse" />
            <div className="h-3 w-1/2 rounded bg-sunken motion-safe:animate-pulse" />
          </div>
        </div>
      ))}
    </div>
  )
}

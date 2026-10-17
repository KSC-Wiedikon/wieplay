import { Link } from 'react-router'
import { formatDate, formatDuration } from '../lib/format'
import type { Video } from '../lib/types'
import { kindLabel } from '../lib/video'
import { useTranslation } from 'react-i18next'
import { SportBadge } from './Badge'
import { Card } from './Card'
import { AnalysisBadge, VideoStatusBadge } from './VideoBadges'
import { VideoPoster } from './VideoPoster'

/** The grid the video cards sit in: one column on phones (poster beside the text), up to five. */
export const VIDEO_GRID = 'grid gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5'

/**
 * A video as a poster card: the whole card opens the video. `team` adds the sport and the team
 * (the start page lists several teams; a team's page does not need it).
 */
export function VideoCard({ video, team = false }: { video: Video; team?: boolean }) {
  const { t } = useTranslation()
  const duration = video.status === 'ready' ? formatDuration(video.duration_ms) : ''
  const kind = team ? null : kindLabel(t, video.kind)
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
          {team && <SportBadge sport={video.sport} />}
          {team && <span className="font-semibold text-fg">{video.team_name}</span>}
          <span className="tabular-nums">{formatDate(video.recorded_on)}</span>
          {kind && <span>{kind}</span>}
        </p>
        <p className="flex flex-wrap items-center gap-1.5 empty:hidden">
          {video.status !== 'ready' && <VideoStatusBadge status={video.status} />}
          <AnalysisBadge video={video} />
        </p>
      </div>
    </Card>
  )
}

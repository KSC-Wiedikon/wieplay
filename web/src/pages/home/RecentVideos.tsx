import { useTranslation } from 'react-i18next'
import { VIDEO_GRID, VideoCard } from '../../components/VideoCard'
import type { Video } from '../../lib/types'

/** The newest videos of the viewer's teams, as poster tiles. */
export function RecentVideoGrid({ videos }: { videos: Video[] }) {
  const { t } = useTranslation()
  return (
    <ul aria-label={t('home.recent')} className={VIDEO_GRID}>
      {videos.map((video) => (
        <li key={video.id}>
          <VideoCard video={video} team />
        </li>
      ))}
    </ul>
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

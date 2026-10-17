import { useTranslation } from 'react-i18next'
import { blockerMessage } from '../lib/blockers'
import type { Blocker, Video, VideoStatus } from '../lib/types'
import { Badge } from './Badge'
import { AlertIcon, CheckIcon } from './icons'

const statusTone = {
  uploading: 'accent',
  importing: 'accent',
  queued: 'accent',
  processing: 'accent',
  ready: 'neutral',
  failed: 'danger',
  refused: 'danger',
} as const

export function VideoStatusBadge({ status }: { status: VideoStatus }) {
  const { t } = useTranslation()
  return <Badge tone={statusTone[status] ?? 'neutral'}>{t(`video.status.${status}`, status)}</Badge>
}

/** Whether Nomal has analysed a ready video and how many rally suggestions wait for a coach. */
export function AnalysisBadge({ video }: { video: Pick<Video, 'status' | 'analysis' | 'rallies_reviewed_at'> }) {
  const { t } = useTranslation()
  const analysis = video.analysis
  if (video.status !== 'ready' || !analysis || analysis.status === 'none') return null
  if (analysis.status === 'queued') return <Badge tone="neutral">{t('video.analysis.queued')}</Badge>
  if (analysis.status === 'running') return <Badge tone="accent">{t('video.analysis.running')}</Badge>
  if (analysis.status === 'failed') return <Badge tone="danger">{t('video.analysis.failed')}</Badge>
  if (analysis.open > 0) return <Badge tone="accent">{t('video.analysis.open', { count: analysis.open })}</Badge>
  if (video.rallies_reviewed_at)
    return (
      <Badge tone="neutral" className="gap-1 whitespace-nowrap">
        <CheckIcon className="size-3.5" />
        {t('video.analysis.reviewed')}
      </Badge>
    )
  return <Badge tone="neutral">{t('video.analysis.done')}</Badge>
}

export function FilmingBadge({ allowed }: { allowed: boolean }) {
  const { t } = useTranslation()
  return (
    <Badge tone={allowed ? 'neutral' : 'danger'} className="gap-1 whitespace-nowrap">
      {allowed ? <CheckIcon className="size-3.5" /> : <AlertIcon className="size-3.5" />}
      {allowed ? t('filming.allowed') : t('filming.blocked')}
    </Badge>
  )
}

export function BlockerList({ blockers }: { blockers: readonly Blocker[] }) {
  const { t } = useTranslation()
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
      {blockers.map((blocker, index) => (
        <li key={`${blocker.code}-${index}`}>{blockerMessage(t, blocker)}</li>
      ))}
    </ul>
  )
}

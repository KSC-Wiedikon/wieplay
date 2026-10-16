import { useTranslation } from 'react-i18next'
import { blockerMessage } from '../lib/blockers'
import type { Blocker, VideoStatus } from '../lib/types'
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

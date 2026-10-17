import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { Badge } from '../components/Badge'
import { ButtonLink, Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { PlayIcon, ScissorsIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead, Tr } from '../components/Table'
import { newestFirst } from '../lib/clips'
import { formatDate, formatDuration } from '../lib/format'
import { useDeleteClip, useMyClips } from '../lib/library'
import { useToast } from '../lib/toast'
import type { Clip } from '../lib/types'

function clipUrl(clip: Clip): string {
  return `/videos/${clip.video_id}?clip=${clip.id}`
}

/** /clips — "my clips" across videos, newest first (PLAN §9.1: on phones WiePlay is a viewer). */
export function ClipsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const toast = useToast()
  const clips = useMyClips()
  const remove = useDeleteClip()
  const [deleting, setDeleting] = useState<Clip | null>(null)

  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('clips.myTitle')} · WiePlay`}</title>
      <PageHeader title={t('clips.myTitle')} subtitle={t('clips.myHint')} />
      {clips.isPending ? (
        <PageSpinner />
      ) : clips.isError ? (
        <LoadError onRetry={() => void clips.refetch()} />
      ) : clips.data.length === 0 ? (
        <Message icon={<ScissorsIcon />}>{t('clips.myEmpty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('clips.myTitle')}>
            <THead>
              <tr>
                <Th>{t('clips.columns.clip')}</Th>
                <Th className="hidden sm:table-cell">{t('clips.columns.video')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.duration')}</Th>
                <Th className="text-right">
                  <span className="sr-only">{t('tagging.table.actions')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {newestFirst(clips.data).map((clip) => (
                <Tr key={clip.id} onActivate={() => void navigate(clipUrl(clip))}>
                  <Td>
                    <Link to={clipUrl(clip)} className="font-semibold link">
                      {clip.title}
                    </Link>
                    {clip.visibility === 'team' && (
                      <Badge tone="accent" className="ml-2 align-middle">
                        {t('clips.visibility.team')}
                      </Badge>
                    )}
                    <span className="mt-0.5 block text-xs text-muted tabular-nums sm:hidden">
                      {[clip.video_title, formatDate(clip.recorded_on), formatDuration(clip.end_ms - clip.start_ms)].join(' · ')}
                    </span>
                    {clip.note && <span className="mt-0.5 block text-xs wrap-break-word text-muted">{clip.note}</span>}
                  </Td>
                  <Td className="hidden sm:table-cell">
                    {clip.video_title}
                    <span className="block text-xs text-muted tabular-nums">{formatDate(clip.recorded_on)}</span>
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDuration(clip.end_ms - clip.start_ms)}</Td>
                  <Td tight className="text-right whitespace-nowrap">
                    <ButtonLink to={clipUrl(clip)} variant="ghost" size="icon" aria-label={t('clips.watchFor', { title: clip.title })}>
                      <PlayIcon className="size-4" />
                    </ButtonLink>
                    <Button variant="ghostDanger" size="icon" aria-label={t('clips.delete', { title: clip.title })} onClick={() => setDeleting(clip)}>
                      <TrashIcon className="size-4" />
                    </Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title={t('clips.deleteTitle')}
        confirmLabel={t('clips.deleteConfirm')}
        danger
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting, {
            onSuccess: () => toast.success(t('clips.deleted')),
            onError: () => toast.error(t('clips.deleteFailed')),
            onSettled: () => setDeleting(null),
          })
        }
      >
        {t('clips.deleteBody', { title: deleting?.title ?? '' })}
      </ConfirmDialog>
    </div>
  )
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertIcon, DownloadIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { ProgressBar } from '../components/ProgressBar'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { cx } from '../lib/cx'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { downloadUrl, useDeleteExport, useExports } from '../lib/library'
import { useToast } from '../lib/toast'
import type { VideoExport } from '../lib/types'

/** Ready and not past its expiry: the link works. */
function downloadable(item: VideoExport, now: number): boolean {
  return item.status === 'ready' && (item.expires_at === null || new Date(item.expires_at).getTime() > now)
}

/** /downloads — my watermarked MP4s (coaches; PLAN §5.2): status, size, expiry, download, delete. */
export function ExportsPage() {
  const { t } = useTranslation()
  const toast = useToast()
  const exports = useExports()
  const remove = useDeleteExport()
  const [deleting, setDeleting] = useState<VideoExport | null>(null)
  const [now] = useState(() => Date.now())

  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('exports.title')} · nomal`}</title>
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold">{t('exports.title')}</h1>
        <p className="flex items-start gap-2 rounded-xl border border-hairline bg-surface p-3 text-sm">
          <AlertIcon className="mt-0.5 size-4 shrink-0" />
          {t('exports.rules')}
        </p>
      </div>
      {exports.isPending ? (
        <PageSpinner />
      ) : exports.isError ? (
        <LoadError onRetry={() => void exports.refetch()} />
      ) : exports.data.length === 0 ? (
        <Message>{t('exports.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('exports.title')}>
            <THead>
              <tr>
                <Th>{t('exports.columns.file')}</Th>
                <Th>{t('exports.columns.status')}</Th>
                <Th className="hidden sm:table-cell">{t('exports.columns.size')}</Th>
                <Th className="hidden sm:table-cell">{t('exports.columns.until')}</Th>
                <Th className="text-right">
                  <span className="sr-only">{t('tagging.table.actions')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {exports.data.map((item) => {
                const ready = downloadable(item, now)
                const expired = item.status === 'expired' || (item.status === 'ready' && !ready)
                const status = expired ? 'expired' : item.status
                return (
                  <tr key={item.id}>
                    <Td>
                      <span className="font-semibold wrap-break-word">{item.title}</span>
                      <span className="mt-0.5 block text-xs text-muted tabular-nums">
                        {[t(`exports.kind.${item.kind}`), formatDuration(item.duration_ms)].join(' · ')}
                      </span>
                      {ready && item.expires_at && (
                        <span className="mt-0.5 block text-xs text-muted tabular-nums sm:hidden">
                          {[item.bytes !== null ? formatBytes(item.bytes) : null, t('exports.until', { date: formatDate(item.expires_at) })]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      )}
                    </Td>
                    <Td className="min-w-28">
                      <Badge tone={status === 'ready' ? 'accent' : status === 'failed' ? 'danger' : 'neutral'}>
                        {t(`exports.status.${status}`)}
                      </Badge>
                      {item.status === 'queued' && (
                        <span className="mt-2 flex items-center gap-2">
                          <ProgressBar value={item.progress} label={t('exports.status.queued')} className="w-24" />
                          {item.progress !== null && <span className="text-xs tabular-nums">{Math.floor(item.progress * 100)} %</span>}
                        </span>
                      )}
                      {item.status === 'failed' && item.error && (
                        <span className="mt-1 block text-xs wrap-anywhere text-danger">{item.error}</span>
                      )}
                    </Td>
                    <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                      {item.bytes !== null && item.status === 'ready' ? formatBytes(item.bytes) : '–'}
                    </Td>
                    <Td className={cx('hidden whitespace-nowrap tabular-nums sm:table-cell', !ready && 'text-muted')}>
                      {item.expires_at && item.status === 'ready' ? formatDate(item.expires_at) : '–'}
                    </Td>
                    <Td tight className="text-right whitespace-nowrap">
                      {ready && (
                        // A plain link: the API answers with a redirect to a 5-minute signed download.
                        <a
                          href={downloadUrl(item.id)}
                          aria-label={t('exports.getFor', { title: item.title })}
                          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-accent px-4 text-sm font-semibold text-on-accent hover:brightness-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:min-h-9"
                        >
                          <DownloadIcon className="size-4" />
                          <span className="hidden sm:inline">{t('exports.get')}</span>
                        </a>
                      )}
                      <Button
                        variant="ghostDanger"
                        size="icon"
                        aria-label={t('exports.deleteFor', { title: item.title })}
                        onClick={() => setDeleting(item)}
                      >
                        <TrashIcon className="size-4" />
                      </Button>
                    </Td>
                  </tr>
                )
              })}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title={t('exports.deleteTitle')}
        confirmLabel={t('exports.deleteConfirm')}
        danger
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting, {
            onSuccess: () => toast.success(t('exports.deleted')),
            onError: () => toast.error(t('exports.deleteFailed')),
            onSettled: () => setDeleting(null),
          })
        }
      >
        {t('exports.deleteBody')}
      </ConfirmDialog>
    </div>
  )
}

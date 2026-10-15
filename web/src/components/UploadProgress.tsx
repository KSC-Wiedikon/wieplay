import type { TFunction } from 'i18next'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useBlocker } from 'react-router'
import { ApiError, blockersOf } from '../lib/api'
import { formatBytes, formatSeconds } from '../lib/format'
import type { UploadSnapshot } from '../lib/upload'
import { Button, ButtonLink } from './Button'
import { Card } from './Card'
import { ConfirmDialog } from './ConfirmDialog'
import { CheckIcon } from './icons'
import { ProgressBar } from './ProgressBar'
import { Table, TBody, Td, Th, THead } from './Table'
import { BlockerList } from './VideoBadges'

function errorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError) return error.detail
  return t('upload.networkError')
}

interface UploadProgressProps {
  snapshot: UploadSnapshot
  onRetry: () => void
  /** Cancel and delete the video. */
  onCancel: () => Promise<void>
  /** The user leaves the page: stop, keep the parts for a later resume. */
  onLeave: () => void
}

/** Overall and per-file progress, speed and time left; asks before leaving the page while uploading. */
export function UploadProgress({ snapshot, onRetry, onCancel, onLeave }: UploadProgressProps) {
  const { t } = useTranslation()
  const id = useId()
  const [confirmCancel, setConfirmCancel] = useState(false)
  const running = snapshot.state === 'running'
  const blocker = useBlocker(running)
  const { loaded, total, bytesPerSecond, secondsLeft } = snapshot
  const share = total > 0 ? loaded / total : 0
  const cancelling = snapshot.state === 'cancelling'
  // The last file's completion re-checks the filming rules; a 409 there means the video is refused.
  const blockers = blockersOf(snapshot.error)
  const refused = blockers !== null
  const stopped = (snapshot.state === 'error' || snapshot.state === 'stopped') && !refused

  const facts = [
    t('upload.overall', { loaded: formatBytes(loaded), total: formatBytes(total) }),
    running && bytesPerSecond ? t('upload.speed', { speed: formatBytes(bytesPerSecond) }) : null,
    running && secondsLeft !== null ? t('upload.eta', { time: formatSeconds(secondsLeft) }) : null,
  ].filter(Boolean)

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('upload.progressTitle')}
      </h2>
      <Card className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
            <span className="text-xl font-bold tabular-nums">{Math.floor(share * 100)} %</span>
            <span className="text-muted tabular-nums">{facts.join(' · ')}</span>
          </div>
          <ProgressBar value={share} label={t('upload.progressTitle')} />
        </div>
        {running && <p className="text-sm text-muted">{t('upload.keepOpen')}</p>}
        {stopped && (
          <p role="alert" className="text-sm text-danger">
            {t('upload.stopped')} {snapshot.error ? errorText(t, snapshot.error) : ''}
          </p>
        )}
        {refused && (
          <div role="alert" className="flex flex-col gap-2 rounded-xl border border-danger/50 p-3">
            <p className="text-sm font-semibold text-danger">{t('upload.refused')}</p>
            <BlockerList blockers={blockers} />
          </div>
        )}
        <div className="-mx-5 border-t border-hairline">
          <Table caption={t('columns.file')}>
            <THead>
              <tr>
                <Th>{t('columns.file')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.size')}</Th>
                <Th className="text-right">{t('columns.status')}</Th>
              </tr>
            </THead>
            <TBody>
              {snapshot.files.map((file) => (
                <tr key={file.id}>
                  <Td>
                    <span className="block font-semibold break-all">{file.name}</span>
                    <ProgressBar
                      value={file.size ? file.loaded / file.size : 0}
                      label={file.name}
                      className="mt-2 h-1.5"
                    />
                  </Td>
                  <Td className="hidden whitespace-nowrap text-muted tabular-nums sm:table-cell">
                    {formatBytes(file.size)}
                  </Td>
                  <Td className="text-right whitespace-nowrap tabular-nums">
                    {file.done ? (
                      <span className="inline-flex items-center gap-1">
                        <CheckIcon className="size-4" />
                        {t('upload.fileDone')}
                      </span>
                    ) : (
                      `${Math.floor((file.size ? file.loaded / file.size : 0) * 100)} %`
                    )}
                  </Td>
                </tr>
              ))}
            </TBody>
          </Table>
        </div>
        <div className="flex flex-wrap gap-2">
          {refused ? (
            <ButtonLink to={`/videos/${snapshot.video.id}`} variant="primary">
              {t('upload.openVideo')}
            </ButtonLink>
          ) : (
            <>
              {stopped && (
                <Button variant="primary" onClick={onRetry}>
                  {t('upload.retry')}
                </Button>
              )}
              <Button disabled={cancelling} onClick={() => setConfirmCancel(true)}>
                {t('video.cancelUpload')}
              </Button>
            </>
          )}
        </div>
      </Card>

      <ConfirmDialog
        open={confirmCancel}
        title={t('video.cancelUploadTitle')}
        confirmLabel={t('video.cancelUpload')}
        cancelLabel={t('video.keepUploading')}
        danger
        busy={cancelling}
        onCancel={() => setConfirmCancel(false)}
        onConfirm={() => {
          void onCancel().finally(() => setConfirmCancel(false))
        }}
      >
        {t('video.cancelUploadBody')}
      </ConfirmDialog>

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title={t('upload.leaveTitle')}
        confirmLabel={t('upload.leave')}
        cancelLabel={t('upload.stay')}
        onCancel={() => blocker.reset?.()}
        onConfirm={() => {
          onLeave()
          blocker.proceed?.()
        }}
      >
        {t('upload.leaveBody')}
      </ConfirmDialog>
    </section>
  )
}

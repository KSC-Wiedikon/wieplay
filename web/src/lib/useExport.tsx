// Queuing a watermarked export (clips, the rallies only, the game recap, reels): the rules
// shown once per device, the toasts. Used by "Download as MP4" and the video page's "Export".

import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ApiError } from './api'
import { useCreateExport } from './library'
import { readStorage, writeStorage } from './storage'
import { useToast } from './toast'
import type { ExportIn } from './types'

/** Set once the rules for downloads were shown and accepted on this device. */
const SEEN_KEY = 'wieplay-export-rules'

interface Pending {
  request: ExportIn
  queuedText?: string
}

/**
 * Queues a watermarked export (coaches, team managers, sport admins). The first time on a
 * device it says what a download is for (the dialog to render). The file is listed under
 * Downloads when ready.
 */
export function useExport(): { start: (request: ExportIn, queuedText?: string) => void; busy: boolean; dialog: ReactNode } {
  const { t } = useTranslation()
  const toast = useToast()
  const create = useCreateExport()
  const [pending, setPending] = useState<Pending | null>(null)

  const queue = ({ request, queuedText }: Pending) =>
    create.mutate(request, {
      onSuccess: () => toast.success(queuedText ?? t('exports.queued')),
      onError: (error) => {
        if (!(error instanceof ApiError)) toast.error(t('exports.failed', { detail: t('common.error') }))
        else if (error.status === 403) toast.error(t('exports.forbidden'))
        else if (error.status === 409) toast.error(t('exports.conflict', { detail: error.detail }))
        else if (error.status === 413) toast.error(t('exports.tooLong'))
        else toast.error(t('exports.failed', { detail: error.detail }))
      },
    })

  const start = (request: ExportIn, queuedText?: string) => {
    if (readStorage(SEEN_KEY) === '1') queue({ request, queuedText })
    else setPending({ request, queuedText })
  }

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      title={t('exports.explainTitle')}
      confirmLabel={t('exports.explainConfirm')}
      onCancel={() => setPending(null)}
      onConfirm={() => {
        writeStorage(SEEN_KEY, '1')
        const next = pending
        setPending(null)
        if (next) queue(next)
      }}
    >
      {t('exports.rules')}
    </ConfirmDialog>
  )
  return { start, busy: create.isPending, dialog }
}

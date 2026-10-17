import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { useCreateExport } from '../lib/library'
import { readStorage, writeStorage } from '../lib/storage'
import { useToast } from '../lib/toast'
import type { ExportIn } from '../lib/types'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { DownloadIcon } from './icons'

/** Set once the rules for downloads were shown and accepted on this device. */
const SEEN_KEY = 'nomal-export-rules'

interface ExportButtonProps {
  request: ExportIn
  /** Accessible name when the button shows the icon only (the text says "Download as MP4" otherwise). */
  label?: string
  iconOnly?: boolean
  className?: string
  /** The button's text instead of "Download as MP4" (e.g. "Create game recap"). */
  text?: string
  /** What the toast says once it is queued. */
  queuedText?: string
}

/**
 * "Download as MP4": queues a watermarked export (coaches, team managers, sport admins). The
 * first time, it says what a download is for. The file is listed under Downloads when ready.
 */
export function ExportButton({ request, label, iconOnly = false, className, text, queuedText }: ExportButtonProps) {
  const { t } = useTranslation()
  const toast = useToast()
  const create = useCreateExport()
  const [explain, setExplain] = useState(false)

  const queue = () =>
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

  const onClick = () => {
    if (readStorage(SEEN_KEY) === '1') queue()
    else setExplain(true)
  }

  return (
    <>
      <Button
        variant={iconOnly ? 'ghost' : 'secondary'}
        size={iconOnly ? 'icon' : 'md'}
        className={className}
        disabled={create.isPending}
        aria-label={iconOnly ? (label ?? t('exports.download')) : undefined}
        title={iconOnly ? (label ?? t('exports.download')) : undefined}
        onClick={onClick}
      >
        {iconOnly ? <DownloadIcon className="size-4" /> : <DownloadIcon />}
        {!iconOnly && (text ?? t('exports.download'))}
      </Button>
      <ConfirmDialog
        open={explain}
        title={t('exports.explainTitle')}
        confirmLabel={t('exports.explainConfirm')}
        onCancel={() => setExplain(false)}
        onConfirm={() => {
          writeStorage(SEEN_KEY, '1')
          setExplain(false)
          queue()
        }}
      >
        {t('exports.rules')}
      </ConfirmDialog>
    </>
  )
}

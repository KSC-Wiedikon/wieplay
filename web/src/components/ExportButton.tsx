import { useTranslation } from 'react-i18next'
import type { ExportIn } from '../lib/types'
import { useExport } from '../lib/useExport'
import { Button } from './Button'
import { DownloadIcon } from './icons'

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

/** "Download as MP4": queues a watermarked export (useExport). */
export function ExportButton({ request, label, iconOnly = false, className, text, queuedText }: ExportButtonProps) {
  const { t } = useTranslation()
  const { start, busy, dialog } = useExport()
  return (
    <>
      <Button
        variant={iconOnly ? 'ghost' : 'secondary'}
        size={iconOnly ? 'icon' : 'md'}
        className={className}
        disabled={busy}
        aria-label={iconOnly ? (label ?? t('exports.download')) : undefined}
        title={iconOnly ? (label ?? t('exports.download')) : undefined}
        onClick={() => start(request, queuedText)}
      >
        {iconOnly ? <DownloadIcon className="size-4" /> : <DownloadIcon />}
        {!iconOnly && (text ?? t('exports.download'))}
      </Button>
      {dialog}
    </>
  )
}

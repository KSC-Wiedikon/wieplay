import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Button } from './Button'

interface ConfirmDialogProps {
  open: boolean
  title: string
  children?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  /** Red confirm button, for deleting and other things that can't be undone. */
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** In-page confirmation (no native confirm()). Focus starts on Cancel; Escape cancels. */
export function ConfirmDialog({ open, ...props }: ConfirmDialogProps) {
  if (!open) return null
  return createPortal(<Dialog {...props} />, document.body)
}

function Dialog({
  title,
  children,
  confirmLabel,
  cancelLabel,
  danger,
  busy,
  onConfirm,
  onCancel,
}: Omit<ConfirmDialogProps, 'open'>) {
  const { t } = useTranslation()
  const id = useId()
  const panel = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    cancel.current?.focus()
    return () => previous?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      if (!busy) onCancel()
      return
    }
    if (event.key !== 'Tab' || !panel.current) return
    // Keep focus inside the dialog.
    const focusable = [...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input, select')]
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (!first || !last) return
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-navy/60 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel()
      }}
    >
      <div
        ref={panel}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={children ? `${id}-body` : undefined}
        onKeyDown={onKeyDown}
        className="flex w-full max-w-sm flex-col gap-4 rounded-card border border-hairline bg-surface p-5 shadow-lg"
      >
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {title}
        </h2>
        {children && (
          <div id={`${id}-body`} className="text-sm text-muted">
            {children}
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button ref={cancel} disabled={busy} onClick={onCancel}>
            {cancelLabel ?? t('common.cancel')}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

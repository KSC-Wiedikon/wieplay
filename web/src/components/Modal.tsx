import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { Button } from './Button'
import { CloseIcon } from './icons'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  size?: 'md' | 'lg' | 'xl'
}

const sizes = { md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-5xl' } as const

/** An in-page dialog with a close button. Escape and a click outside close it; focus stays inside. */
export function Modal({ open, ...props }: ModalProps) {
  if (!open) return null
  return createPortal(<Panel {...props} />, document.body)
}

function Panel({ title, onClose, children, footer, size = 'md' }: Omit<ModalProps, 'open'>) {
  const { t } = useTranslation()
  const id = useId()
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const first = panel.current?.querySelector<HTMLElement>('input, select, textarea, button:not([data-close])')
    ;(first ?? panel.current)?.focus()
    return () => previous?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !panel.current) return
    const focusable = [
      ...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select, textarea'),
    ]
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
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-navy/60 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={cx(
          'flex max-h-[calc(100dvh-2rem)] w-full flex-col rounded-2xl border border-hairline bg-surface shadow-lg',
          sizes[size],
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-hairline py-2 pr-2 pl-5">
          <h2 id={`${id}-title`} className="text-lg font-semibold">
            {title}
          </h2>
          <Button variant="ghost" size="icon" data-close aria-label={t('common.close')} onClick={onClose}>
            <CloseIcon />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-hairline p-4">{footer}</div>}
      </div>
    </div>
  )
}

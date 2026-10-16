import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { ToastContext, type ToastApi, type ToastTone } from '../lib/toast'
import { Button } from './Button'
import { CloseIcon } from './icons'

interface Toast {
  id: number
  tone: ToastTone
  message: string
}

const DURATION_MS = 6000

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id))
    timers.current.delete(id)
    setToasts((list) => list.filter((toast) => toast.id !== id))
  }, [])

  const show = useCallback(
    (tone: ToastTone, message: string) => {
      const id = nextId.current++
      setToasts((list) => [...list, { id, tone, message }])
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), DURATION_MS),
      )
    },
    [dismiss],
  )

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((timer) => window.clearTimeout(timer))
  }, [])

  const api = useMemo<ToastApi>(
    () => ({ success: (message) => show('success', message), error: (message) => show('error', message) }),
    [show],
  )

  return (
    <ToastContext value={api}>
      {children}
      <section
        aria-label={t('common.notifications')}
        className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4 sm:items-end"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === 'error' ? 'alert' : 'status'}
            className={cx(
              'pointer-events-auto flex w-full max-w-sm items-center gap-2 rounded-card border bg-surface py-1 pr-1 pl-4 text-sm shadow-lg',
              toast.tone === 'error' ? 'border-danger' : 'border-hairline',
            )}
          >
            <p className={cx('flex-1 py-2', toast.tone === 'error' && 'text-danger')}>{toast.message}</p>
            <Button variant="ghost" size="icon" aria-label={t('common.dismiss')} onClick={() => dismiss(toast.id)}>
              <CloseIcon />
            </Button>
          </div>
        ))}
      </section>
    </ToastContext>
  )
}

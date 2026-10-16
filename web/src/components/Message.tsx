import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { Button } from './Button'
import { Card } from './Card'
import { AlertIcon } from './icons'

/** A short full-width notice: 403/404, load errors and simple empty states. */
export function Message({ children, action, icon }: { children: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <Card className="flex flex-wrap items-center gap-x-4 gap-y-3 p-5">
      {icon && <span className="grid size-10 shrink-0 place-items-center rounded-full bg-sunken text-muted">{icon}</span>}
      <p className="min-w-0 flex-1 basis-48 text-sm text-muted">{children}</p>
      {action}
    </Card>
  )
}

/** An empty list or page: an icon, what is missing and what to do about it. */
export function EmptyState({
  icon,
  title,
  children,
  action,
  className,
}: {
  icon: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cx(
        'flex flex-col items-center gap-3 rounded-card border border-dashed border-hairline-strong px-6 py-10 text-center',
        className,
      )}
    >
      <span className="grid size-12 place-items-center rounded-full bg-accent-soft text-on-accent-soft">{icon}</span>
      <div className="flex max-w-md flex-col gap-1">
        <p className="font-semibold">{title}</p>
        {children && <p className="text-sm text-muted">{children}</p>}
      </div>
      {action && <div className="mt-1 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

export function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <Message icon={<AlertIcon />} action={<Button onClick={onRetry}>{t('common.retry')}</Button>}>
      <span role="alert">{t('common.error')}</span>
    </Message>
  )
}

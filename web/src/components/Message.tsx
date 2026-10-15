import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from './Button'
import { Card } from './Card'

/** A short full-width notice: empty states, 403/404 and load errors. */
export function Message({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <Card className="flex flex-col items-start gap-3 p-6">
      <p className="text-sm text-muted">{children}</p>
      {action}
    </Card>
  )
}

export function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <Message action={<Button onClick={onRetry}>{t('common.retry')}</Button>}>
      <span role="alert">{t('common.error')}</span>
    </Message>
  )
}

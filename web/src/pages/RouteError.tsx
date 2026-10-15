import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouteError } from 'react-router'
import { Button } from '../components/Button'
import { Message } from '../components/Message'

/** Last-resort boundary for render errors; data errors are handled on each page. */
export function RouteError() {
  const { t } = useTranslation()
  const error = useRouteError()
  useEffect(() => console.error(error), [error])

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <Message action={<Button onClick={() => window.location.reload()}>{t('common.retry')}</Button>}>
        <span role="alert">{t('common.error')}</span>
      </Message>
    </main>
  )
}

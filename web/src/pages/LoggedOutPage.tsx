import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router'
import { Mark, Wordmark } from '../components/Brand'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { loginUrl } from '../lib/api'

export function LoggedOutPage() {
  const { t } = useTranslation()
  const { pathname, search } = useLocation()

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-8">
      <title>nomal</title>
      <Card className="flex w-full max-w-sm flex-col items-center gap-6 p-8 text-center">
        <div className="flex flex-col items-center gap-3">
          <Mark className="size-24" />
          <h1>
            <Wordmark className="text-5xl" />
          </h1>
        </div>
        <p className="text-muted">{t('loggedOut.tagline')}</p>
        <Button
          variant="primary"
          className="w-full"
          onClick={() => window.location.assign(loginUrl(pathname + search))}
        >
          {t('loggedOut.login')}
        </Button>
      </Card>
    </main>
  )
}

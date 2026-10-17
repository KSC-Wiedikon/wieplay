import { useTranslation } from 'react-i18next'
import { Outlet } from 'react-router'
import { Header } from '../components/Header'
import { LoadError } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { useExportNotifications } from '../lib/exportWatch'
import { useMe } from '../lib/queries'
import type { Session } from '../lib/session'
import { LoginPage } from './LoginPage'

export function AppLayout() {
  const { t } = useTranslation()
  const me = useMe()

  if (me.isPending) return <PageSpinner />
  if (me.isError) {
    return (
      <main className="mx-auto max-w-md px-4 py-16">
        <LoadError onRetry={() => void me.refetch()} />
      </main>
    )
  }
  if (!me.data) return <LoginPage />

  return (
    <>
      <a
        href="#main"
        className="sr-only rounded-control bg-accent px-4 py-2 text-on-accent focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
      >
        {t('common.skipToContent')}
      </a>
      <Header me={me.data} />
      <ExportNotifications />
      {/* Every page uses the full window up to 110rem; forms and dialogs set their own widths. */}
      <main id="main" className="mx-auto w-full max-w-[110rem] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <Outlet context={{ me: me.data } satisfies Session} />
      </main>
    </>
  )
}

/** Toasts when a download asked for in this tab is ready, on any page. */
function ExportNotifications() {
  useExportNotifications()
  return null
}

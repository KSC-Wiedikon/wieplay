import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router'
import { Mark, Wordmark } from '../components/Brand'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Field, TextInput } from '../components/Field'
import { Spinner } from '../components/Spinner'
import { ApiError, guestLogin, loginUrl } from '../lib/api'
import { browser, goesStraightToWiedisync, rememberLogin } from '../lib/guests'
import { keys } from '../lib/queryClient'

/**
 * Logged out: KSCW members log in through Wiedisync (one click, or none when that was their
 * choice last time), guest clubs with the email and password from their invite link (PLAN §4.5).
 * Shown in place of whatever page was asked for, which then opens once logged in.
 */
export function LoginPage() {
  const { t } = useTranslation()
  const { pathname, search } = useLocation()
  const next = pathname + search
  // Read once: the page must not jump away while someone is typing a password.
  const [straight] = useState(goesStraightToWiedisync)

  useEffect(() => {
    if (straight) browser.assign(loginUrl(next))
  }, [straight, next])

  if (straight) {
    return (
      <main className="grid min-h-dvh place-items-center">
        <Spinner label={t('guests.login.redirecting')} />
      </main>
    )
  }

  const wiedisync = () => {
    rememberLogin('wiedisync')
    browser.assign(loginUrl(next))
  }

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-8">
      <title>WiePlay</title>
      <Card className="flex w-full max-w-sm flex-col gap-6 p-6 sm:p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <Mark className="size-20" />
          <h1>
            <Wordmark className="text-5xl" />
          </h1>
          <p className="text-muted">{t('loggedOut.tagline')}</p>
        </div>
        <section aria-label={t('guests.login.kscwTitle')} className="flex flex-col">
          <Button variant="primary" className="w-full" onClick={wiedisync}>
            {t('loggedOut.login')}
          </Button>
        </section>
        <div className="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
          <span className="h-px flex-1 bg-hairline" />
          {t('guests.login.or')}
          <span className="h-px flex-1 bg-hairline" />
        </div>
        <GuestLoginForm />
        <Link to="/privacy" className="link self-center text-sm">
          {t('guests.privacy.title')}
        </Link>
      </Card>
    </main>
  )
}

function GuestLoginForm() {
  const { t } = useTranslation()
  const id = useId()
  const client = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || !email.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      await guestLogin(email.trim(), password)
      rememberLogin('guest')
      // The session cookie is set: the app asks who is logged in again and opens the page.
      await client.invalidateQueries({ queryKey: keys.me })
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 401 ? t('guests.login.wrong') : t('guests.login.failed'))
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-title`} className="font-semibold">
          {t('guests.login.title')}
        </h2>
        <p className="text-sm text-muted">{t('guests.login.hint')}</p>
      </div>
      <form className="flex flex-col gap-3" onSubmit={(e) => void onSubmit(e)}>
        <Field label={t('guests.login.email')} htmlFor={`${id}-email`}>
          <TextInput
            id={`${id}-email`}
            type="email"
            autoComplete="username"
            required
            maxLength={320}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label={t('guests.login.password')} htmlFor={`${id}-password`}>
          <TextInput
            id={`${id}-password`}
            type="password"
            autoComplete="current-password"
            required
            maxLength={200}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={busy || !email.trim() || !password}>
          {busy ? t('guests.login.submitting') : t('guests.login.submit')}
        </Button>
        <p className="text-xs text-muted">{t('guests.login.forgot')}</p>
      </form>
    </section>
  )
}

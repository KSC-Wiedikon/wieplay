import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Mark, Wordmark } from '../components/Brand'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { Field, TextInput } from '../components/Field'
import { LoadError } from '../components/Message'
import { Spinner } from '../components/Spinner'
import { acceptInvite, ApiError, inviteInfo } from '../lib/api'
import { rememberLogin } from '../lib/guests'
import { keys } from '../lib/queryClient'
import type { GuestInvite } from '../lib/types'

const MIN_PASSWORD = 10

/**
 * An invite link of a guest club (PLAN §4.5): /join/<token>. Sets up the person's login, or a new
 * password when they have one, then opens their team. Needs no session.
 */
export function JoinPage() {
  const { token = '' } = useParams()
  const { t } = useTranslation()
  const invite = useQuery({
    queryKey: keys.invite(token),
    queryFn: ({ signal }) => inviteInfo(token, signal),
    retry: false,
    staleTime: Infinity,
  })

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-8">
      <Card className="flex w-full max-w-md flex-col gap-6 p-6 sm:p-8">
        <div className="flex items-center gap-3">
          <Mark className="size-12" />
          <Wordmark className="text-3xl" />
        </div>
        {invite.isPending ? (
          <Spinner />
        ) : invite.isError ? (
          invite.error instanceof ApiError && (invite.error.status === 404 || invite.error.status === 410) ? (
            <div className="flex flex-col gap-4">
              <title>nomal</title>
              <p role="alert">{invite.error.status === 410 ? t('guests.join.gone') : t('guests.join.unknown')}</p>
              <ButtonLink to="/" className="self-start">
                {t('guests.join.toLogin')}
              </ButtonLink>
            </div>
          ) : (
            <LoadError onRetry={() => void invite.refetch()} />
          )
        ) : (
          <JoinForm token={token} invite={invite.data} />
        )}
      </Card>
    </main>
  )
}

function JoinForm({ token, invite }: { token: string; invite: GuestInvite }) {
  const { t } = useTranslation()
  const id = useId()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [privacy, setPrivacy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reset = invite.has_account
  const role = invite.role === 'coach' ? t('guests.join.roleCoach') : t('guests.join.rolePlayer')

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD
  const mismatch = repeat.length > 0 && repeat !== password
  const ready =
    (reset || email.trim() !== '') && password.length >= MIN_PASSWORD && repeat === password && privacy && !busy

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      const { next } = await acceptInvite(token, {
        email: reset ? null : email.trim(),
        password,
        accept_privacy: true,
      })
      rememberLogin('guest')
      // Someone else may have been logged in in this browser: start the app from scratch.
      client.removeQueries()
      void navigate(next, { replace: true })
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 410) setError(t('guests.join.gone'))
      else setError(t('guests.join.failed', { detail: caught instanceof ApiError ? caught.detail : t('common.error') }))
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <title>{`${t('guests.join.title', { team: invite.team })} · nomal`}</title>
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{t('guests.join.title', { team: invite.team })}</h1>
        <p className="text-muted">{t('guests.join.intro', { club: invite.club })}</p>
        <p className="rounded-control border border-hairline bg-sunken px-3 py-2 text-sm font-semibold">
          {t('guests.join.who', { name: invite.name, role, team: invite.team, club: invite.club, season: invite.season })}
        </p>
        {reset && <p className="text-sm">{t('guests.join.reset')}</p>}
      </div>
      <form className="flex flex-col gap-4" onSubmit={(e) => void onSubmit(e)}>
        {!reset && (
          <Field label={t('guests.join.email')} hint={t('guests.join.emailHint')} htmlFor={`${id}-email`}>
            <TextInput
              id={`${id}-email`}
              type="email"
              autoComplete="email"
              required
              maxLength={320}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
        )}
        <Field
          label={t('guests.join.password')}
          hint={t('guests.join.passwordHint')}
          error={tooShort ? t('guests.join.tooShort') : null}
          htmlFor={`${id}-password`}
        >
          <TextInput
            id={`${id}-password`}
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            maxLength={200}
            value={password}
            aria-invalid={tooShort || undefined}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field
          label={t('guests.join.repeat')}
          error={mismatch ? t('guests.join.mismatch') : null}
          htmlFor={`${id}-repeat`}
        >
          <TextInput
            id={`${id}-repeat`}
            type="password"
            autoComplete="new-password"
            required
            maxLength={200}
            value={repeat}
            aria-invalid={mismatch || undefined}
            onChange={(e) => setRepeat(e.target.value)}
          />
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-5 shrink-0 accent-accent"
            checked={privacy}
            onChange={(e) => setPrivacy(e.target.checked)}
          />
          <span>
            {t('guests.join.privacyBefore')}
            <Link to="/privacy" target="_blank" rel="noopener" className="link font-semibold">
              {t('guests.join.privacyLink')}
            </Link>
            {t('guests.join.privacyAfter')}
          </span>
        </label>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" disabled={!ready}>
          {busy ? t('guests.join.submitting') : reset ? t('guests.join.submitReset') : t('guests.join.submit')}
        </Button>
      </form>
    </div>
  )
}

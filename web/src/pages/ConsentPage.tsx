import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { LoadError } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { formatDate } from '../lib/format'
import { useMyConsent, useSetMyConsent } from '../lib/queries'
import type { Consent } from '../lib/types'
import { consentOutdated } from '../lib/video'

// The information notice (DRAFT, PLAN §5.4), in reading order.
const NOTICE = ['what', 'who', 'howLong', 'where', 'minors', 'withdraw', 'contact'] as const

export function ConsentPage() {
  const { t } = useTranslation()
  const id = useId()
  const consent = useMyConsent()

  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <title>{`${t('consent.title')} · nomal`}</title>
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-bold">{t('consent.title')}</h1>
          <Badge tone="accent">{t('common.draft')}</Badge>
        </div>
        <p className="text-sm text-muted">{t('consent.draftNote')}</p>
      </div>

      <Card className="flex flex-col gap-5 p-5 sm:p-6">
        <p>{t('consent.notice.intro')}</p>
        {NOTICE.map((key) => (
          <section key={key} aria-labelledby={`${id}-${key}`} className="flex flex-col gap-1">
            <h2 id={`${id}-${key}`} className="font-semibold">
              {t(`consent.notice.${key}Title`)}
            </h2>
            <p className="text-sm">{t(`consent.notice.${key}`)}</p>
          </section>
        ))}
        {consent.data && (
          <p className="text-xs text-muted">{t('consent.version', { version: consent.data.notice_version })}</p>
        )}
      </Card>

      <section aria-labelledby={`${id}-answer`} className="flex flex-col gap-3">
        <h2 id={`${id}-answer`} className="text-lg font-semibold">
          {t('consent.statusTitle')}
        </h2>
        {consent.isPending ? (
          <PageSpinner />
        ) : consent.isError ? (
          <LoadError onRetry={() => void consent.refetch()} />
        ) : (
          <Answer consent={consent.data} />
        )}
      </section>
    </div>
  )
}

function Answer({ consent }: { consent: Consent }) {
  const { t } = useTranslation()
  const save = useSetMyConsent()
  const [confirm, setConfirm] = useState(false)
  const date = formatDate(consent.at)
  const outdated = consentOutdated(consent, consent.notice_version)
  const status =
    outdated
      ? t('consent.acceptedOlder', { date })
      : consent.status === 'accepted'
      ? consent.via === 'paper'
        ? t('consent.acceptedPaper', { date })
        : t('consent.accepted', { date })
      : consent.status === 'withdrawn'
        ? t('consent.withdrawn', { date })
        : t('consent.none')

  return (
    <Card className="flex flex-col gap-4 p-5">
      <p role="status" className="font-semibold">
        {status}
      </p>
      <div className="flex flex-wrap gap-2">
        {(consent.status !== 'accepted' || outdated) && (
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate('accepted')}>
            {t('consent.agree')}
          </Button>
        )}
        {consent.status === 'accepted' && (
          <Button disabled={save.isPending} onClick={() => setConfirm(true)}>
            {t('consent.withdrawAction')}
          </Button>
        )}
        {consent.status === null && (
          <Button disabled={save.isPending} onClick={() => save.mutate('withdrawn')}>
            {t('consent.decline')}
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        title={t('consent.withdrawTitle')}
        confirmLabel={t('consent.withdrawAction')}
        danger
        busy={save.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() => save.mutate('withdrawn', { onSettled: () => setConfirm(false) })}
      >
        {t('consent.withdrawBody')}
      </ConfirmDialog>
    </Card>
  )
}

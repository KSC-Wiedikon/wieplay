import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { AdminShell } from '../components/AdminShell'
import { SportBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Field, TextInput } from '../components/Field'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { ForbiddenError } from '../lib/api'
import { formatDateTime } from '../lib/format'
import { usePolicies, useSetPolicy } from '../lib/queries'
import { SPORTS } from '../lib/teams'
import type { Policy } from '../lib/types'

const collator = new Intl.Collator('de-CH', { numeric: true, sensitivity: 'base' })

export function AdminPoliciesPage() {
  const { t } = useTranslation()
  return (
    <AdminShell title={t('admin.policies.title')}>
      <Policies />
    </AdminShell>
  )
}

function Policies() {
  const { t } = useTranslation()
  const id = useId()
  const policies = usePolicies()
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-lg font-semibold">
          {t('admin.policies.title')}
        </h2>
        <p className="text-sm text-muted">{t('admin.policies.hint')}</p>
      </div>
      {policies.isPending ? (
        <PageSpinner />
      ) : policies.isError ? (
        policies.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void policies.refetch()} />
        )
      ) : policies.data.length === 0 ? (
        <Message>{t('admin.policies.empty')}</Message>
      ) : (
        SPORTS.map((sport) => {
          const list = policies.data.filter((p) => p.sport === sport).sort((a, b) => collator.compare(a.label, b.label))
          if (list.length === 0) return null
          return (
            <div key={sport} className="flex flex-col gap-2">
              <h3 className="font-semibold">{t(`sport.${sport}`)}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {list.map((policy) => (
                  // A save changes set_at, which resets the form to the stored values.
                  <PolicyCard key={`${policy.lineage_id}-${policy.set_at ?? ''}`} policy={policy} />
                ))}
              </div>
            </div>
          )
        })
      )}
    </section>
  )
}

function PolicyCard({ policy }: { policy: Policy }) {
  const { t } = useTranslation()
  const id = useId()
  const save = useSetPolicy()
  const [matches, setMatches] = useState(policy.matches_allowed)
  const [reason, setReason] = useState(policy.reason ?? '')
  const dirty = matches !== policy.matches_allowed || reason.trim() !== (policy.reason ?? '')

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate({ policy, update: { matches_allowed: matches, reason: reason.trim() || null } })
  }

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-3 p-5">
        <h4 className="flex items-start gap-2 font-semibold">
          <SportBadge sport={policy.sport} />
          {policy.label}
        </h4>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm">{t('admin.policies.matches')}</span>
          <Switch
            checked={matches}
            label={`${policy.label}: ${t('admin.policies.matches')}`}
            onChange={setMatches}
          />
        </div>
        <Field label={t('admin.policies.reason')} hint={t('admin.policies.reasonHint')} htmlFor={`${id}-reason`}>
          <TextInput
            id={`${id}-reason`}
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-muted tabular-nums">
            {policy.set_at ? t('admin.policies.lastChanged', { date: formatDateTime(policy.set_at) }) : ''}
          </span>
          <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
            {save.isPending ? t('common.saving') : t('common.save')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

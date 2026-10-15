import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { AdminShell } from '../components/AdminShell'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Select } from '../components/Field'
import { LoadError, Message } from '../components/Message'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ForbiddenError } from '../lib/api'
import { formatDateTime } from '../lib/format'
import { useJobs, useRetryJob } from '../lib/queries'
import type { Job, JobStatus } from '../lib/types'

const STATUSES: readonly JobStatus[] = ['queued', 'running', 'succeeded', 'failed', 'cancelled']

const tones = {
  queued: 'neutral',
  running: 'accent',
  succeeded: 'neutral',
  failed: 'danger',
  cancelled: 'neutral',
} as const

function JobStatusBadge({ job }: { job: Job }) {
  const { t } = useTranslation()
  const known = (STATUSES as readonly string[]).includes(job.status)
  const tone = known ? tones[job.status as JobStatus] : 'neutral'
  return (
    <Badge tone={tone} className="whitespace-nowrap">
      {known ? t(`admin.jobs.status.${job.status as JobStatus}`) : job.status}
      {job.status === 'running' && job.progress !== null ? ` ${Math.floor(job.progress * 100)} %` : ''}
    </Badge>
  )
}

export function AdminJobsPage() {
  const { t } = useTranslation()
  return (
    <AdminShell title={t('admin.jobs.title')}>
      <Jobs />
    </AdminShell>
  )
}

function Jobs() {
  const { t } = useTranslation()
  const id = useId()
  const [status, setStatus] = useState<string>('')
  const jobs = useJobs(status || null)
  const retry = useRetryJob()

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id={id} className="flex items-center gap-2 text-lg font-semibold">
            {t('admin.jobs.title')}
            {jobs.isFetching && !jobs.isPending && <Spinner className="[&_svg]:size-4" />}
          </h2>
          <p className="text-sm text-muted">{t('admin.jobs.hint')}</p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor={`${id}-status`} className="text-sm font-semibold">
            {t('admin.jobs.filter')}
          </label>
          <Select id={`${id}-status`} inline value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{t('admin.jobs.all')}</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {t(`admin.jobs.status.${s}`)}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {jobs.isPending ? (
        <PageSpinner />
      ) : jobs.isError ? (
        jobs.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void jobs.refetch()} />
        )
      ) : jobs.data.length === 0 ? (
        <Message>{t('admin.jobs.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('admin.jobs.title')}>
            <THead>
              <tr>
                <Th>{t('admin.jobs.kind')}</Th>
                <Th>{t('columns.status')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.jobs.attempts')}</Th>
                <Th className="hidden md:table-cell">{t('admin.jobs.worker')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.jobs.created')}</Th>
                <Th className="hidden lg:table-cell">{t('admin.jobs.finished')}</Th>
              </tr>
            </THead>
            <TBody>
              {jobs.data.map((job) => (
                <tr key={job.id}>
                  <Td>
                    <span className="block font-mono font-semibold">{job.kind}</span>
                    {job.video_id && (
                      <Link
                        to={`/videos/${job.video_id}`}
                        className="block text-link underline-offset-2 hover:underline"
                      >
                        {job.video_title || job.video_id}
                      </Link>
                    )}
                    {job.message && <span className="block text-xs text-muted">{job.message}</span>}
                    <span className="block text-xs text-muted tabular-nums sm:hidden">
                      {formatDateTime(job.created_at)}
                    </span>
                    {job.error && (
                      <details className="mt-1 text-xs">
                        <summary className="cursor-pointer font-semibold text-danger">
                          {t('admin.jobs.showError')}
                        </summary>
                        <pre className="mt-1 font-mono wrap-anywhere whitespace-pre-wrap">{job.error}</pre>
                      </details>
                    )}
                  </Td>
                  <Td>
                    <span className="flex flex-col items-start gap-1">
                      <JobStatusBadge job={job} />
                      {job.status === 'failed' && (
                        <Button
                          className="px-3"
                          aria-label={t('admin.jobs.retryFor', { kind: job.kind })}
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(job)}
                        >
                          {t('admin.jobs.retry')}
                        </Button>
                      )}
                    </span>
                  </Td>
                  <Td className="hidden tabular-nums sm:table-cell">
                    {job.attempts}/{job.max_attempts}
                  </Td>
                  <Td className="hidden font-mono text-xs md:table-cell">{job.worker || '–'}</Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                    {formatDateTime(job.created_at)}
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums lg:table-cell">
                    {formatDateTime(job.finished_at) || '–'}
                  </Td>
                </tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
    </section>
  )
}

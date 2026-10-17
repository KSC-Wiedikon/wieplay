import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { AdminShell } from '../components/AdminShell'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Field, TextInput } from '../components/Field'
import { AlertIcon, CopyIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError, ForbiddenError } from '../lib/api'
import { formatDateTime } from '../lib/format'
import { useCreateWorker, useRevokeWorker, useSetWorkerPaused, useWorkers } from '../lib/queries'
import { useToast } from '../lib/toast'
import type { Worker, WorkerCreated } from '../lib/types'

/** What a worker can be given (backend/wieplay/worker). */
const KNOWN_CAPS = [
  'ingest',
  'import',
  'analyse',
  'inbox',
  'transcode',
  'thumbs',
  'audio',
  'cpu_analysis',
  'analysis_copy',
  'export',
  'retention',
  'nas',
  'cuda',
  'ml',
  'gpu_dispatch',
] as const

const NAME = /^[a-z0-9][a-z0-9-]*$/

export function AdminWorkersPage() {
  const { t } = useTranslation()
  return (
    <AdminShell title={t('admin.workers.title')}>
      <div className="flex flex-col gap-8">
        <WorkerList />
        <CreateWorker />
      </div>
    </AdminShell>
  )
}

function WorkerState({ worker }: { worker: Worker }) {
  const { t } = useTranslation()
  if (worker.revoked) return <Badge tone="danger">{t('admin.workers.revokedState')}</Badge>
  if (worker.paused) return <Badge>{t('admin.workers.paused')}</Badge>
  return <Badge tone="accent">{t('admin.workers.active')}</Badge>
}

function Caps({ caps }: { caps: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {caps.map((cap) => (
        <Badge key={cap} className="font-mono font-normal">
          {cap}
        </Badge>
      ))}
    </span>
  )
}

function WorkerList() {
  const { t } = useTranslation()
  const id = useId()
  const workers = useWorkers()
  const setPaused = useSetWorkerPaused()
  const revoke = useRevokeWorker()
  const [revoking, setRevoking] = useState<Worker | null>(null)

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-lg font-semibold">
          {t('admin.workers.title')}
        </h2>
        <p className="text-sm text-muted">{t('admin.workers.hint')}</p>
      </div>
      {workers.isPending ? (
        <PageSpinner />
      ) : workers.isError ? (
        workers.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void workers.refetch()} />
        )
      ) : workers.data.length === 0 ? (
        <Message>{t('admin.workers.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('admin.workers.title')}>
            <THead>
              <tr>
                <Th>{t('admin.workers.name')}</Th>
                <Th className="hidden md:table-cell">{t('admin.workers.caps')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.workers.lastSeen')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.workers.version')}</Th>
                <Th>{t('admin.workers.state')}</Th>
                <Th>
                  <span className="sr-only">{t('columns.status')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {workers.data.map((worker) => (
                <tr key={worker.id}>
                  <Td>
                    <span className="block font-mono font-semibold">{worker.name}</span>
                    <span className="mt-1 block md:hidden">
                      <Caps caps={worker.caps} />
                    </span>
                    <span className="mt-1 block text-xs text-muted tabular-nums sm:hidden">
                      {t('admin.workers.lastSeen')}: {formatDateTime(worker.last_seen_at) || t('admin.workers.never')}
                    </span>
                  </Td>
                  <Td className="hidden md:table-cell">
                    <Caps caps={worker.caps} />
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                    {formatDateTime(worker.last_seen_at) || t('admin.workers.never')}
                  </Td>
                  <Td className="hidden font-mono text-xs sm:table-cell">{worker.version || '–'}</Td>
                  <Td>
                    <WorkerState worker={worker} />
                  </Td>
                  <Td tight className="text-right">
                    {!worker.revoked && (
                      <span className="inline-flex flex-col gap-1 sm:flex-row">
                        <Button
                          className="px-3"
                          aria-label={
                            worker.paused
                              ? t('admin.workers.resumeFor', { name: worker.name })
                              : t('admin.workers.pauseFor', { name: worker.name })
                          }
                          disabled={setPaused.isPending}
                          onClick={() => setPaused.mutate({ worker, paused: !worker.paused })}
                        >
                          {worker.paused ? t('admin.workers.resume') : t('admin.workers.pause')}
                        </Button>
                        <Button
                          variant="ghostDanger"
                          className="px-3"
                          aria-label={t('admin.workers.revokeFor', { name: worker.name })}
                          onClick={() => setRevoking(worker)}
                        >
                          {t('admin.workers.revoke')}
                        </Button>
                      </span>
                    )}
                  </Td>
                </tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={revoking !== null}
        title={t('admin.workers.revokeTitle', { name: revoking?.name ?? '' })}
        confirmLabel={t('admin.workers.revoke')}
        danger
        busy={revoke.isPending}
        onCancel={() => setRevoking(null)}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking, { onSettled: () => setRevoking(null) })
        }}
      >
        {t('admin.workers.revokeBody')}
      </ConfirmDialog>
    </section>
  )
}

function CreateWorker() {
  const { t } = useTranslation()
  const id = useId()
  const create = useCreateWorker()
  const [name, setName] = useState('')
  const [caps, setCaps] = useState<ReadonlySet<string>>(new Set())
  const [tried, setTried] = useState(false)
  const [created, setCreated] = useState<WorkerCreated | null>(null)
  const nameError = tried && !NAME.test(name) ? t('admin.workers.nameInvalid') : null
  const capsError = tried && caps.size === 0 ? t('admin.workers.capsRequired') : null

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setTried(true)
    if (!NAME.test(name) || caps.size === 0) return
    create.mutate(
      { name, caps: KNOWN_CAPS.filter((cap) => caps.has(cap)) },
      {
        onSuccess: (result) => {
          setCreated(result)
          setName('')
          setCaps(new Set())
          setTried(false)
        },
      },
    )
  }

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {t('admin.workers.create')}
      </h2>
      {created ? (
        <NewToken created={created} onDone={() => setCreated(null)} />
      ) : (
        <Card>
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4 p-5">
            <Field
              label={t('admin.workers.name')}
              hint={t('admin.workers.nameHint')}
              error={nameError}
              htmlFor={`${id}-name`}
              className="sm:max-w-sm"
            >
              <TextInput
                id={`${id}-name`}
                value={name}
                maxLength={64}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                aria-invalid={nameError ? true : undefined}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-sm font-semibold">{t('admin.workers.capsLabel')}</legend>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                {KNOWN_CAPS.map((cap) => (
                  <label key={cap} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm sm:min-h-9">
                    <input
                      type="checkbox"
                      className="size-5 accent-accent"
                      checked={caps.has(cap)}
                      onChange={() =>
                        setCaps((current) => {
                          const next = new Set(current)
                          if (next.has(cap)) next.delete(cap)
                          else next.add(cap)
                          return next
                        })
                      }
                    />
                    <span className="font-mono">{cap}</span>
                  </label>
                ))}
              </div>
              {capsError && (
                <p role="alert" className="text-xs text-danger">
                  {capsError}
                </p>
              )}
            </fieldset>
            {create.isError && (
              <p role="alert" className="text-sm text-danger">
                {t('admin.workers.createFailed', {
                  detail: create.error instanceof ApiError ? create.error.detail : t('common.error'),
                })}
              </p>
            )}
            <Button type="submit" variant="primary" className="self-start" disabled={create.isPending}>
              {create.isPending ? t('admin.workers.creating') : t('admin.workers.createAction')}
            </Button>
          </form>
        </Card>
      )}
    </section>
  )
}

function NewToken({ created, onDone }: { created: WorkerCreated; onDone: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.token)
      toast.success(t('admin.workers.copied'))
    } catch {
      toast.error(t('admin.workers.copyFailed'))
    }
  }
  return (
    <Card className="flex flex-col gap-4 p-5">
      <h3 className="font-semibold">{t('admin.workers.tokenTitle', { name: created.worker.name })}</h3>
      <p role="alert" className="flex items-start gap-2 text-sm font-semibold text-danger">
        <AlertIcon className="mt-0.5 size-4 shrink-0" />
        {t('admin.workers.tokenWarning')}
      </p>
      <code className="block rounded-xl bg-sunken p-3 font-mono text-sm break-all select-all">{created.token}</code>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => void copy()}>
          <CopyIcon />
          {t('admin.workers.copy')}
        </Button>
        <Button onClick={onDone}>{t('admin.workers.tokenDone')}</Button>
      </div>
    </Card>
  )
}

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge, SportBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Field, Select, TextInput } from '../components/Field'
import { CheckIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { UploadProgress } from '../components/UploadProgress'
import { BlockerList } from '../components/VideoBadges'
import { api, ApiError, blockersOf, ForbiddenError, NotFoundError } from '../lib/api'
import { cx } from '../lib/cx'
import { addDaysIso, formatBytes, formatDate, formatDateTime, formatDuration, todayIso } from '../lib/format'
import { opponentOf } from '../lib/games'
import { useFilming, useInbox, useTeam, useTeamGames, useTeamTrainings, useVideoConfig } from '../lib/queries'
import { keys } from '../lib/queryClient'
import { sportOf, useSportAccent } from '../lib/sport'
import { useToast } from '../lib/toast'
import type { InboxFile, TeamDetail, Video, VideoConfig, VideoCreate, VideoKind } from '../lib/types'
import { matchFiles, sortByName, useUpload, type FilePair } from '../lib/upload'
import { inboxFolder, inTeamFolder, isVideoFileName, VIDEO_ACCEPT, VIDEO_EXTENSIONS } from '../lib/video'
import { NotFoundPage } from './NotFoundPage'

/** How far back a game or training can be picked. */
const RECENT_DAYS = 60
/** The filming check answers for days up to 400 days back. */
const OLDEST_DAYS = 400
const OTHER_DAY = 'other'

type Source = 'device' | 'inbox'

export function UploadPage() {
  const { id } = useParams()
  if (!id || !/^\d+$/.test(id)) return <NotFoundPage />
  return <UploadView teamId={Number(id)} />
}

function UploadView({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const team = useTeam(teamId)
  const config = useVideoConfig()
  useSportAccent(team.data ? sportOf(team.data.sport) : 'vb')

  if (team.isPending || config.isPending) return <PageSpinner />
  if (team.isError) {
    if (team.error instanceof NotFoundError) return <Message>{t('team.notFound')}</Message>
    if (team.error instanceof ForbiddenError) return <Message>{t('team.forbidden')}</Message>
    return <LoadError onRetry={() => void team.refetch()} />
  }
  if (config.isError) return <LoadError onRetry={() => void config.refetch()} />

  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('upload.title')} · ${team.data.name} · nomal`}</title>
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold">{t('upload.title')}</h1>
        <p className="flex items-center gap-2 text-muted">
          <SportBadge sport={team.data.sport} />
          <Link to={`/teams/${teamId}`} className="font-semibold text-link underline-offset-2 hover:underline">
            {team.data.name}
          </Link>
          <span>{team.data.season}</span>
        </p>
      </div>
      {!config.data.enabled ? (
        <Message>{t('video.notConfigured')}</Message>
      ) : !team.data.can_upload ? (
        <Message>{t('upload.forbidden')}</Message>
      ) : (
        <UploadFlow team={team.data} config={config.data} />
      )}
    </div>
  )
}

interface Choice {
  value: string
  label: string
  date: string
  defaultTitle: string
}

function UploadFlow({ team, config }: { team: TeamDetail; config: VideoConfig }) {
  const { t } = useTranslation()
  const id = useId()
  const navigate = useNavigate()
  const client = useQueryClient()
  const toast = useToast()
  const upload = useUpload()
  const today = todayIso()
  const from = addDaysIso(today, -RECENT_DAYS)
  const oldest = addDaysIso(today, -OLDEST_DAYS)

  const [kind, setKind] = useState<VideoKind>('match')
  const [event, setEvent] = useState('')
  const [otherDate, setOtherDate] = useState(today)
  const [source, setSource] = useState<Source>('device')
  const [files, setFiles] = useState<File[]>([])
  const [inboxIds, setInboxIds] = useState<ReadonlySet<string>>(new Set())
  const [title, setTitle] = useState('')
  const [pairs, setPairs] = useState<FilePair[]>([])

  const games = useTeamGames(team.id)
  const trainings = useTeamTrainings(team.id)
  const inbox = useInbox(true)

  const choices = useMemo<Choice[]>(() => {
    if (kind === 'match') {
      return (games.data ?? [])
        .filter((g) => g.date && g.date >= from && g.date <= today)
        .sort((a, b) => b.date.localeCompare(a.date))
        .map((g) => ({
          value: String(g.id),
          label: `${formatDate(g.date)} · ${opponentOf(g)}`,
          date: g.date,
          defaultTitle: `${g.home_name} – ${g.away_name}`,
        }))
    }
    return (trainings.data ?? [])
      .filter((tr) => tr.date && tr.date >= from && tr.date <= today)
      .sort((a, b) => b.date.localeCompare(a.date))
      .map((tr) => ({
        value: String(tr.id),
        label: [formatDate(tr.date), tr.hall_name].filter(Boolean).join(' · '),
        date: tr.date,
        defaultTitle: `${t('video.kind.training')} ${formatDate(tr.date)}`,
      }))
  }, [kind, games.data, trainings.data, from, today, t])

  const choice = choices.find((c) => c.value === event)
  const otherDateOk = /^\d{4}-\d{2}-\d{2}$/.test(otherDate) && otherDate >= oldest && otherDate <= today
  const on =
    kind === 'training' && event === OTHER_DAY ? (otherDateOk ? otherDate : null) : (choice?.date ?? null)
  const defaultTitle =
    choice?.defaultTitle ?? (on ? `${t('video.kind.training')} ${formatDate(on)}` : '')
  const filming = useFilming(team.id, kind, on)

  // The inbox has one folder per team; only this team's files can go into its videos.
  const folder = inboxFolder(team)
  const inboxFiles = (inbox.data ?? []).filter((f) => inTeamFolder(f.path, folder))
  const inboxAvailable = inbox.isSuccess
  const chosenSource = inboxAvailable ? source : 'device'
  const sortedFiles = useMemo(() => sortByName(files), [files])
  const pickedInbox = sortByName(
    inboxFiles.filter((f) => inboxIds.has(f.id)).map((f) => ({ ...f, name: f.path })),
  )

  const fileError = (() => {
    if (files.length > config.max_files) return t('upload.tooMany', { max: config.max_files })
    const wrongType = files.find((f) => !isVideoFileName(f.name))
    if (wrongType) return t('upload.badType', { name: wrongType.name, types: VIDEO_EXTENSIONS.join(', ') })
    const big = files.find((f) => f.size > config.max_file_bytes)
    return big ? t('upload.tooLarge', { name: big.name, max: formatBytes(config.max_file_bytes) }) : null
  })()

  const create = useMutation({ mutationFn: (body: VideoCreate) => api.createVideo(team.id, body) })
  const blockers = blockersOf(create.error)

  const state = upload.snapshot?.state
  const doneVideo = state === 'done' ? upload.snapshot?.video : null
  const refused = blockersOf(upload.snapshot?.error) !== null
  useEffect(() => {
    if (refused) void client.invalidateQueries({ queryKey: keys.teamVideos(team.id) })
  }, [refused, client, team.id])
  useEffect(() => {
    if (!doneVideo) return
    void client.invalidateQueries({ queryKey: keys.teamVideos(team.id) })
    void navigate(`/videos/${doneVideo.id}`, { replace: true })
  }, [doneVideo, client, navigate, team.id])

  if (upload.snapshot && state !== 'done') {
    const snapshot = upload.snapshot
    return (
      <UploadProgress
        snapshot={snapshot}
        onRetry={() => void upload.start(snapshot.video, pairs)}
        onLeave={upload.stop}
        onCancel={async () => {
          try {
            await upload.cancel()
            toast.success(t('video.cancelled'))
            await client.invalidateQueries({ queryKey: keys.teamVideos(team.id) })
            void navigate(`/teams/${team.id}`, { replace: true })
          } catch {
            toast.error(t('video.cancelFailed'))
          }
        }}
      />
    )
  }

  const ready =
    on !== null &&
    filming.data?.allowed === true &&
    (chosenSource === 'device' ? files.length > 0 && !fileError : inboxIds.size > 0) &&
    !create.isPending

  const onStart = (formEvent: FormEvent) => {
    formEvent.preventDefault()
    if (!ready || !on) return
    const body: VideoCreate = { kind }
    if (title.trim()) body.title = title.trim()
    if (kind === 'match') body.game_id = Number(event)
    else if (event === OTHER_DAY) body.recorded_on = on
    else body.training_id = Number(event)
    if (chosenSource === 'device') body.files = sortedFiles.map((f) => ({ name: f.name, size: f.size, type: f.type }))
    else body.inbox_file_ids = pickedInbox.map((f) => f.id)

    create.mutate(body, {
      onSuccess: (video: Video) => {
        void client.invalidateQueries({ queryKey: keys.teamVideos(team.id) })
        if (chosenSource === 'inbox') {
          void client.invalidateQueries({ queryKey: keys.inbox })
          void navigate(`/videos/${video.id}`)
          return
        }
        const matched = matchFiles(video.files, sortedFiles).pairs
        setPairs(matched)
        void upload.start(video, matched)
      },
    })
  }

  const chooseKind = (next: VideoKind) => {
    setKind(next)
    setEvent('')
    create.reset()
  }

  return (
    <form onSubmit={onStart} className="flex flex-col gap-6">
      <Card className="flex flex-col gap-5 p-5">
        <Segmented
          legend={t('upload.kind')}
          name={`${id}-kind`}
          value={kind}
          onChange={chooseKind}
          options={[
            { value: 'match', label: t('video.kind.match') },
            { value: 'training', label: t('video.kind.training') },
          ]}
        />

        {(kind === 'match' ? games : trainings).isPending ? (
          <Spinner />
        ) : (kind === 'match' ? games : trainings).isError ? (
          <LoadError onRetry={() => void (kind === 'match' ? games : trainings).refetch()} />
        ) : kind === 'match' && choices.length === 0 ? (
          <p className="text-sm text-muted">{t('upload.noGames')}</p>
        ) : (
          <Field label={kind === 'match' ? t('upload.pickGame') : t('upload.pickTraining')} htmlFor={`${id}-event`}>
            <Select
              id={`${id}-event`}
              value={event}
              onChange={(e) => {
                setEvent(e.target.value)
                create.reset()
              }}
            >
              <option value="" disabled>
                {t('upload.choose')}
              </option>
              {choices.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
              {kind === 'training' && <option value={OTHER_DAY}>{t('upload.otherDay')}</option>}
            </Select>
          </Field>
        )}

        {kind === 'training' && event === OTHER_DAY && (
          <Field label={t('upload.date')} htmlFor={`${id}-date`}>
            <TextInput
              id={`${id}-date`}
              type="date"
              min={oldest}
              max={today}
              value={otherDate}
              aria-invalid={otherDateOk ? undefined : true}
              onChange={(e) => setOtherDate(e.target.value)}
              className="sm:max-w-48"
            />
          </Field>
        )}
        {kind === 'training' && event === OTHER_DAY && !otherDateOk && (
          <p role="alert" className="text-sm text-danger">
            {t('upload.dateRange')}
          </p>
        )}

        {on && <FilmingResult query={filming} on={on} />}
      </Card>

      {on && filming.data?.allowed && (
        <>
          <Card className="flex flex-col gap-4 p-5">
            {inboxAvailable && (
              <Segmented
                legend={t('upload.source')}
                name={`${id}-source`}
                value={source}
                onChange={setSource}
                options={[
                  { value: 'device', label: t('upload.device') },
                  { value: 'inbox', label: t('upload.inbox') },
                ]}
              />
            )}
            {chosenSource === 'device' ? (
              <DevicePicker
                files={sortedFiles}
                error={fileError}
                onPick={(picked) => {
                  setFiles(picked)
                  create.reset()
                }}
              />
            ) : (
              <InboxPicker
                folder={folder}
                files={inboxFiles}
                selected={inboxIds}
                on={on}
                onToggle={(fileId) => {
                  setInboxIds((current) => {
                    const next = new Set(current)
                    if (next.has(fileId)) next.delete(fileId)
                    else next.add(fileId)
                    return next
                  })
                  create.reset()
                }}
              />
            )}
          </Card>

          <Card className="flex flex-col gap-4 p-5">
            <Field label={t('upload.titleLabel')} htmlFor={`${id}-title`}>
              <TextInput
                id={`${id}-title`}
                value={title}
                maxLength={200}
                placeholder={defaultTitle}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            {create.isError && (
              <div role="alert" className="flex flex-col gap-2 text-sm">
                {blockers ? (
                  <>
                    <p className="font-semibold text-danger">{t('upload.blocked')}</p>
                    <BlockerList blockers={blockers} />
                  </>
                ) : (
                  <p className="text-danger">
                    {t('upload.failed', {
                      detail: create.error instanceof ApiError ? create.error.detail : t('upload.networkError'),
                    })}
                  </p>
                )}
              </div>
            )}
            <Button type="submit" variant="primary" className="self-start" disabled={!ready}>
              {create.isPending ? t('upload.creating') : chosenSource === 'inbox' ? t('upload.startInbox') : t('upload.start')}
            </Button>
          </Card>
        </>
      )}
    </form>
  )
}

function Segmented<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string
  name: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-semibold">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const checked = option.value === value
          return (
            <label
              key={option.value}
              className={cx(
                'inline-flex min-h-11 cursor-pointer items-center rounded-xl border px-4 text-sm font-semibold sm:min-h-9',
                'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
                checked ? 'border-transparent bg-accent text-on-accent' : 'border-hairline bg-surface hover:bg-sunken',
              )}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={checked}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              {option.label}
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

function FilmingResult({ query, on }: { query: ReturnType<typeof useFilming>; on: string }) {
  const { t } = useTranslation()
  if (query.isPending) return <Spinner label={t('filming.checking')} />
  if (query.isError) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p role="alert" className="text-sm text-danger">
          {t('filming.checkFailed')}
        </p>
        <Button onClick={() => void query.refetch()}>{t('common.retry')}</Button>
      </div>
    )
  }
  if (query.data.allowed) {
    return (
      <p role="status" className="flex items-center gap-2 text-sm font-semibold">
        <CheckIcon className="size-4" />
        {t('filming.allowedOn', { date: formatDate(on) })}
      </p>
    )
  }
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-xl border border-danger/50 p-3">
      <p className="text-sm font-semibold text-danger">{t('filming.blockedOn', { date: formatDate(on) })}</p>
      <BlockerList blockers={query.data.blockers} />
    </div>
  )
}

function DevicePicker({
  files,
  error,
  onPick,
}: {
  files: File[]
  error: string | null
  onPick: (files: File[]) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const total = files.reduce((sum, f) => sum + f.size, 0)
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t('upload.pickFilesHint')}</p>
      <label
        className={cx(
          'inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 self-start rounded-xl border border-hairline bg-surface px-4 text-sm font-semibold text-fg hover:bg-sunken sm:min-h-9',
          'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
        )}
      >
        <input
          id={id}
          type="file"
          multiple
          accept={VIDEO_ACCEPT}
          className="sr-only"
          onChange={(e) => onPick(e.target.files ? [...e.target.files] : [])}
        />
        {t('upload.pickFiles')}
      </label>
      {files.length > 0 && (
        <>
          <p className="text-sm font-semibold">
            {t('upload.filesChosen', { count: files.length, size: formatBytes(total) })}
          </p>
          <Card className="overflow-hidden">
            <Table caption={t('upload.source')}>
              <THead>
                <tr>
                  <Th>{t('columns.file')}</Th>
                  <Th className="text-right">{t('columns.size')}</Th>
                </tr>
              </THead>
              <TBody>
                {files.map((file) => (
                  <tr key={`${file.name}-${file.size}`}>
                    <Td className="break-all">{file.name}</Td>
                    <Td className="text-right whitespace-nowrap text-muted tabular-nums">{formatBytes(file.size)}</Td>
                  </tr>
                ))}
              </TBody>
            </Table>
          </Card>
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

function InboxPicker({
  folder,
  files,
  selected,
  on,
  onToggle,
}: {
  folder: string
  files: InboxFile[]
  selected: ReadonlySet<string>
  on: string
  onToggle: (id: string) => void
}) {
  const { t } = useTranslation()
  const day = formatDate(on)
  const [lead, rest] = t('upload.inboxFolder', { folder: '\u0000' }).split('\u0000')
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        {t('upload.inboxHint')} {lead}
        <code className="rounded-md bg-sunken px-1 py-0.5 font-mono text-fg">{folder}</code>
        {rest}
      </p>
      {files.length === 0 ? (
        <p className="text-sm">{t('upload.inboxEmpty')}</p>
      ) : (
      <Card className="overflow-hidden">
        <Table caption={t('upload.inbox')}>
          <THead>
            <tr>
              <Th className="w-0">
                <span className="sr-only">{t('upload.inbox')}</span>
              </Th>
              <Th>{t('columns.file')}</Th>
              <Th className="hidden sm:table-cell">{t('columns.recorded')}</Th>
              <Th className="hidden sm:table-cell">{t('columns.duration')}</Th>
              <Th className="text-right">{t('columns.size')}</Th>
            </tr>
          </THead>
          <TBody>
            {sortByName(files.map((f) => ({ ...f, name: f.path }))).map((file) => {
              const name = file.path.split('/').pop() ?? file.path
              const sameDay = file.recorded_at_guess !== null && formatDate(file.recorded_at_guess) === day
              return (
                <tr key={file.id} className="[&>td]:align-middle">
                  <Td tight>
                    <input
                      type="checkbox"
                      className="size-5 accent-accent"
                      checked={selected.has(file.id)}
                      aria-label={t('upload.inboxPick', { name })}
                      onChange={() => onToggle(file.id)}
                    />
                  </Td>
                  <Td>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold break-all">{name}</span>
                      {sameDay && <Badge>{t('upload.sameDay')}</Badge>}
                    </span>
                    {name !== file.path && <span className="block text-xs break-all text-muted">{file.path}</span>}
                    <span className="block text-xs text-muted tabular-nums sm:hidden">
                      {[formatDateTime(file.recorded_at_guess), formatDuration(file.duration_ms)]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                    {formatDateTime(file.recorded_at_guess) || '–'}
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                    {formatDuration(file.duration_ms) || '–'}
                  </Td>
                  <Td className="text-right whitespace-nowrap text-muted tabular-nums">{formatBytes(file.size)}</Td>
                </tr>
              )
            })}
          </TBody>
        </Table>
      </Card>
      )}
    </div>
  )
}

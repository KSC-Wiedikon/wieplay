import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { Badge, SportBadge } from '../components/Badge'
import { Button } from '../components/Button'
import { buttonClasses } from '../components/buttonStyles'
import { Card } from '../components/Card'
import { Field, Select, TextInput } from '../components/Field'
import { CheckIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { UploadProgress } from '../components/UploadProgress'
import { BlockerList } from '../components/VideoBadges'
import { api, ApiError, blockersOf, ForbiddenError, NotFoundError } from '../lib/api'
import { cx } from '../lib/cx'
import { addDaysIso, formatBytes, formatDate, formatDateTime, formatDuration, todayIso } from '../lib/format'
import { opponentOf } from '../lib/games'
import { useFilming, useInbox, useMe, useTeam, useTeamGames, useVideoConfig } from '../lib/queries'
import { keys } from '../lib/queryClient'
import { sportOf, useSportAccent } from '../lib/sport'
import { useToast } from '../lib/toast'
import type { InboxFile, TeamDetail, Video, VideoConfig, VideoCreate } from '../lib/types'
import { matchFiles, sortByName, useUpload, type FilePair } from '../lib/upload'
import {
  inboxFolder,
  inTeamFolder,
  isVideoFileName,
  parseVideoLink,
  platformName,
  VIDEO_ACCEPT,
  VIDEO_EXTENSIONS,
} from '../lib/video'
import { NotFoundPage } from './NotFoundPage'

/** How far back a game can be picked. */
/** The filming check answers for days up to 400 days back. */
const OLDEST_DAYS = 400
/** A match that is not in Wiedisync (friendly, tournament, cup): a date and a title instead of a game. */
const OTHER_MATCH = 'other'
/** Placeholder of the link field. */
const LINK_EXAMPLE = 'https://www.youtube.com/watch?v=…'

/** Files from this device, files in the NAS inbox, or a YouTube/Twitch link the worker fetches. */
type Source = 'device' | 'inbox' | 'link'

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
    // A form: it keeps a readable width on wide screens.
    <div className="flex max-w-3xl flex-col gap-6">
      <title>{`${t('upload.title')} · ${team.data.name} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <>
            <SportBadge sport={team.data.sport} />
            <Link to={`/teams/${teamId}`} className="font-semibold link">
              {team.data.name}
            </Link>
            <span>{team.data.season}</span>
          </>
        }
        title={t('upload.title')}
      />
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
  const oldest = addDaysIso(today, -OLDEST_DAYS)

  // A guest club's team (PLAN §4.5) has no Wiedisync games, no NAS inbox and no link imports:
  // a date and a title, and the files from this device.
  const guest = team.guest === true
  const [picked, setMatch] = useState(guest ? OTHER_MATCH : '')
  const [otherDate, setOtherDate] = useState(today)
  const [otherTitle, setOtherTitle] = useState('')
  const [source, setSource] = useState<Source>('device')
  const [files, setFiles] = useState<File[]>([])
  const [inboxIds, setInboxIds] = useState<ReadonlySet<string>>(new Set())
  const [link, setLink] = useState('')
  const [title, setTitle] = useState('')
  const [pairs, setPairs] = useState<FilePair[]>([])

  const games = useTeamGames(team.id)
  const inbox = useInbox(!guest)
  const me = useMe()

  const choices = useMemo<Choice[]>(
    () =>
      (games.data ?? [])
        // Every played game the filming rules still cover, so older games can be imported.
        .filter((g) => g.date && g.date >= oldest && g.date <= today)
        .sort((a, b) => b.date.localeCompare(a.date))
        .map((g) => ({
          value: String(g.id),
          label: `${formatDate(g.date)} · ${opponentOf(g)}`,
          date: g.date,
          defaultTitle: `${g.home_name} – ${g.away_name}`,
        })),
    [games.data, oldest, today],
  )

  // `?game=<id>`: the game is already picked (WiePlay's page for a Wiedisync game links here),
  // as long as it is one of the choices and nothing else was picked.
  const [searchParams] = useSearchParams()
  const linked = searchParams.get('game')
  const match = picked === '' && linked !== null && choices.some((c) => c.value === linked) ? linked : picked
  const other = match === OTHER_MATCH
  const choice = choices.find((c) => c.value === match)
  const otherDateOk = /^\d{4}-\d{2}-\d{2}$/.test(otherDate) && otherDate >= oldest && otherDate <= today
  const on = other ? (otherDateOk ? otherDate : null) : (choice?.date ?? null)
  const filming = useFilming(team.id, on)

  // The inbox has one folder per team; only this team's files can go into its videos.
  const folder = inboxFolder(team)
  const inboxFiles = (inbox.data ?? []).filter((f) => inTeamFolder(f.path, folder))
  const inboxAvailable = inbox.isSuccess
  const chosenSource = source === 'inbox' && !inboxAvailable ? 'device' : source
  const videoLink = parseVideoLink(link)
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
  // The only 409 for a link without blockers: no channel of that platform is allow-listed yet.
  const noChannel =
    chosenSource === 'link' && !blockers && create.error instanceof ApiError && create.error.status === 409

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
    (!other || otherTitle.trim() !== '') &&
    filming.data?.allowed === true &&
    (chosenSource === 'device'
      ? files.length > 0 && !fileError
      : chosenSource === 'inbox'
        ? inboxIds.size > 0
        : videoLink !== null) &&
    !create.isPending

  const onStart = (formEvent: FormEvent) => {
    formEvent.preventDefault()
    if (!ready || !on) return
    const body: VideoCreate = {}
    if (other) {
      body.recorded_on = on
      body.title = otherTitle.trim()
    } else {
      body.game_id = Number(match)
      if (title.trim()) body.title = title.trim()
    }
    if (chosenSource === 'device') body.files = sortedFiles.map((f) => ({ name: f.name, size: f.size, type: f.type }))
    else if (chosenSource === 'inbox') body.inbox_file_ids = pickedInbox.map((f) => f.id)
    // The text as typed: the API parses it again and fetches only its canonical address.
    else body.import_url = link.trim()

    create.mutate(body, {
      onSuccess: (video: Video) => {
        void client.invalidateQueries({ queryKey: keys.teamVideos(team.id) })
        // Nothing to upload from the browser: the worker takes it from here.
        if (chosenSource !== 'device') {
          if (chosenSource === 'inbox') void client.invalidateQueries({ queryKey: keys.inbox })
          void navigate(`/videos/${video.id}`)
          return
        }
        const matched = matchFiles(video.files, sortedFiles).pairs
        setPairs(matched)
        void upload.start(video, matched)
      },
    })
  }

  return (
    <form onSubmit={onStart} className="flex flex-col gap-6">
      <Card className="flex flex-col gap-5 p-5">
        {guest ? null : games.isPending ? (
          <Spinner />
        ) : games.isError ? (
          <LoadError onRetry={() => void games.refetch()} />
        ) : (
          <Field
            label={t('upload.pickMatch')}
            hint={choices.length === 0 ? t('upload.noGames') : undefined}
            htmlFor={`${id}-match`}
          >
            <Select
              id={`${id}-match`}
              value={match}
              onChange={(e) => {
                setMatch(e.target.value)
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
              <option value={OTHER_MATCH}>{t('upload.otherMatch')}</option>
            </Select>
          </Field>
        )}

        {other && (
          <>
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
            {!otherDateOk && (
              <p role="alert" className="text-sm text-danger">
                {t('upload.dateRange')}
              </p>
            )}
            <Field label={t('upload.matchTitle')} hint={t('upload.matchTitleHint')} htmlFor={`${id}-match-title`}>
              <TextInput
                id={`${id}-match-title`}
                value={otherTitle}
                required
                maxLength={200}
                onChange={(e) => {
                  setOtherTitle(e.target.value)
                  create.reset()
                }}
              />
            </Field>
          </>
        )}

        {on && <FilmingResult query={filming} on={on} />}
      </Card>

      {on && filming.data?.allowed && (
        <>
          <Card className="flex flex-col gap-4 p-5">
            {!guest && (
              <Segmented
                legend={t('upload.sourceLegend')}
                name={`${id}-source`}
                value={chosenSource}
                onChange={(next) => {
                  setSource(next)
                  create.reset()
                }}
                options={[
                  { value: 'device', label: t('upload.device') },
                  ...(inboxAvailable ? [{ value: 'inbox' as const, label: t('upload.inbox') }] : []),
                  { value: 'link', label: t('upload.link') },
                ]}
              />
            )}
            {chosenSource === 'link' ? (
              <LinkPicker
                value={link}
                onChange={(value) => {
                  setLink(value)
                  create.reset()
                }}
              />
            ) : chosenSource === 'device' ? (
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
            {!other && (
              <Field label={t('upload.titleLabel')} htmlFor={`${id}-title`}>
                <TextInput
                  id={`${id}-title`}
                  value={title}
                  maxLength={200}
                  placeholder={choice?.defaultTitle}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
            )}
            {create.isError && (
              <div role="alert" className="flex flex-col gap-2 text-sm">
                {blockers ? (
                  <>
                    <p className="font-semibold text-danger">{t('upload.blocked')}</p>
                    <BlockerList blockers={blockers} />
                  </>
                ) : noChannel ? (
                  <>
                    <p className="text-danger">
                      {t('upload.noChannel', { platform: platformName(videoLink?.platform ?? '') })}
                    </p>
                    {me.data?.is_admin && (
                      <Link
                        to="/admin/import-channels"
                        className="self-start font-semibold link"
                      >
                        {t('upload.addChannels')}
                      </Link>
                    )}
                  </>
                ) : (
                  <p className="text-danger">
                    {t(chosenSource === 'link' ? 'upload.importFailed' : 'upload.failed', {
                      detail: create.error instanceof ApiError ? create.error.detail : t('upload.networkError'),
                    })}
                  </p>
                )}
              </div>
            )}
            <Button type="submit" variant="primary" className="self-start" disabled={!ready}>
              {create.isPending
                ? t('upload.creating')
                : chosenSource === 'inbox'
                  ? t('upload.startInbox')
                  : chosenSource === 'link'
                    ? t('upload.startLink')
                    : t('upload.start')}
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
                'inline-flex min-h-11 cursor-pointer items-center rounded-control border px-4 text-sm font-semibold sm:min-h-9',
                'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
                checked ? 'border-transparent bg-accent text-on-accent shadow-card' : 'border-hairline-strong bg-surface hover:bg-sunken',
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
        className={buttonClasses({}, 'self-start')}
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

/** A YouTube or Twitch video link, recognised while it is typed. */
function LinkPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation()
  const id = useId()
  const link = parseVideoLink(value)
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t('upload.linkHint')}</p>
      <Field label={t('upload.linkLabel')} htmlFor={id}>
        <TextInput
          id={id}
          value={value}
          inputMode="url"
          maxLength={500}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder={LINK_EXAMPLE}
          aria-describedby={`${id}-status`}
          onChange={(e) => onChange(e.target.value)}
        />
        {/* Always rendered, so screen readers announce what the link turned out to be. */}
        <div id={`${id}-status`} role="status" className="flex flex-col gap-1 text-sm">
          {link ? (
            <>
              <p className="flex items-center gap-2 font-semibold">
                <CheckIcon className="size-4" />
                {t('upload.linkRecognized', { platform: platformName(link.platform), id: link.id })}
              </p>
              {link.platform === 'twitch' && <p className="text-muted">{t('upload.linkTwitchSoon')}</p>}
            </>
          ) : (
            value.trim() !== '' && <p className="text-muted">{t('upload.linkUnknown')}</p>
          )}
        </div>
      </Field>
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

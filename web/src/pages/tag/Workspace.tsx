import { useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Button, ButtonLink } from '../../components/Button'
import { ClipDialog } from '../../components/ClipDialog'
import { AlertIcon, CheckIcon, KeyboardIcon, PauseIcon, PlayIcon, RedoIcon, UndoIcon } from '../../components/icons'
import { LoadError } from '../../components/Message'
import { PageSpinner } from '../../components/Spinner'
import { Switch } from '../../components/Switch'
import { VideoPlayer } from '../../components/VideoPlayer'
import { api } from '../../lib/api'
import type { ClipDraft } from '../../lib/clips'
import { playWindows } from '../../lib/condensed'
import { cx } from '../../lib/cx'
import { formatDate } from '../../lib/format'
import { formatEntry } from '../../lib/grammar'
import { formatClock, periodLabel, playerLabel, segmentKindLabel, sideLabel } from '../../lib/labels'
import { Outbox, type OutboxItem } from '../../lib/outbox'
import { Playhead, usePlayhead } from '../../lib/playhead'
import { keys } from '../../lib/queryClient'
import { periodAt, ralliesOf, scoreBefore, SERVE_SLACK_MS, serveCodes, shotScoreAt } from '../../lib/rallies'
import { TaggingStore } from '../../lib/taggingStore'
import type { Tagging, Taxonomy, Video } from '../../lib/types'
import { useCondensedPlayback } from '../../lib/useCondensedPlayback'
import { WorkspaceContext, useSync, useUi, useView, useWorkspace, type WorkspaceParts } from './context'
import { Cheatsheet } from './Cheatsheet'
import { TagController } from './controller'
import { EntryPanel } from './EntryPanel'
import { EventTable } from './EventTable'
import { RosterPanel } from './RosterPanel'
import { SetsBar } from './SetsBar'
import { SegmentPanel, Timeline } from './Timeline'

/**
 * Workspaces of this tab that were just closed and are still sending their last changes, by
 * video. A new workspace for the same video waits for them, so the data it loads includes them.
 */
const closing = new Map<string, Promise<void>>()

interface Session {
  store: TaggingStore
  outbox: Outbox
  bundle: Tagging
}

/**
 * One tagging session: loads the video's tagging data fresh (never a cached copy, which may
 * miss changes sent moments ago), opens the outbox (queued changes of an earlier visit come
 * back), then shows the desk. Closing it sends what is still queued.
 */
export function Workspace({ video, taxonomy }: { video: Video; taxonomy: Taxonomy }) {
  const client = useQueryClient()
  const [session, setSession] = useState<Session | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    let opened: Session | null = null
    const start = async () => {
      await closing.get(video.id)
      const bundle = await client.fetchQuery({
        queryKey: keys.tagging(video.id),
        queryFn: ({ signal }) => api.tagging(video.id, signal),
        staleTime: 0,
      })
      if (cancelled) return
      const store = new TaggingStore(video.id, bundle)
      const outbox = await Outbox.open({
        videoId: video.id,
        send: (body) => api.postChanges(video.id, body),
        onSynced: (out) => store.applySynced(out),
      })
      if (cancelled) {
        outbox.close()
        return
      }
      store.attach(outbox)
      opened = { store, outbox, bundle }
      setSession(opened)
    }
    start().catch(() => {
      if (!cancelled) setFailed(true)
    })
    return () => {
      cancelled = true
      const ended = opened
      if (!ended) return
      ended.store.detach()
      // Leaving: send what is queued now rather than at the next visit.
      const done = ended.outbox.flush().finally(() => {
        ended.outbox.close()
        if (closing.get(video.id) === done) closing.delete(video.id)
        void client.invalidateQueries({ queryKey: keys.tagging(video.id) })
      })
      closing.set(video.id, done)
    }
  }, [client, video.id, attempt])

  if (failed) {
    return (
      <LoadError
        onRetry={() => {
          setFailed(false)
          setSession(null)
          setAttempt((n) => n + 1)
        }}
      />
    )
  }
  if (!session) return <PageSpinner />
  return <Desk video={video} bundle={session.bundle} taxonomy={taxonomy} store={session.store} outbox={session.outbox} />
}

interface WorkspaceProps {
  video: Video
  bundle: Tagging
  taxonomy: Taxonomy
}

function Desk({ video, bundle, taxonomy, store, outbox }: WorkspaceProps & { store: TaggingStore; outbox: Outbox }) {
  const [playhead] = useState(() => new Playhead())
  const [controller] = useState(() => new TagController(store, playhead, taxonomy, { duration_ms: video.duration_ms }))
  const [element, setElement] = useState<HTMLVideoElement | null>(null)
  const parts = useMemo<WorkspaceParts>(
    () => ({ video, bundle, taxonomy, store, outbox, playhead, controller }),
    [video, bundle, taxonomy, store, outbox, playhead, controller],
  )

  useEffect(() => {
    playhead.attach(element)
    return () => playhead.detach()
  }, [playhead, element])

  useEffect(() => {
    window.addEventListener('keydown', controller.onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', controller.onKeyDown, { capture: true })
  }, [controller])

  return (
    <WorkspaceContext value={parts}>
      <DeskLayout element={element} setElement={setElement} />
    </WorkspaceContext>
  )
}

function DeskLayout({
  element,
  setElement,
}: {
  element: HTMLVideoElement | null
  setElement: (element: HTMLVideoElement | null) => void
}) {
  const { t } = useTranslation()
  const { video, controller } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const sync = useSync()

  const rallies = useMemo(() => ralliesOf(view.segments), [view.segments])
  const windows = useMemo(() => playWindows(rallies, { duration_ms: video.duration_ms }), [rallies, video.duration_ms])
  useCondensedPlayback(element, windows, ui.skip)

  // Without IndexedDB, unsent changes are lost with the page: ask before leaving.
  useEffect(() => {
    if (sync.persistent || sync.pending === 0) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [sync.persistent, sync.pending])

  return (
    <div className="flex flex-col gap-4">
      <title>{`${t('tagging.pageTitle', { title: video.title })} · nomal`}</title>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-xl font-bold wrap-break-word">{video.title}</h1>
          <p className="text-sm text-muted">
            <span className="tabular-nums">{formatDate(video.recorded_on)}</span>
            {' · '}
            <Link to={`/teams/${video.team_id}`} className="font-semibold link">
              {`${video.team_name} ${video.team_season}`}
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <SyncStatus />
          <ButtonLink to={`/videos/${video.id}`}>{t('tagging.back')}</ButtonLink>
        </div>
      </header>
      <SyncNotes />

      <div className="flex items-start gap-4">
        {/* The video stays short enough to keep the sets bar and the timeline on screen. */}
        <div className="flex max-w-[calc((100dvh-27rem)*16/9)] min-w-0 flex-[100_1_0%] flex-col gap-2">
          <VideoPlayer video={video} videoRef={setElement} keyboard={false} onFrameRate={controller.setFps} />
          <Transport />
        </div>
        {/* About 22rem until the video reaches its height limit; then the panel takes the rest. */}
        <aside className="flex min-w-80 flex-[1_0_22rem] flex-col gap-2" aria-label={t('tagging.panel')}>
          <StatePanel />
          <EntryPanel />
          <RosterPanel />
        </aside>
      </div>
      <SetsBar />
      <Timeline />
      {ui.segmentId && <SegmentPanel key={ui.segmentId} id={ui.segmentId} />}
      <EventTable />
      <Cheatsheet />
      <WorkspaceClipDialog />
    </div>
  )
}

/** C in the workspace: save the marked range as a clip (linked to the selected tag inside it). */
function WorkspaceClipDialog() {
  const { t } = useTranslation()
  const { controller, video } = useWorkspace()
  const ui = useUi()
  const view = useView()
  const request = ui.clipRequest
  const draft = useMemo<ClipDraft | null>(() => {
    if (!request) return null
    const tag = request.event_id ? view.events.find((e) => e.id === request.event_id) : undefined
    const title = tag
      ? `${formatEntry(tag)} · ${playerLabel(t, tag.side, tag.number, view.roster, tag.member_id)}`
      : t('clips.dialog.defaultTitle', { time: formatClock(request.start_ms, { tenths: false }) })
    return { id: null, video_id: video.id, start_ms: request.start_ms, end_ms: request.end_ms, title, note: null, event_id: request.event_id, visibility: 'private' }
    // The form keeps what was typed: it reads the draft once per clip range.
  }, [request, view.events, view.roster, video.id, t])
  return (
    <ClipDialog
      draft={draft}
      // Who may tag may share with the team (PLAN §5.1).
      canShare
      duration_ms={video.duration_ms}
      onClose={() => controller.closeClip(false)}
      onSaved={() => controller.closeClip(true)}
    />
  )
}

function Transport() {
  const { t } = useTranslation()
  const { controller, playhead, video } = useWorkspace()
  const ui = useUi()
  const view = useView()
  const playing = usePlayhead(playhead, (s) => s.playing)
  const rate = usePlayhead(playhead, (s) => s.rate)
  const tenths = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 100))
  const duration = usePlayhead(playhead, (s) => s.duration_ms) ?? video.duration_ms

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card border border-hairline bg-surface shadow-card p-2">
      <Button variant="primary" size="icon" onClick={controller.togglePlay} aria-label={playing ? t('tagging.transport.pause') : t('tagging.transport.play')}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </Button>
      <p className="min-w-36 font-mono text-sm tabular-nums">
        {t('tagging.transport.time', { time: formatClock(tenths * 100), total: formatClock(duration, { tenths: false }) })}
      </p>
      <div className="flex items-center gap-1" role="group" aria-label={t('tagging.transport.speed')}>
        <Button size="icon" onClick={() => controller.speed(-1)} aria-label={t('tagging.transport.slower')}>
          <span aria-hidden="true">−</span>
        </Button>
        <span className="w-12 text-center text-sm font-semibold tabular-nums">{`${rate}×`}</span>
        <Button size="icon" onClick={() => controller.speed(1)} aria-label={t('tagging.transport.faster')}>
          <span aria-hidden="true">+</span>
        </Button>
      </div>
      {/* Dead time is what lies between rallies; basketball marks none (yet). */}
      {controller.playsRallies && (
        <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold">
          <Switch checked={ui.skip} onChange={(on) => controller.toggleSkip(on)} label={t('tagging.condensed.skip')} />
          <span aria-hidden="true">{t('tagging.condensed.skip')}</span>
        </label>
      )}
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="icon" disabled={!view.canUndo} onClick={controller.undo} aria-label={t('tagging.transport.undo')} title={t('tagging.transport.undo')}>
          <UndoIcon />
        </Button>
        <Button variant="ghost" size="icon" disabled={!view.canRedo} onClick={controller.redo} aria-label={t('tagging.transport.redo')} title={t('tagging.transport.redo')}>
          <RedoIcon />
        </Button>
        <Button variant="ghost" onClick={() => controller.toggleCheatsheet(true)}>
          <KeyboardIcon />
          {t('tagging.keys.open')}
        </Button>
      </div>
    </div>
  )
}

function SyncStatus() {
  const { t } = useTranslation()
  const { outbox } = useWorkspace()
  const sync = useSync()

  let text: string
  let tone: 'ok' | 'busy' | 'warn' = 'busy'
  if (sync.blocked) {
    tone = 'warn'
    text =
      sync.blocked.status === 401
        ? t('tagging.sync.loggedOut', { count: sync.pending })
        : sync.blocked.status === 403
          ? t('tagging.sync.forbidden', { count: sync.pending })
          : t('tagging.sync.blocked', { detail: sync.blocked.detail, count: sync.pending })
  } else if (sync.pending === 0 && sync.phase !== 'sending') {
    tone = 'ok'
    text = t('tagging.sync.saved')
  } else if (!sync.online) {
    tone = 'warn'
    text = t('tagging.sync.offline', { count: sync.pending })
  } else if (sync.phase === 'retrying') {
    tone = 'warn'
    text = t('tagging.sync.retrying', { count: sync.pending })
  } else if (sync.phase === 'sending') {
    text = t('tagging.sync.saving')
  } else {
    text = t('tagging.sync.unsynced', { count: sync.pending })
  }

  return (
    <div className="flex items-center gap-2">
      <p
        role="status"
        className={cx(
          'flex items-center gap-1.5 rounded-xl px-2 py-1 text-sm font-semibold',
          tone === 'warn' ? 'text-danger' : tone === 'ok' ? 'text-muted' : 'text-fg',
        )}
      >
        {tone === 'ok' ? <CheckIcon className="size-4" /> : tone === 'warn' ? <AlertIcon className="size-4" /> : null}
        {text}
      </p>
      {(sync.blocked || sync.phase === 'retrying') && (
        <Button onClick={() => void outbox.retry()}>{t('tagging.sync.retry')}</Button>
      )}
    </div>
  )
}

function describe(t: TFunction, item: OutboxItem, sport: string): string {
  if (item.kind === 'event') return formatEntry({ ...item.payload, number: item.payload.number ?? null })
  if (item.kind === 'segment') return segmentKindLabel(t, item.payload.kind, sport)
  return periodLabel(t, sport, item.payload.no)
}

/** Changes the server refused or that someone else overtook; and a warning without IndexedDB. */
function SyncNotes() {
  const { t } = useTranslation()
  const { outbox, taxonomy } = useWorkspace()
  const sync = useSync()
  const notes = [
    ...sync.rejected.map((r) => t('tagging.sync.rejected', { entry: describe(t, r.item, taxonomy.sport), detail: r.detail })),
    ...sync.stale.map((item) => t('tagging.sync.stale', { entry: describe(t, item, taxonomy.sport) })),
  ]
  if (notes.length === 0 && sync.persistent) return null
  return (
    <div className="flex flex-col gap-2">
      {!sync.persistent && (
        <p className="flex items-center gap-2 rounded-xl border border-hairline bg-surface p-3 text-sm">
          <AlertIcon className="size-4" />
          {t('tagging.sync.memoryOnly')}
        </p>
      )}
      {notes.length > 0 && (
        <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-danger bg-surface p-3 text-sm">
          <ul className="flex flex-col gap-1">
            {notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
          <Button variant="ghost" onClick={() => outbox.dismissNotes()}>
            {t('common.dismiss')}
          </Button>
        </div>
      )}
    </div>
  )
}

/** The match state at the playhead, per sport. */
function StatePanel() {
  const { taxonomy } = useWorkspace()
  return taxonomy.sport === 'basketball' ? <QuarterState /> : <SetState />
}

/** Set, score and server at the playhead (PLAN §9.1: "Set 2 · 14:12 · serve: us"). */
function SetState() {
  const { t } = useTranslation()
  const { playhead, taxonomy } = useWorkspace()
  const view = useView()
  const tenths = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 100))
  const t_ms = tenths * 100
  const state = scoreBefore(t_ms, view.periods, view.segments)
  const open = view.openRally
  const serves = serveCodes(taxonomy)
  const serveTag = open
    ? view.events.find((e) => serves.has(e.code) && e.t_ms >= open.start_ms - SERVE_SLACK_MS && e.t_ms <= t_ms)
    : undefined
  const serving = serveTag?.side ?? state.serving

  return (
    <section aria-label={t('tagging.state.title')} className="flex flex-col gap-1 rounded-card border border-hairline bg-surface shadow-card px-4 py-3">
      <p className="flex flex-wrap items-baseline gap-x-3 text-lg font-bold tabular-nums">
        <span>{state.period ? t('tagging.setNo', { no: state.period.no }) : t('tagging.state.noSet')}</span>
        {state.period && <span className="text-2xl">{`${state.us}:${state.them}`}</span>}
        <span className="text-base font-semibold">
          {serving ? t('tagging.state.serve', { side: sideLabel(t, serving) }) : t('tagging.state.serveUnknown')}
        </span>
      </p>
      <p className="text-sm text-muted tabular-nums">
        {open ? t('tagging.state.rallyOpen', { time: formatClock(open.start_ms) }) : t('tagging.state.noRally')}
      </p>
      {view.provisional && <p className="text-xs text-muted">{t('tagging.state.provisional')}</p>}
    </section>
  )
}

/** Basketball: the quarter and the running score at the playhead ("Q2 · 38:35"); no serve, no rallies. */
function QuarterState() {
  const { t } = useTranslation()
  const { playhead, taxonomy } = useWorkspace()
  const view = useView()
  const tenths = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 100))
  const t_ms = tenths * 100
  const state = shotScoreAt(t_ms, view.periods, view.events, taxonomy)
  const running = periodAt(view.periods, t_ms)

  return (
    <section aria-label={t('tagging.state.title')} className="flex flex-col gap-1 rounded-card border border-hairline bg-surface shadow-card px-4 py-3">
      <p className="flex flex-wrap items-baseline gap-x-3 text-lg font-bold tabular-nums">
        <span>{running ? periodLabel(t, taxonomy.sport, running.no) : t('tagging.basketball.noQuarter')}</span>
        {state.period && <span className="text-2xl">{`${state.us}:${state.them}`}</span>}
      </p>
      {view.provisional && <p className="text-xs text-muted">{t('tagging.state.provisional')}</p>}
    </section>
  )
}

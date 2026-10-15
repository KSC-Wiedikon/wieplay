import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Button, ButtonLink } from '../../components/Button'
import { AlertIcon, CheckIcon, KeyboardIcon, PauseIcon, PlayIcon, RedoIcon, UndoIcon } from '../../components/icons'
import { PageSpinner } from '../../components/Spinner'
import { Switch } from '../../components/Switch'
import { VideoPlayer } from '../../components/VideoPlayer'
import { api } from '../../lib/api'
import { playWindows } from '../../lib/condensed'
import { cx } from '../../lib/cx'
import { formatDate } from '../../lib/format'
import { formatEntry } from '../../lib/grammar'
import { formatClock, sideLabel } from '../../lib/labels'
import { Outbox, type OutboxItem } from '../../lib/outbox'
import { Playhead, usePlayhead } from '../../lib/playhead'
import { keys } from '../../lib/queryClient'
import { ralliesOf, scoreBefore, SERVE_SLACK_MS, serveCodes } from '../../lib/rallies'
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

interface WorkspaceProps {
  video: Video
  bundle: Tagging
  taxonomy: Taxonomy
}

/** Opens the outbox (queued changes of an earlier visit come back) and then shows the desk. */
export function Workspace({ video, bundle, taxonomy }: WorkspaceProps) {
  const client = useQueryClient()
  const [store] = useState(() => new TaggingStore(video.id, bundle))
  const [outbox, setOutbox] = useState<Outbox | null>(null)

  useEffect(() => {
    let cancelled = false
    let opened: Outbox | null = null
    void Outbox.open({
      videoId: video.id,
      send: (body) => api.postChanges(video.id, body),
      onSynced: (out) => store.applySynced(out),
    }).then((o) => {
      if (cancelled) {
        o.close()
        return
      }
      opened = o
      store.attach(o)
      setOutbox(o)
    })
    return () => {
      cancelled = true
      store.detach()
      const o = opened
      if (!o) return
      // Leaving: send what is queued now rather than at the next visit.
      void o.flush().finally(() => {
        o.close()
        void client.invalidateQueries({ queryKey: keys.tagging(video.id) })
      })
    }
  }, [store, video.id, client])

  if (!outbox) return <PageSpinner />
  return <Desk video={video} bundle={bundle} taxonomy={taxonomy} store={store} outbox={outbox} />
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
            <Link to={`/teams/${video.team_id}`} className="font-semibold text-link underline-offset-2 hover:underline">
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
    </div>
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
    <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-hairline bg-surface p-2">
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
      <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold">
        <Switch checked={ui.skip} onChange={(on) => controller.toggleSkip(on)} label={t('tagging.condensed.skip')} />
        <span aria-hidden="true">{t('tagging.condensed.skip')}</span>
      </label>
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

function describe(t: ReturnType<typeof useTranslation>['t'], item: OutboxItem): string {
  if (item.kind === 'event') return formatEntry({ ...item.payload, number: item.payload.number ?? null })
  if (item.kind === 'segment') return t(`tagging.segment.kind.${item.payload.kind}`)
  return t('tagging.setNo', { no: item.payload.no })
}

/** Changes the server refused or that someone else overtook; and a warning without IndexedDB. */
function SyncNotes() {
  const { t } = useTranslation()
  const { outbox } = useWorkspace()
  const sync = useSync()
  const notes = [
    ...sync.rejected.map((r) => t('tagging.sync.rejected', { entry: describe(t, r.item), detail: r.detail })),
    ...sync.stale.map((item) => t('tagging.sync.stale', { entry: describe(t, item) })),
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

/** Set, score and server at the playhead (PLAN §9.1: "Set 2 · 14:12 · serve: us"). */
function StatePanel() {
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
    <section aria-label={t('tagging.state.title')} className="flex flex-col gap-1 rounded-2xl border border-hairline bg-surface px-4 py-3">
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

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { AnalysisStatus } from '../../components/AnalysisStatus'
import { Button, ButtonLink } from '../../components/Button'
import { ClipDialog } from '../../components/ClipDialog'
import { AlertIcon, CheckIcon, KeyboardIcon, PauseIcon, PlayIcon, RedoIcon, UndoIcon } from '../../components/icons'
import { LoadError } from '../../components/Message'
import { SuggestedValue } from '../../components/ScoringMarks'
import { PageSpinner } from '../../components/Spinner'
import { Segmented } from '../../components/Segmented'
import { Switch } from '../../components/Switch'
import { TrajectoryLayer, TrajectoryToggle } from '../../components/Trajectories'
import { useTrajectories } from '../../lib/trajectories'
import { VideoPlayer } from '../../components/VideoPlayer'
import { analysisQuery, isBusy, useAnalysis } from '../../lib/analysis'
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
import { actionItems, factTags, suggestedCount } from '../../lib/actions'
import { allProgress, reviewProgress, suggestionCount } from '../../lib/suggestions'
import { TaggingStore } from '../../lib/taggingStore'
import type { Tagging, Taxonomy, Video } from '../../lib/types'
import { useCondensedPlayback } from '../../lib/useCondensedPlayback'
import { useLoopPlayback } from '../../lib/useLoopPlayback'
import { WorkspaceContext, useActionReview, useReview, useSync, useUi, useView, useWorkspace, type WorkspaceParts } from './context'
import { ActionReviewController } from './actionReview'
import { ActionReviewPanel } from './ActionReviewPanel'
import { Cheatsheet } from './Cheatsheet'
import { TagController } from './controller'
import { EntryPanel } from './EntryPanel'
import { EventTable } from './EventTable'
import { RallyList } from './RallyList'
import { ReviewController } from './review'
import { ReviewPanel } from './ReviewPanel'
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
  /** Proposals rejected before this session (no longer in the tagging data), for the review's progress. */
  rejectedBefore: number
  /** Suggested actions rejected before this session, likewise. */
  actionsRejectedBefore: number
}

/**
 * One tagging session: loads the video's tagging data fresh (never a cached copy, which may
 * miss changes sent moments ago), opens the outbox (queued changes of an earlier visit come
 * back), then shows the desk. Closing it sends what is still queued.
 */
export function Workspace({ video, taxonomy, narrow = false }: { video: Video; taxonomy: Taxonomy; narrow?: boolean }) {
  const client = useQueryClient()
  const [session, setSession] = useState<Session | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    let opened: Session | null = null
    const start = async () => {
      await closing.get(video.id)
      const [bundle, analysis] = await Promise.all([
        client.fetchQuery({
          queryKey: keys.tagging(video.id),
          queryFn: ({ signal }) => api.tagging(video.id, signal),
          staleTime: 0,
        }),
        // Read with the tagging data, so its count of rejected proposals matches it. Optional:
        // without it (basketball, an older server) the review counts what the data lists.
        taxonomy.sport === 'basketball' ? null : client.fetchQuery({ ...analysisQuery(video.id), staleTime: 0, retry: false }).catch(() => null),
      ])
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
      opened = {
        store,
        outbox,
        bundle,
        rejectedBefore: analysis?.proposals.rejected ?? 0,
        actionsRejectedBefore: analysis?.actions?.suggestions.rejected ?? 0,
      }
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
  }, [client, video.id, taxonomy.sport, attempt])

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
  return (
    <Desk
      video={video}
      bundle={session.bundle}
      taxonomy={taxonomy}
      store={session.store}
      outbox={session.outbox}
      rejectedBefore={session.rejectedBefore}
      actionsRejectedBefore={session.actionsRejectedBefore}
      narrow={narrow}
    />
  )
}

interface WorkspaceProps {
  video: Video
  bundle: Tagging
  taxonomy: Taxonomy
}

interface DeskProps extends WorkspaceProps {
  store: TaggingStore
  outbox: Outbox
  rejectedBefore: number
  actionsRejectedBefore: number
  narrow: boolean
}

/**
 * Leaving the review after changes: once they are sent, Nomal scores the match again with them
 * (who served, who won, sets, breaks) and the page takes over the new suggestions. Nothing to
 * wait for: a failure (or a sport without scoring, 409) leaves things as they are.
 */
function scoreAgain(client: QueryClient, outbox: Outbox, store: TaggingStore, videoId: string): void {
  void outbox
    .flush()
    .then(() => api.scoring(videoId))
    .then(async (analysis) => {
      client.setQueryData(keys.analysis(videoId), analysis)
      store.replaceBase(await api.tagging(videoId))
    })
    .catch(() => {})
}

function Desk({ video, bundle, taxonomy, store, outbox, rejectedBefore, actionsRejectedBefore, narrow }: DeskProps) {
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [playhead] = useState(() => new Playhead())
  const [controller] = useState(() => new TagController(store, playhead, taxonomy, { duration_ms: video.duration_ms }))
  const [review] = useState(
    () => new ReviewController(store, playhead, controller, { rejectedBefore, onLeave: () => scoreAgain(client, outbox, store, video.id) }),
  )
  const [actions] = useState(
    () => new ActionReviewController(store, playhead, controller, { rejectedBefore: actionsRejectedBefore, onLeave: () => review.stop() }),
  )
  const [element, setElement] = useState<HTMLVideoElement | null>(null)
  const reload = useCallback(() => {
    api
      .tagging(video.id)
      .then((fresh) => store.replaceBase(fresh))
      .catch(() => {})
  }, [store, video.id])
  const parts = useMemo<WorkspaceParts>(
    () => ({ video, bundle, taxonomy, store, outbox, playhead, controller, review, actions, narrow, reload }),
    [video, bundle, taxonomy, store, outbox, playhead, controller, review, actions, narrow, reload],
  )
  const reviewing = useSyncedReview(review)

  useEffect(() => {
    playhead.attach(element)
    return () => playhead.detach()
  }, [playhead, element])

  useEffect(() => review.attach(), [review])
  useEffect(() => actions.attach(), [actions])
  // Leaving the review leaves its actions queue too.
  useEffect(
    () =>
      review.subscribe(() => {
        if (!review.getState().active) actions.stop()
      }),
    [review, actions],
  )

  // One keyboard at a time: the review's keys while reviewing (its actions queue's when that is
  // on), the tagging keys otherwise.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!review.getState().active) controller.onKeyDown(event)
      else if (actions.getState().active) actions.onKeyDown(event)
      else review.onKeyDown(event)
    }
    window.addEventListener('keydown', onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true })
  }, [controller, review, actions])

  // ?mode=review (the video page's "Review suggestions") opens the review; a narrow screen has only it.
  const wantsReview = narrow || params.get('mode') === 'review'
  const wantsActions = params.get('queue') === 'actions'
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    if (wantsReview) review.start()
    if (wantsReview && wantsActions) actions.start()
  }, [review, actions, wantsReview, wantsActions])
  const actionQueue = useSyncedActions(actions)
  // The queue stays in the address too.
  useEffect(() => {
    if (actionQueue === (params.get('queue') === 'actions')) return
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (actionQueue) next.set('queue', 'actions')
        else next.delete('queue')
        return next
      },
      { replace: true },
    )
  }, [actionQueue, params, setParams])
  // The mode stays in the address, so a reload comes back to it.
  useEffect(() => {
    if (narrow || reviewing === (params.get('mode') === 'review')) return
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (reviewing) next.set('mode', 'review')
        else next.delete('mode')
        return next
      },
      { replace: true },
    )
  }, [narrow, reviewing, params, setParams])

  // An analysis that finishes while the page is open brings its suggestions in.
  const analysis = useAnalysis(video.id, taxonomy.sport !== 'basketball')
  const analysisStatus = analysis.data?.status ?? null
  // So do new suggested actions.
  const actionsStatus = analysis.data?.actions?.status ?? null
  const lastActions = useRef(actionsStatus)
  useEffect(() => {
    const before = lastActions.current
    lastActions.current = actionsStatus
    if (actionsStatus !== 'done' || before === null || !isBusy({ status: before })) return
    let cancelled = false
    void Promise.all([api.tagging(video.id), api.analysis(video.id).catch(() => null)])
      .then(([fresh, latest]) => {
        if (cancelled) return
        store.replaceBase(fresh)
        actions.reset(latest?.actions?.suggestions.rejected ?? 0)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [actionsStatus, store, actions, video.id])
  const lastStatus = useRef(analysisStatus)
  useEffect(() => {
    const before = lastStatus.current
    lastStatus.current = analysisStatus
    if (analysisStatus !== 'done' || before === null || !isBusy({ status: before })) return
    let cancelled = false
    void Promise.all([api.tagging(video.id), api.analysis(video.id).catch(() => null)])
      .then(([fresh, latest]) => {
        if (cancelled) return
        store.replaceBase(fresh)
        review.reset(latest?.proposals.rejected ?? 0)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [analysisStatus, store, review, video.id])

  return (
    <WorkspaceContext value={parts}>
      <DeskLayout element={element} setElement={setElement} />
    </WorkspaceContext>
  )
}

/** Whether the actions queue of the review is on, as React state. */
function useSyncedActions(actions: ActionReviewController): boolean {
  const [active, setActive] = useState(() => actions.getState().active)
  useEffect(() => {
    const update = () => setActive(actions.getState().active)
    update()
    return actions.subscribe(update)
  }, [actions])
  return active
}

/** Whether the review is on, as React state. */
function useSyncedReview(review: ReviewController): boolean {
  const [active, setActive] = useState(() => review.getState().active)
  useEffect(() => {
    const update = () => setActive(review.getState().active)
    update()
    return review.subscribe(update)
  }, [review])
  return active
}

function DeskLayout({
  element,
  setElement,
}: {
  element: HTMLVideoElement | null
  setElement: (element: HTMLVideoElement | null) => void
}) {
  const { t } = useTranslation()
  const { video, controller, narrow, reload } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const sync = useSync()
  const review = useReview()
  const actionReview = useActionReview()
  const reviewing = review.active
  const reviewingActions = reviewing && actionReview.active

  const rallies = useMemo(() => ralliesOf(view.segments), [view.segments])
  const trajectories = useTrajectories(video.id, controller.playsRallies)
  const windows = useMemo(() => playWindows(rallies, { duration_ms: video.duration_ms }), [rallies, video.duration_ms])
  // While reviewing, the suggestion on screen plays in a loop instead.
  useCondensedPlayback(element, windows, ui.skip && !reviewing)
  const current = reviewing && !reviewingActions ? (view.segments.find((s) => s.id === review.currentId) ?? null) : null
  // An action loops around its contact: 1 s before it to 0.5 s after it.
  const action = reviewingActions ? (view.events.find((e) => e.id === actionReview.currentId) ?? null) : null
  useLoopPlayback(element, current?.start_ms ?? action?.t_ms ?? null, current?.end_ms ?? action?.t_ms ?? null)

  // The analysis card counts rally proposals ("rallies reviewed" waits for those only); the
  // review goes through the breaks Nomal labelled too.
  const rallyProgress = useMemo(() => reviewProgress(view.segments, review.seen, review.rejectedBefore), [view.segments, review.seen, review.rejectedBefore])
  const live = useMemo(() => ({ total: rallyProgress.total, open: rallyProgress.total - rallyProgress.done }), [rallyProgress])
  const progress = useMemo(
    () => allProgress(view.segments, review.seen, review.seenGaps, review.rejectedBefore),
    [view.segments, review.seen, review.seenGaps, review.rejectedBefore],
  )
  const byHand = rallies.length - suggestionCount(view.segments)
  const actionCount = useMemo(() => actionItems(view.events).length, [view.events])
  const openActions = useMemo(() => suggestedCount(view.events), [view.events])
  // The review is offered once Nomal suggested something (volleyball: basketball marks no rallies).
  const canReview = controller.playsRallies && (progress.total > 0 || actionCount > 0 || reviewing)
  const status = controller.playsRallies && video.can_tag ? <AnalysisStatus video={video} rallies={byHand} live={live} onReviewed={reload} /> : null

  // Without IndexedDB, unsent changes are lost with the page: ask before leaving.
  useEffect(() => {
    if (sync.persistent || sync.pending === 0) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [sync.persistent, sync.pending])

  return (
    <div className="flex flex-col gap-4">
      <title>{`${t(reviewing ? 'tagging.review.title' : 'tagging.pageTitle', { title: video.title })}${reviewing ? ` · ${video.title}` : ''} · nomal`}</title>
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
        <div className="flex flex-wrap items-center gap-2">
          {canReview && !narrow && <ModeSwitch open={progress.total - progress.done + openActions} />}
          <SyncStatus />
          <ButtonLink to={`/videos/${video.id}`}>{t('tagging.back')}</ButtonLink>
        </div>
      </header>
      <SyncNotes />
      {narrow && <p className="text-sm text-muted">{t('tagging.review.phoneHint')}</p>}
      {/* While reviewing, the suggestion comes first; the analysis and "rallies reviewed" go below. */}
      {!reviewing && status}

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        {/* The video stays short enough to keep the sets bar and the timeline on screen. */}
        <div className={cx('flex min-w-0 flex-col gap-2', !narrow && 'max-w-[calc((100dvh-27rem)*16/9)] flex-[100_1_0%]')}>
          <VideoPlayer
            video={video}
            videoRef={setElement}
            keyboard={false}
            onFrameRate={controller.setFps}
            overlay={trajectories.available && trajectories.on ? <TrajectoryLayer state={trajectories} video={element} /> : undefined}
          />
          <Transport tools={<TrajectoryToggle state={trajectories} />} />
        </div>
        {/* About 22rem until the video reaches its height limit; then the panel takes the rest. */}
        {reviewing ? (
          <div className={cx('flex flex-col gap-2', !narrow && 'min-w-80 flex-[1_0_22rem]')}>
            {actionCount > 0 && <QueueSwitch rallies={progress.total - progress.done} actions={openActions} />}
            {reviewingActions ? <ActionReviewPanel /> : <ReviewPanel />}
          </div>
        ) : (
          <aside className="flex min-w-80 flex-[1_0_22rem] flex-col gap-2" aria-label={t('tagging.panel')}>
            <StatePanel />
            <EntryPanel />
            <RosterPanel />
          </aside>
        )}
      </div>
      {!reviewing && <SetsBar />}
      <Timeline />
      {!reviewing && ui.segmentId && <SegmentPanel key={ui.segmentId} id={ui.segmentId} />}
      {reviewingActions ? <EventTable /> : reviewing ? <RallyList mode="review" /> : <Lists rallies={rallies.length} />}
      {reviewing && status}
      <Cheatsheet />
      <WorkspaceClipDialog />
    </div>
  )
}

/** Tag or review: the review goes through Nomal's suggestions one by one. */
function ModeSwitch({ open }: { open: number }) {
  const { t } = useTranslation()
  const { review } = useWorkspace()
  const state = useReview()
  return (
    <Segmented
      label={t('tagging.mode.label')}
      value={state.active ? 'review' : 'tag'}
      options={[
        { value: 'tag', label: t('tagging.mode.tag') },
        { value: 'review', label: open > 0 ? t('tagging.mode.reviewCount', { count: open }) : t('tagging.mode.review') },
      ]}
      onChange={(value, event) => {
        if (value === 'review') review.start()
        else review.stop()
        // A pointer click must not leave Enter on this button: Enter answers the review.
        if (event.detail > 0) event.currentTarget.blur()
      }}
    />
  )
}

/** In the review: Nomal's rallies (and breaks) or its suggested actions. */
function QueueSwitch({ rallies, actions: open }: { rallies: number; actions: number }) {
  const { t } = useTranslation()
  const { actions } = useWorkspace()
  const state = useActionReview()
  return (
    <Segmented
      label={t('actions.queue.label')}
      value={state.active ? 'actions' : 'rallies'}
      options={[
        { value: 'rallies', label: t('actions.queue.rallies', { count: rallies }) },
        { value: 'actions', label: t('actions.queue.actions', { count: open }) },
      ]}
      onChange={(value, event) => {
        if (value === 'actions') actions.start()
        else actions.stop()
        // A pointer click must not leave Enter on this button: Enter answers the review.
        if (event.detail > 0) event.currentTarget.blur()
      }}
    />
  )
}

/** Below the timeline: the tags, or (volleyball) the rallies with Nomal's suggestions. */
function Lists({ rallies }: { rallies: number }) {
  const { t } = useTranslation()
  const { controller } = useWorkspace()
  const view = useView()
  const [list, setList] = useState<'tags' | 'rallies'>('tags')
  if (!controller.playsRallies) return <EventTable />
  const lists = (
    <Segmented
      label={t('tagging.lists.label')}
      value={list}
      options={[
        { value: 'tags', label: t('tagging.lists.tags', { count: view.events.length }) },
        { value: 'rallies', label: t('tagging.lists.rallies', { count: rallies }) },
      ]}
      onChange={(value) => setList(value)}
    />
  )
  if (list === 'rallies') return <RallyList mode="tag" toolbar={lists} />
  return (
    <div className="flex flex-col gap-3">
      <div>{lists}</div>
      <EventTable />
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

function Transport({ tools }: { tools?: ReactNode }) {
  const { t } = useTranslation()
  const { controller, playhead, video, review: reviewer, narrow } = useWorkspace()
  const ui = useUi()
  const view = useView()
  const reviewing = useReview().active
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
      {/* Dead time is what lies between rallies; basketball marks none (yet). Reviewing loops one rally. */}
      {controller.playsRallies && !reviewing && (
        <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold">
          <Switch checked={ui.skip} onChange={(on) => controller.toggleSkip(on)} label={t('tagging.condensed.skip')} />
          <span aria-hidden="true">{t('tagging.condensed.skip')}</span>
        </label>
      )}
      {tools}
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="icon" disabled={!view.canUndo} onClick={reviewing ? reviewer.undo : controller.undo} aria-label={t('tagging.transport.undo')} title={t('tagging.transport.undo')}>
          <UndoIcon />
        </Button>
        <Button variant="ghost" size="icon" disabled={!view.canRedo} onClick={reviewing ? reviewer.redo : controller.redo} aria-label={t('tagging.transport.redo')} title={t('tagging.transport.redo')}>
          <RedoIcon />
        </Button>
        {/* Phones and tablets review by touch: no keys to list. */}
        {!narrow && (
          <Button variant="ghost" onClick={() => controller.toggleCheatsheet(true)}>
            <KeyboardIcon />
            {t('tagging.keys.open')}
          </Button>
        )}
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
  // A suggested serve is no fact: only the coach's tags name the server.
  const serveTag = open
    ? factTags(view.events).find((e) => serves.has(e.code) && e.t_ms >= open.start_ms - SERVE_SLACK_MS && e.t_ms <= t_ms)
    : undefined
  const serving = serveTag?.side ?? state.serving
  // The score counts winners Nomal suggested (no coach decided those rallies yet).
  const suggested = !serveTag && state.suggested === true

  return (
    <section aria-label={t('tagging.state.title')} className="flex flex-col gap-1 rounded-card border border-hairline bg-surface shadow-card px-4 py-3">
      <p className="flex flex-wrap items-baseline gap-x-3 text-lg font-bold tabular-nums">
        <span>{state.period ? t('tagging.setNo', { no: state.period.no }) : t('tagging.state.noSet')}</span>
        {state.period && (
          <SuggestedValue suggested={state.suggested === true} className="text-2xl">
            {`${state.us}:${state.them}`}
          </SuggestedValue>
        )}
        <SuggestedValue suggested={suggested && serving !== null} className="text-base font-semibold">
          {serving ? t('tagging.state.serve', { side: sideLabel(t, serving) }) : t('tagging.state.serveUnknown')}
        </SuggestedValue>
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

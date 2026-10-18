import '../i18n/pages'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useAnalysis } from '../lib/analysis'
import { ApiError } from '../lib/api'
import { useCourt, type CourtState } from '../lib/court'
import { cx } from '../lib/cx'
import { useFindPlayers, useSearchTime, type PlayersSummary } from '../lib/players'
import { keys } from '../lib/queryClient'
import { useReadScoreboard, useScoreboard, type ScoreboardState } from '../lib/scoreboard'
import {
  analysisState,
  ballState,
  BUSY_JOBS,
  courtState,
  namesState,
  playersState,
  scoreboardState,
  useVideoSetup,
  type StepKey,
  type StepState,
} from '../lib/setup'
import { formatPercent } from '../lib/suggestions'
import { useToast } from '../lib/toast'
import type { Video } from '../lib/types'
import { AnalysisStatus } from './AnalysisStatus'
import { Badge } from './Badge'
import { Button, ButtonLink } from './Button'
import { CourtSetupDialog } from './CourtSetupDialog'
import { Field, TextInput } from './Field'
import { AlertIcon, ChevronDownIcon, CircleCheckIcon, CircleIcon, SlidersIcon } from './icons'
import { Modal } from './Modal'
import { ScoreboardDialog } from './ScoreboardDialog'

// --- the frame ----------------------------------------------------------------------------------

function StateIcon({ state }: { state: StepState }) {
  switch (state) {
    case 'done':
      return <CircleCheckIcon className="size-5 shrink-0 text-on-accent-soft" />
    case 'attention':
      return <AlertIcon className="size-5 shrink-0 text-link" />
    case 'busy':
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5 shrink-0 text-muted motion-safe:animate-spin">
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
          <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
      )
    default:
      return <CircleIcon className={cx('size-5 shrink-0', state === 'todo' ? 'text-fg' : 'text-muted')} />
  }
}

interface StepProps {
  title: string
  state: StepState
  /** One line on where it stands. */
  status?: ReactNode
  badge?: ReactNode
  /** The buttons. */
  actions?: ReactNode
  /** More than a line (the analysis). */
  children?: ReactNode
  wide?: boolean
}

function Step({ title, state, status, badge, actions, children, wide = false }: StepProps) {
  const { t } = useTranslation()
  return (
    <li
      data-step-state={state}
      className={cx(
        'flex min-w-0 flex-col gap-2 rounded-xl border p-3',
        wide && 'md:col-span-2',
        state === 'attention' ? 'border-accent bg-surface' : 'border-hairline bg-surface',
      )}
    >
      <div className="flex items-start gap-2.5">
        <StateIcon state={state} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
            {title}
            {badge}
            <span className={cx('text-xs font-semibold', state === 'attention' ? 'text-link' : 'text-muted')}>{t(`setup.state.${state}`)}</span>
          </h3>
          {status && <div className="text-sm text-muted">{status}</div>}
        </div>
      </div>
      {children && <div className="min-w-0 sm:pl-7.5">{children}</div>}
      {actions && <div className="flex flex-wrap gap-2 sm:pl-7.5">{actions}</div>}
    </li>
  )
}

interface VideoSetupProps {
  video: Video
  /** The video element: the court's and the scoreboard's dialogs draw its paused frame. */
  element: HTMLVideoElement | null
  /** The coach's rallies (not open suggestions) and own action tags, for the analysis. */
  rallies: number
  actionTags: number
}

/**
 * The video's setup, for those who may tag (volleyball): one card per step with where it stands
 * and what to do: the analysis (rallies and score), the ball's path, the court, the scoreboard,
 * the players (beta, on demand) and the teams' names. Steps still open come first; the
 * completed ones are grouped below. Folded away until something needs the coach, unless the
 * coach opens it.
 */
export function VideoSetup({ video, element, rallies, actionTags }: VideoSetupProps) {
  const { t } = useTranslation()
  const id = useId()
  const analysis = useAnalysis(video.id, true)
  const court = useCourt(video.id)
  const board = useScoreboard(video.id)
  useFollowReading(video.id, board.data)
  const [open, setOpen] = useState<boolean | null>(null)

  const players = analysis.data?.players ?? null
  const steps: { key: StepKey; state: StepState; node: (state: StepState) => ReactNode }[] = [
    {
      key: 'analysis',
      state: analysisState(analysis.data, video, rallies),
      node: (state) => (
        <Step key="analysis" title={t('setup.analysis.title')} state={state} wide>
          <AnalysisStatus video={video} rallies={rallies} actionTags={actionTags} reviewHref={`/videos/${video.id}/tag?mode=review`} plain />
        </Step>
      ),
    },
    { key: 'ball', state: ballState(court.data), node: (state) => <BallStep key="ball" state={state} court={court.data} /> },
    { key: 'court', state: courtState(court.data), node: (state) => <CourtStep key="court" state={state} video={video} court={court.data} element={element} /> },
    {
      key: 'scoreboard',
      state: scoreboardState(board.data, video.no_scoreboard === true),
      node: (state) => <ScoreboardStep key="scoreboard" state={state} video={video} board={board.data} element={element} />,
    },
    ...(players
      ? [{ key: 'players' as const, state: playersState(players), node: (state: StepState) => <PlayersStep key="players" state={state} video={video} players={players} canFind={analysis.data?.can_run ?? true} /> }]
      : []),
    ...(video.names ? [{ key: 'names' as const, state: namesState(video), node: (state: StepState) => <NamesStep key="names" state={state} video={video} /> }] : []),
  ]
  const todo = steps.filter((s) => s.state !== 'done')
  const done = steps.filter((s) => s.state === 'done')
  const running = steps.filter((s) => s.state === 'busy').length
  const needs = steps.filter((s) => s.state === 'attention' || s.state === 'todo').length
  // Open once something needed the coach, and it stays open while they work through it (a
  // step that starts running must not fold the card away); the coach's own choice wins.
  const [wanted, setWanted] = useState(false)
  if (needs > 0 && !wanted) setWanted(true)
  const expanded = open ?? (wanted || needs > 0)
  const summary = [
    t('setup.progress', { done: done.length, total: steps.length }),
    needs > 0 ? t('setup.needsYou', { count: needs }) : null,
    running > 0 ? t('setup.running', { count: running }) : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <section aria-labelledby={`${id}-title`} className="rounded-card border border-hairline bg-surface shadow-card">
      <h2 id={`${id}-title`} className="text-base">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={`${id}-steps`}
          onClick={() => setOpen(!expanded)}
          className="flex min-h-12 w-full cursor-pointer items-center gap-3 rounded-card px-4 py-2.5 text-left hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
        >
          <SlidersIcon className="size-5 shrink-0 text-muted" />
          <span className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-baseline sm:gap-3">
            <span className="font-semibold">{t('setup.title')}</span>
            <span className="min-w-0 text-sm font-normal text-muted tabular-nums sm:truncate">{summary}</span>
          </span>
          <ChevronDownIcon className={cx('size-5 shrink-0 text-muted transition-transform motion-reduce:transition-none', expanded && 'rotate-180')} />
        </button>
      </h2>
      {expanded && (
        <div id={`${id}-steps`} className="flex flex-col gap-4 border-t border-hairline p-3 sm:p-4">
          {todo.length > 0 && <ul className="grid gap-3 md:grid-cols-2">{todo.map((s) => s.node(s.state))}</ul>}
          {done.length > 0 && (
            <section aria-labelledby={`${id}-done`} className="flex flex-col gap-2">
              <h3 id={`${id}-done`} className="text-xs font-semibold tracking-wide text-muted uppercase">
                {`${t('setup.completed')} (${done.length})`}
              </h3>
              <ul className="grid gap-3 md:grid-cols-2">{done.map((s) => s.node(s.state))}</ul>
            </section>
          )}
        </div>
      )}
    </section>
  )
}

/** The score line changes once the board is read: fetch the tagging data and the analysis again then. */
function useFollowReading(videoId: string, board: ScoreboardState | undefined) {
  const client = useQueryClient()
  const status = board?.job?.status
  const last = useRef(status)
  useEffect(() => {
    if (last.current && BUSY_JOBS.includes(last.current) && status === 'done') {
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
    }
    last.current = status
  }, [status, client, videoId])
}

// --- the steps ----------------------------------------------------------------------------------

function BallStep({ state, court }: { state: StepState; court: CourtState | undefined }) {
  const { t } = useTranslation()
  const status = state === 'done' ? t('setup.ball.done') : state === 'busy' ? t('setup.ball.busy') : state === 'attention' ? t('setup.ball.failed') : t('setup.ball.none')
  return <Step title={t('setup.ball.title')} state={state} status={<p role="status">{court ? status : null}</p>} />
}

function CourtStep({ state, video, court, element }: { state: StepState; video: Video; court: CourtState | undefined; element: HTMLVideoElement | null }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const calibration = court?.calibration ?? null
  const status = calibration
    ? calibration.checked === false
      ? t('setup.court.auto')
      : t('setup.court.done')
    : state === 'busy'
      ? t('setup.court.busy')
      : t('setup.court.none')
  const label = calibration ? (calibration.checked === false ? t('setup.court.check') : t('setup.court.edit')) : t('setup.court.open')
  return (
    <Step
      title={t('setup.court.title')}
      state={state}
      status={<p role="status">{court ? status : null}</p>}
      actions={
        court?.can_edit ? (
          <>
            <Button variant={state === 'attention' || state === 'todo' ? 'primary' : 'secondary'} onClick={() => setOpen(true)} aria-haspopup="dialog">
              {label}
            </Button>
            {open && <CourtSetupDialog open videoId={video.id} state={court} element={element} onClose={() => setOpen(false)} />}
          </>
        ) : null
      }
    />
  )
}

function ScoreboardStep({ state, video, board, element }: { state: StepState; video: Video; board: ScoreboardState | undefined; element: HTMLVideoElement | null }) {
  const { t } = useTranslation()
  const toast = useToast()
  const again = useReadScoreboard(video.id)
  const setup = useVideoSetup(video.id)
  const [open, setOpen] = useState(false)
  if (!board) return <Step title={t('setup.scoreboard.title')} state={state} />
  const job = board.job?.status
  const segments = board.reading?.segments.length ?? 0
  const status = !board.region
    ? video.no_scoreboard
      ? t('setup.scoreboard.absent')
      : t('setup.scoreboard.none')
    : job && BUSY_JOBS.includes(job)
      ? board.job?.progress
        ? t('scoreboard.readingProgress', { pct: Math.floor(board.job.progress * 100) })
        : t('scoreboard.reading')
      : job === 'failed'
        ? t('scoreboard.readFailed')
        : board.reading
          ? segments > 0
            ? t('scoreboard.read', { count: segments })
            : t('scoreboard.readNothing')
          : t('setup.scoreboard.waiting')
  return (
    <Step
      title={t('setup.scoreboard.title')}
      state={state}
      status={<p role="status">{status}</p>}
      actions={
        board.can_edit ? (
          <>
            <Button variant={state === 'todo' ? 'primary' : 'secondary'} onClick={() => setOpen(true)} aria-haspopup="dialog">
              {board.region ? t('setup.scoreboard.edit') : t('setup.scoreboard.open')}
            </Button>
            {board.region && job === 'failed' && (
              <Button variant="ghost" onClick={() => again.mutate(undefined)} disabled={again.isPending}>
                {t('scoreboard.readAgain')}
              </Button>
            )}
            {!board.region && !video.no_scoreboard && (
              <Button
                variant="ghost"
                disabled={setup.isPending}
                onClick={() =>
                  setup.mutate(
                    { no_scoreboard: true },
                    { onError: (error) => toast.error(t('scoreboard.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })) },
                  )
                }
              >
                {t('setup.scoreboard.noBoard')}
              </Button>
            )}
            {open && <ScoreboardDialog video={video} state={board} element={element} onClose={() => setOpen(false)} />}
          </>
        ) : null
      }
    />
  )
}

function PlayersStep({ state, video, players, canFind }: { state: StepState; video: Video; players: PlayersSummary; canFind: boolean }) {
  const { t } = useTranslation()
  const find = useFindPlayers(video.id)
  const time = useSearchTime(players.find_minutes)
  const done = players.run_id !== null
  const status =
    state === 'attention'
      ? t('players.step.failed')
      : state === 'busy'
        ? t('players.step.busy', { time })
        : done
          ? t('players.step.found', { total: players.clusters, us: players.clusters_us, them: players.clusters_them, named: players.named })
          : t('players.step.none', { time })
  return (
    <Step
      title={t('setup.players.title')}
      badge={<Badge>{t('players.beta')}</Badge>}
      state={state}
      status={
        <>
          <p role="status">{status}</p>
          {done && players.actions > 0 && <p className="font-semibold text-fg tabular-nums">{t('players.step.identified', { value: formatPercent(players.identified_share) })}</p>}
        </>
      }
      actions={
        done ? (
          <ButtonLink to={`/videos/${video.id}/players`} variant={players.named < players.clusters ? 'primary' : 'secondary'}>
            {t('players.step.open')}
          </ButtonLink>
        ) : state === 'busy' || !canFind ? null : (
          <Button variant="secondary" disabled={find.isPending} title={t('players.findHint', { time })} onClick={() => find.mutate(undefined)}>
            {t('players.find', { time })}
          </Button>
        )
      }
    />
  )
}

function NamesStep({ state, video }: { state: StepState; video: Video }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const names = video.names
  if (!names) return null
  const from = names.them_from === 'set' || names.us_set ? t('setup.names.fromYou') : names.them_from === 'game' ? t('setup.names.fromGame') : names.them_from === 'title' ? t('setup.names.fromTitle') : null
  return (
    <Step
      title={t('setup.names.title')}
      state={state}
      status={
        <>
          <p className="font-semibold text-fg" title={`${names.us_full} – ${names.them_full ?? t('sides.opponent')}`}>
            {`${names.us} – ${names.them ?? t('sides.opponent')}`}
          </p>
          <p role="status">{names.them ? from : t('setup.names.unknown')}</p>
        </>
      }
      actions={
        video.can_tag ? (
          <>
            <Button variant={state === 'todo' ? 'primary' : 'secondary'} onClick={() => setOpen(true)} aria-haspopup="dialog">
              {t('setup.names.edit')}
            </Button>
            {open && <NamesDialog video={video} onClose={() => setOpen(false)} />}
          </>
        ) : null
      }
    />
  )
}

/** The two names a coach may set for this video; empty goes back to the derived one. */
function NamesDialog({ video, onClose }: { video: Video; onClose: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const setup = useVideoSetup(video.id)
  const names = video.names
  const [us, setUs] = useState(names?.us_set ?? '')
  const [them, setThem] = useState(names?.them_set ?? '')
  if (!names) return null
  // What an empty field stands for: the derived name (the server's, without the coach's).
  const derivedUs = names.us_set ? null : names.us
  const derivedThem = names.them_set ? null : names.them
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setup.mutate(
      { name_us: us.trim() || null, name_them: them.trim() || null },
      {
        onSuccess: () => {
          toast.success(t('setup.names.saved'))
          onClose()
        },
        onError: (error) => toast.error(t('setup.names.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
      },
    )
  }
  return (
    <Modal open title={t('setup.names.dialogTitle')} onClose={onClose}>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <p className="text-sm text-muted">{t('setup.names.intro')}</p>
        <Field
          label={t('setup.names.us')}
          htmlFor={`${id}-us`}
          hint={[derivedUs ? t('setup.names.derived', { name: derivedUs }) : null, t('setup.names.official', { name: names.us_full })].filter(Boolean).join(' · ')}
        >
          <TextInput id={`${id}-us`} value={us} maxLength={40} placeholder={derivedUs ?? undefined} onChange={(e) => setUs(e.target.value)} />
        </Field>
        <Field
          label={t('setup.names.them')}
          htmlFor={`${id}-them`}
          hint={[derivedThem ? t('setup.names.derived', { name: derivedThem }) : null, names.them_full ? t('setup.names.official', { name: names.them_full }) : null]
            .filter(Boolean)
            .join(' · ')}
        >
          <TextInput id={`${id}-them`} value={them} maxLength={40} placeholder={derivedThem ?? t('sides.opponent')} onChange={(e) => setThem(e.target.value)} />
        </Field>
        <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="submit" variant="primary" disabled={setup.isPending}>
            {t('setup.names.save')}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

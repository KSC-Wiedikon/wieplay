import type { TFunction } from 'i18next'
import { useMemo, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { AlertIcon, CheckIcon, ChevronDownIcon, ChevronUpIcon, CloseIcon, MergeIcon, SparkleIcon, SplitIcon, UndoIcon } from '../../components/icons'
import { EmptyState } from '../../components/Message'
import { ProgressBar } from '../../components/ProgressBar'
import { SuggestionChip } from '../../components/SuggestionChip'
import { useSetRalliesReviewed } from '../../lib/analysis'
import { cx } from '../../lib/cx'
import { formatClock, scoreNoteText, segmentKindLabel, sideLabel } from '../../lib/labels'
import { periodAt } from '../../lib/rallies'
import { fullNameOf, useSideNames, type SideNames } from '../../lib/sides'
import {
  ACCEPT_ALL_MIN,
  allProgress,
  effectiveServer,
  effectiveWinner,
  formatPercent,
  formatSeconds,
  GAP_KINDS,
  isGap,
  isOpen,
  logCounts,
  logMatches,
  reviewItems,
  scoreAfter,
  sureSuggestions,
  type LogVerdict,
} from '../../lib/suggestions'
import type { GapKind, Segment, Side } from '../../lib/types'
import { useReview, useView, useWorkspace } from './context'
import { GuideLink } from './GuideLink'
import { GAP_KEYS, type ReviewNotice } from './review'

/**
 * A pointer click leaves the focus on the button, where Enter and Space would press it again;
 * give the keys back to the review instead. Keyboard presses keep their focus.
 */
function press(action: () => void) {
  return (event: MouseEvent<HTMLButtonElement>) => {
    action()
    if (event.detail > 0) event.currentTarget.blur()
  }
}

function Kbd({ children, hidden }: { children: ReactNode; hidden?: boolean }) {
  return (
    <kbd
      aria-hidden="true"
      className={cx(
        'rounded-md border border-current/25 px-1 py-px font-mono text-[11px] leading-4 font-semibold opacity-80',
        hidden && 'hidden',
      )}
    >
      {children}
    </kbd>
  )
}

/** What the scoreboard log leaves for the coach, in this order. */
const LOG_LEFT = ['none', 'disagree', 'missed', 'silent'] as const satisfies readonly Exclude<LogVerdict, 'match'>[]

/** "15 with no point on the log, 11 where the log and WiePlay disagree, …" (the counts above 0). */
function logLeftText(t: TFunction, counts: Partial<Record<LogVerdict, number | string>>): string {
  return LOG_LEFT.filter((key) => Number(counts[key] ?? 0) > 0)
    .map((key) => t(`tagging.review.logLeft.${key}`, { count: Number(counts[key]) }))
    .join(', ')
}

/** The key of each break label (W B T D O). */
const KEY_OF = Object.fromEntries(Object.entries(GAP_KEYS).map(([key, kind]) => [kind, key.toUpperCase()])) as Record<GapKind, string>

function noticeText(t: TFunction, notice: ReviewNotice, sport: string, names: SideNames | null): string {
  const p = notice.params ?? {}
  const time = p.time === undefined ? '' : formatClock(Number(p.time))
  switch (notice.key) {
    case 'accepted':
    case 'rejected':
    case 'split':
      return t(`tagging.review.notice.${notice.key}`, { time })
    case 'gapAccepted':
    case 'gapRejected':
    case 'relabelled':
      return t(`tagging.review.notice.${notice.key}`, { time, kind: segmentKindLabel(t, p.kind as GapKind, sport) })
    case 'serve':
      return t('tagging.review.notice.serve', { side: sideLabel(t, p.side as Side, names) })
    case 'won':
      return t('tagging.review.notice.won', { side: sideLabel(t, p.side as Side, names) })
    case 'moved':
      return p.field === 'end' ? t('tagging.review.notice.movedEnd', { time }) : t('tagging.review.notice.movedStart', { time })
    case 'acceptedAll':
      return t('tagging.review.notice.acceptedAll', { count: Number(p.count) })
    case 'acceptedLog': {
      const accepted = t('tagging.review.notice.acceptedLog', { count: Number(p.count) })
      const list = logLeftText(t, p)
      return list ? `${accepted} ${t('tagging.review.notice.acceptedLogLeft', { left: Number(p.left), list })}` : accepted
    }
    case 'done':
      return t('tagging.review.doneTitle')
    default:
      return t(`tagging.review.notice.${notice.key}`)
  }
}

/**
 * The review of WiePlay's suggestions (PLAN §10.1): the suggestion on screen, its boundaries,
 * the answers as big buttons (tablets) with their keys, the progress, "accept all that match
 * the scoreboard log" (with a trusted log) and "accept all sure ones".
 */
export function ReviewPanel() {
  const { t } = useTranslation()
  const { review, video, narrow, taxonomy } = useWorkspace()
  const names = useSideNames()
  const view = useView()
  const state = useReview()
  const items = useMemo(() => reviewItems(view.segments, state.pending), [view.segments, state.pending])
  const progress = useMemo(
    () => allProgress(view.segments, state.seen, state.seenGaps, state.rejectedBefore),
    [view.segments, state.seen, state.seenGaps, state.rejectedBefore],
  )
  const sure = useMemo(() => sureSuggestions(view.segments), [view.segments])
  // Only with a trusted scoreboard log (PLAN §10.10): the server sends its answers only there.
  const matches = useMemo(() => logMatches(view.segments), [view.segments])
  const logLeft = useMemo(() => logLeftText(t, logCounts(view.segments)), [t, view.segments])
  const current = items.find((s) => s.id === state.currentId) ?? null
  const gap = current !== null && isGap(current.kind)
  const set = current ? periodAt(view.periods, current.start_ms) : null
  const min = formatPercent(ACCEPT_ALL_MIN)
  const progressText = t('tagging.review.progressLabel', { done: progress.done, total: progress.total })

  return (
    <section aria-labelledby="review-title" className="flex flex-col gap-3 rounded-card border border-hairline bg-surface p-4 shadow-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="review-title" className="flex items-center gap-2 font-semibold">
          <SparkleIcon className="size-4" />
          {t('tagging.review.title')}
        </h2>
        <p aria-live="polite" aria-atomic="true" className="text-lg font-bold tabular-nums">
          <span aria-hidden="true">{t('tagging.review.progress', { done: progress.done, total: progress.total })}</span>
          <span className="sr-only">{progressText}</span>
        </p>
      </div>
      {progress.total > 0 && <ProgressBar value={progress.done / progress.total} label={progressText} />}

      {current ? (
        <>
          <div
            className={cx(
              'flex flex-col gap-2 rounded-xl border-2 p-3',
              isOpen(current, state.pending) ? 'border-dashed border-accent bg-accent-soft/40' : 'border-hairline bg-sunken',
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold tabular-nums">
                {gap
                  ? t('tagging.review.currentGap', { kind: segmentKindLabel(t, current.kind, taxonomy.sport), time: formatClock(current.start_ms) })
                  : t('tagging.review.current', { time: formatClock(current.start_ms) })}
                <span className="font-normal text-muted">
                  {` · ${formatSeconds(current.end_ms - current.start_ms)}`}
                  {set && ` · ${t('tagging.review.set', { no: set.no })}`}
                </span>
              </h3>
              <SuggestionChip segment={current} splitOff={state.pending.has(current.id)} />
            </div>
            <div className="grid gap-2 min-[28rem]:grid-cols-2">
              {(['start_ms', 'end_ms'] as const).map((field) => {
                const name = field === 'start_ms' ? t('tagging.review.start') : t('tagging.review.end')
                return (
                  <div key={field} className="flex flex-col gap-1">
                    <span className="text-xs font-semibold text-muted">
                      {name}
                      {!narrow && <span className="ml-1 font-normal">{field === 'start_ms' ? '← →' : '⇧ ← →'}</span>}
                    </span>
                    <span className="flex items-center gap-1">
                      <Button size="touchIcon" aria-label={t('tagging.review.earlier', { field: name })} onClick={press(() => review.nudge(field, -1))}>
                        <span aria-hidden="true">−</span>
                      </Button>
                      <span className="min-w-0 flex-1 text-center font-mono text-sm tabular-nums">{formatClock(current[field])}</span>
                      <Button size="touchIcon" aria-label={t('tagging.review.later', { field: name })} onClick={press(() => review.nudge(field, 1))}>
                        <span aria-hidden="true">+</span>
                      </Button>
                    </span>
                  </div>
                )
              })}
            </div>
            {gap ? <GapLabels gap={current} /> : <RallyFacts rally={current} />}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Button variant="primary" size="touch" onClick={press(review.accept)}>
              <CheckIcon className="size-4" />
              {t('tagging.review.accept')}
              <Kbd hidden={narrow}>Enter</Kbd>
            </Button>
            <Button variant="danger" size="touch" onClick={press(review.reject)}>
              <CloseIcon className="size-4" />
              {t('tagging.review.reject')}
              <Kbd hidden={narrow}>X</Kbd>
            </Button>
            {/* A break is not merged or split: it is labelled (above). */}
            {!gap && (
              <>
                <Button size="touch" onClick={press(review.merge)}>
                  <MergeIcon className="size-4" />
                  {t('tagging.review.merge')}
                  <Kbd hidden={narrow}>M</Kbd>
                </Button>
                <Button size="touch" onClick={press(review.split)}>
                  <SplitIcon className="size-4" />
                  {t('tagging.review.split')}
                  <Kbd hidden={narrow}>S</Kbd>
                </Button>
              </>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <Button variant="ghost" size="touch" disabled={!view.canUndo} onClick={press(review.undo)}>
              <UndoIcon className="size-4" />
              {t('tagging.review.undo')}
              <Kbd hidden={narrow}>U</Kbd>
            </Button>
            <span className="ml-auto flex gap-1">
              <Button variant="ghost" size="touchIcon" aria-label={t('tagging.review.previous')} title={t('tagging.review.previous')} onClick={press(() => review.move(-1))}>
                <ChevronUpIcon />
              </Button>
              <Button variant="ghost" size="touchIcon" aria-label={t('tagging.review.next')} title={t('tagging.review.next')} onClick={press(() => review.move(1))}>
                <ChevronDownIcon />
              </Button>
            </span>
          </div>
        </>
      ) : progress.total > 0 ? (
        <Done videoId={video.id} reviewed={Boolean(video.rallies_reviewed_at)} />
      ) : (
        <EmptyState icon={<SparkleIcon />} title={t('tagging.review.emptyTitle')}>
          {t('tagging.review.emptyBody')}
        </EmptyState>
      )}

      <p role="status" className={cx('min-h-5 text-sm', state.notice?.tone === 'error' ? 'text-danger' : 'text-muted')}>
        {state.notice ? noticeText(t, state.notice, taxonomy.sport, names) : t('tagging.review.hint')}
      </p>

      {(matches.length > 0 || sure.length > 0) && (
        <div className="flex flex-col gap-2 border-t border-hairline pt-3">
          {matches.length > 0 && (
            <Button size="touch" className="w-full whitespace-normal" onClick={press(review.askAcceptLog)}>
              <CheckIcon className="size-4" />
              {t('tagging.review.acceptLog', { count: matches.length })}
            </Button>
          )}
          {sure.length > 0 && (
            <Button size="touch" className="w-full whitespace-normal" onClick={press(review.askAcceptAll)}>
              <CheckIcon className="size-4" />
              {t('tagging.review.acceptAll', { min, count: sure.length })}
            </Button>
          )}
        </div>
      )}

      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}
      <GuideLink />

      <ConfirmDialog
        open={state.confirmAll}
        title={t('tagging.review.acceptAllTitle', { count: sure.length })}
        confirmLabel={t('tagging.review.acceptAllConfirm', { count: sure.length })}
        onCancel={review.cancelAcceptAll}
        onConfirm={review.confirmAcceptAll}
      >
        {t('tagging.review.acceptAllBody', { min })}
      </ConfirmDialog>
      <ConfirmDialog
        open={state.confirmLog}
        title={t('tagging.review.acceptLogTitle', { count: matches.length })}
        confirmLabel={t('tagging.review.acceptLogConfirm', { count: matches.length })}
        onCancel={review.cancelAcceptLog}
        onConfirm={review.confirmAcceptLog}
      >
        {t('tagging.review.acceptLogBody')}
        {logLeft && ` ${t('tagging.review.acceptLogLeft', { list: logLeft })}`}
      </ConfirmDialog>
    </section>
  )
}

/** "(suggested, 87%, near court)": what the model adds to a suggested value. */
function suggestedDetail(t: TFunction, parts: (string | null | undefined)[]): string {
  return ` (${[t('tagging.scoring.suggested'), ...parts].filter(Boolean).join(', ')})`
}

/**
 * Who served, who won and the score after the rally, by the teams' names: the coach's facts,
 * else WiePlay's suggestions; "check" with the reason when the automatic score disagrees with
 * something here. 1 and 2 call the serving team, 3 and 4 the winner (a fact the scoring keeps
 * to: the score line follows at once).
 */
function RallyFacts({ rally }: { rally: Segment }) {
  const { t } = useTranslation()
  const { review, narrow } = useWorkspace()
  const names = useSideNames()
  const server = effectiveServer(rally)
  const winner = effectiveWinner(rally)
  const score = scoreAfter(rally)
  const call = rally.serving_manual ?? null
  const won = rally.won_by ?? null
  const yours = ` (${t('tagging.review.yourCall')})`
  const serveText = server.value
    ? `${sideLabel(t, server.value, names)}${
        server.suggested
          ? suggestedDetail(t, [formatPercent(rally.serve_confidence), rally.serving_court ? t(`tagging.scoring.${rally.serving_court}`) : null])
          : call === server.value
            ? yours
            : ''
      }`
    : t('tagging.review.unknown')
  // The club scoreboard's log (docs/PLAN.md §10.10): the point pressed after the rally.
  const logSays = rally.log_won_by ?? null
  const fromLog = winner.suggested && logSays !== null && logSays === winner.value
  const wonText = winner.value
    ? `${sideLabel(t, winner.value, names)}${
        winner.suggested
          ? suggestedDetail(t, [fromLog ? t('tagging.scoring.log.label') : formatPercent(rally.score_confidence)])
          : won === winner.value
            ? yours
            : ''
      }`
    : t('tagging.review.unknown')
  const logScore = rally.log_score_us != null && rally.log_score_them != null ? `${rally.log_score_us}:${rally.log_score_them}` : null
  const logText =
    logSays === null
      ? null
      : logSays === 'none'
        ? t('tagging.scoring.log.none')
        : t(logScore ? 'tagging.scoring.log.pointScore' : 'tagging.scoring.log.point', { team: sideLabel(t, logSays, names), score: logScore })
  const logDelay = rally.log_at_ms != null ? Math.round((rally.log_at_ms - rally.end_ms) / 100) / 10 : null
  const scoreText = score
    ? `${names ? `${names.us} ${score.text} ${names.them}` : score.text}${score.suggested ? suggestedDetail(t, []) : ''}`
    : '–'
  const rows: [string, string, boolean][] = [
    [t('tagging.review.serve'), serveText, server.suggested],
    [t('tagging.review.won'), wonText, winner.suggested],
    [t('tagging.review.scoreAfter'), scoreText, score?.suggested ?? false],
  ]
  const label = (side: Side) => sideLabel(t, side, names)
  return (
    <div className="flex flex-col gap-2 border-t border-hairline pt-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        {rows.map(([name, value, suggested]) => (
          <div key={name} className="contents">
            <dt className="font-semibold text-muted">{name}</dt>
            <dd className={cx('min-w-0 wrap-break-word tabular-nums', suggested && 'text-muted italic')}>{value}</dd>
          </div>
        ))}
      </dl>
      {logText && (
        <p data-log={logSays ?? undefined} className="text-sm text-muted tabular-nums" title={logDelay !== null ? t('tagging.scoring.log.title', { delay: logDelay }) : undefined}>
          {logText}
        </p>
      )}
      {rally.score_conflict && (
        <p data-check="true" className="flex items-start gap-1.5 text-sm font-semibold text-danger">
          <AlertIcon className="mt-0.5 size-4 shrink-0" />
          {t('tagging.scoring.checkReason', { reason: scoreNoteText(t, rally.score_note) })}
        </p>
      )}
      <CallButtons
        label={t('tagging.review.whoServed')}
        value={call}
        keys={['1', '2']}
        name={(side) => t('tagging.review.serves', { team: label(side) })}
        text={label}
        title={(side) => (names ? fullNameOf(names, side) : undefined)}
        onPick={review.serve}
        narrow={narrow}
      />
      <CallButtons
        label={t('tagging.review.whoWon')}
        value={won}
        keys={['3', '4']}
        name={(side) => t('tagging.review.pointTo', { team: label(side) })}
        text={label}
        title={(side) => (names ? fullNameOf(names, side) : undefined)}
        onPick={review.won}
        narrow={narrow}
      />
    </div>
  )
}

/**
 * Two buttons, ours and theirs, the coach's call pressed: who served (1 2), who won (3 4). They
 * show the teams' names (the group says what for); their names say it all ("KSCW H3 serves").
 */
function CallButtons({
  label,
  value,
  keys,
  name,
  text,
  title,
  onPick,
  narrow,
}: {
  label: string
  value: Side | null
  keys: [string, string]
  name: (side: Side) => string
  text: (side: Side) => string
  title: (side: Side) => string | undefined
  onPick: (side: Side) => void
  narrow: boolean
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-semibold text-muted">{label}</span>
      <div className="grid grid-cols-2 gap-2" role="group" aria-label={label}>
        {(['us', 'them'] as const).map((side, i) => (
          <Button
            key={side}
            size="touch"
            aria-pressed={value === side}
            aria-label={name(side)}
            title={title(side) ?? name(side)}
            className={cx('min-w-0 px-3 leading-tight whitespace-normal', value === side && 'bg-accent-soft! text-on-accent-soft ring-2 ring-accent')}
            onClick={press(() => onPick(side))}
          >
            <span className="min-w-0 text-center wrap-break-word">{text(side)}</span>
            <Kbd hidden={narrow}>{keys[i]}</Kbd>
          </Button>
        ))}
      </div>
    </div>
  )
}

/** A break's label: what WiePlay thinks it is, and the keys (W B T D O) to say what it is. */
function GapLabels({ gap }: { gap: Segment }) {
  const { t } = useTranslation()
  const { review, narrow, taxonomy } = useWorkspace()
  return (
    <div className="flex flex-col gap-1 border-t border-hairline pt-2">
      <span className="text-xs font-semibold text-muted">{t('tagging.review.label')}</span>
      <div role="group" aria-label={t('tagging.review.label')} className="flex flex-wrap gap-1">
        {GAP_KINDS.map((kind) => {
          const name = segmentKindLabel(t, kind, taxonomy.sport)
          const on = gap.kind === kind
          return (
            <Button
              key={kind}
              size="touch"
              variant={on ? 'primary' : 'secondary'}
              aria-pressed={on}
              aria-label={t('tagging.review.labelAs', { kind: name })}
              className="px-3"
              onClick={press(() => review.relabel(kind))}
            >
              {name}
              <Kbd hidden={narrow}>{KEY_OF[kind]}</Kbd>
            </Button>
          )
        })}
      </div>
    </div>
  )
}

/** Every suggestion is answered: the way to "rallies reviewed", which teaches WiePlay. */
function Done({ videoId, reviewed }: { videoId: string; reviewed: boolean }) {
  const { t } = useTranslation()
  const { reload } = useWorkspace()
  const mark = useSetRalliesReviewed(videoId)
  return (
    <EmptyState
      icon={<CheckIcon />}
      title={t('tagging.review.doneTitle')}
      action={
        reviewed ? undefined : (
          <Button variant="primary" size="touch" disabled={mark.isPending} onClick={() => mark.mutate(true, { onSuccess: reload })}>
            {t('tagging.analysis.markReviewed')}
          </Button>
        )
      }
    >
      {reviewed ? t('tagging.review.doneReviewed') : t('tagging.review.doneBody')}
    </EmptyState>
  )
}

/** The keys the buttons don't show; ? (or "Keys") lists them all. */
function KeyLegend() {
  const { t } = useTranslation()
  const names = useSideNames()
  const teams = { us: sideLabel(t, 'us', names), them: sideLabel(t, 'them', names) }
  const rows: [string, string][] = [
    ['← →', t('tagging.keys.review.start')],
    ['⇧ ← →', t('tagging.keys.review.end')],
    ['Alt', t('tagging.keys.review.frame')],
    ['1 2', t('tagging.keys.review.serve', teams)],
    ['3 4', t('tagging.keys.review.won', teams)],
    ['W B T D O', t('tagging.keys.review.label')],
    ['↑ ↓', t('tagging.keys.review.move')],
    ['Space', t('tagging.keys.review.space')],
    ['?', t('tagging.review.allKeys')],
  ]
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-xs text-muted">
      {rows.map(([key, text]) => (
        <div key={key} className="contents">
          <dt>
            <kbd className="rounded-md border border-hairline bg-sunken px-1.5 py-px font-mono text-[11px] font-semibold whitespace-nowrap text-fg">{key}</kbd>
          </dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  )
}

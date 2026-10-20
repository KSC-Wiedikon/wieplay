import '../../i18n/pages'
import type { TFunction } from 'i18next'
import { useId, useMemo, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { AlertIcon, CheckIcon, ChevronDownIcon, ChevronUpIcon, CloseIcon, MergeIcon, SparkleIcon, SplitIcon, UndoIcon } from '../../components/icons'
import { EmptyState } from '../../components/Message'
import { ProgressBar } from '../../components/ProgressBar'
import { SuggestionChip } from '../../components/SuggestionChip'
import { useSetRalliesReviewed } from '../../lib/analysis'
import { cx } from '../../lib/cx'
import { aSegmentKind, formatClock, reasonText, scoreNoteText, segmentKindLabel, sideLabel } from '../../lib/labels'
import { periodAt } from '../../lib/rallies'
import { cardTone, courtByRally, issueText, notesAfter, notesToShow, noteText, noteTime, useSheetCourt, type SheetNote } from '../../lib/sheetCourt'
import { fullNameOf, useSideNames, type SideNames } from '../../lib/sides'
import {
  allProgress,
  effectiveServer,
  effectiveWinner,
  formatPercent,
  formatSeconds,
  gapToCheck,
  isGap,
  isOpen,
  rallyToCheck,
  reviewItems,
  scoreAfter,
  setToCheck,
} from '../../lib/suggestions'
import type { GapKind, Segment, Side } from '../../lib/types'
import { useReview, useView, useWorkspace } from './context'
import { GuideLink } from './GuideLink'
import { BREAK_BUTTONS, GAP_KEYS, type ReviewNotice } from './review'

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

/** The key of each break label (W B T D O; "Sub" has none). */
const KEY_OF = Object.fromEntries(Object.entries(GAP_KEYS).map(([key, kind]) => [kind, key.toUpperCase()])) as Partial<Record<GapKind, string>>

function noticeText(t: TFunction, notice: ReviewNotice, sport: string, names: SideNames | null): string {
  const p = notice.params ?? {}
  const time = p.time === undefined ? '' : formatClock(Number(p.time))
  switch (notice.key) {
    case 'accepted':
    case 'rejected':
    case 'split':
      return t(`tagging.review.notice.${notice.key}`, { time })
    case 'toRally':
      return t('tagging.review.notice.toRally', { start: formatClock(Number(p.start)), end: formatClock(Number(p.end)) })
    case 'gapAccepted':
    case 'gapRejected':
      return t(`tagging.review.notice.${notice.key}`, { time, kind: segmentKindLabel(t, p.kind as GapKind, sport) })
    // Inside the sentence: "Now a timeout", "the suggestion at 30:41.5 is a timeout".
    case 'relabelled':
    case 'toBreak':
      return t(`tagging.review.notice.${notice.key}`, { time, kind: aSegmentKind(t, p.kind as GapKind, sport) })
    case 'serve':
      return t('tagging.review.notice.serve', { side: sideLabel(t, p.side as Side, names) })
    case 'won':
      return t('tagging.review.notice.won', { side: sideLabel(t, p.side as Side, names) })
    case 'moved':
      return p.field === 'end' ? t('tagging.review.notice.movedEnd', { time }) : t('tagging.review.notice.movedStart', { time })
    case 'done':
      return t('tagging.review.doneTitle')
    default:
      return t(`tagging.review.notice.${notice.key}`)
  }
}

/**
 * "To check", the rallies and breaks (PLAN §10.1): the item on screen, why WiePlay is unsure and
 * its other answer, its boundaries, the answers as big buttons (tablets) with their keys (on a
 * rally also "not a rally, it's a timeout …", on a break which one it is), the sets to check, and
 * the progress. What WiePlay is sure of counts already and is not asked.
 */
export function ReviewPanel() {
  const { t } = useTranslation()
  const { review, video, narrow, taxonomy } = useWorkspace()
  const names = useSideNames()
  const view = useView()
  const state = useReview()
  const items = useMemo(() => reviewItems(view.segments, state.pending), [view.segments, state.pending])
  // What the walk still visits: "Every suggestion is reviewed" only when nothing is.
  const open = useMemo(() => items.filter((s) => isOpen(s, state.pending)).length, [items, state.pending])
  const progress = useMemo(
    () => allProgress(view.segments, state.seen, state.seenGaps, state.rejectedBefore, state.pending),
    [view.segments, state.seen, state.seenGaps, state.rejectedBefore, state.pending],
  )
  const current = items.find((s) => s.id === state.currentId) ?? null
  const gap = current !== null && isGap(current.kind)
  const set = current ? periodAt(view.periods, current.start_ms) : null
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
            {gap && gapToCheck(current) && <GapWhy segment={current} />}
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
            {!gap && <RallyFacts rally={current} />}
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
            {/* A break is not merged or split: it is labelled (below), or it is play after all. */}
            {gap && (
              <Button size="touch" className="col-span-2" onClick={press(review.toRally)}>
                {t('tagging.review.toRally')}
                <Kbd hidden={narrow}>R</Kbd>
              </Button>
            )}
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
          <BreakButtons segment={current} />
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
      ) : open > 0 ? (
        <EmptyState
          icon={<SparkleIcon />}
          title={t('tagging.review.stillOpen', { count: open })}
          action={
            <Button variant="primary" size="touch" onClick={press(review.resume)}>
              {t('tagging.review.next')}
              <ChevronDownIcon />
            </Button>
          }
        >
          {t('tagging.review.stillOpenBody')}
        </EmptyState>
      ) : progress.total > 0 || items.length > 0 ? (
        <Done videoId={video.id} reviewed={Boolean(video.rallies_reviewed_at)} />
      ) : (
        <EmptyState icon={<SparkleIcon />} title={t('tagging.review.emptyTitle')}>
          {t('tagging.review.emptyBody')}
        </EmptyState>
      )}

      <p role="status" className={cx('min-h-5 text-sm', state.notice?.tone === 'error' ? 'text-danger' : 'text-muted')}>
        {state.notice ? noticeText(t, state.notice, taxonomy.sport, names) : t('tagging.review.hint')}
      </p>

      <SetsToCheck />
      <SheetNotes />
      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}
      <GuideLink />

    </section>
  )
}

/** "(WiePlay, 87%, near court)": where a value of WiePlay's comes from. */
function suggestedDetail(t: TFunction, parts: (string | null | undefined)[]): string {
  return ` (${[t('tagging.scoring.suggested'), ...parts].filter(Boolean).join(', ')})`
}

/** Why WiePlay is unsure of a break, and its other answer as a button. */
function GapWhy({ segment }: { segment: Segment }) {
  const { t } = useTranslation()
  const { review, taxonomy } = useWorkspace()
  const other = segment.model_alternative ?? null
  const confidence = formatPercent(segment.confidence)
  return (
    <div data-to-check="true" className="flex flex-col gap-1 rounded-lg border border-danger/40 bg-danger/5 p-2 text-sm">
      <p className="flex items-center gap-1.5 font-semibold text-danger">
        <AlertIcon className="size-4 shrink-0" />
        {t('tagging.review.toCheckTitle')}
      </p>
      <p>{confidence ? t('tagging.review.whyGap', { confidence }) : t('tagging.review.whyGapPlain')}</p>
      {other && isGap(other as GapKind) && (
        <Button size="touch" className="self-start whitespace-normal" onClick={press(review.takeAlternative)}>
          {t('tagging.review.otherAnswer', { answer: segmentKindLabel(t, other as GapKind, taxonomy.sport) })}
        </Button>
      )}
    </div>
  )
}

/** WiePlay's other answer for a rally: the other winner, or no rally at all. */
function RallyAlternative({ rally }: { rally: Segment }) {
  const { t } = useTranslation()
  const { review } = useWorkspace()
  const names = useSideNames()
  const other = rally.model_alternative ?? null
  if (other !== 'us' && other !== 'them' && other !== 'none') return null
  const answer = other === 'none' ? t('tagging.review.otherNone') : t('tagging.review.otherWon', { team: sideLabel(t, other, names) })
  return (
    <Button size="touch" data-alternative={other} className="self-start whitespace-normal" onClick={press(review.takeAlternative)}>
      {t('tagging.review.otherAnswer', { answer })}
    </Button>
  )
}

/**
 * The sets WiePlay is unsure of (PLAN §10.3): where each starts and ends, how sure it is,
 * accept or reject. They count already; this is the check.
 */
function SetsToCheck() {
  const { t } = useTranslation()
  const { controller } = useWorkspace()
  const view = useView()
  const sets = view.periods.filter(setToCheck)
  if (sets.length === 0) return null
  return (
    <div className="flex flex-col gap-2 border-t border-hairline pt-3" data-sets-to-check="true">
      <h3 className="text-sm font-semibold">{t('tagging.review.setsToCheck', { count: sets.length })}</h3>
      <ul className="flex flex-col gap-2">
        {sets.map((p) => {
          const confidence = formatPercent(p.confidence)
          return (
            <li key={p.id} className="flex flex-wrap items-center gap-2 text-sm">
              <button type="button" className="cursor-pointer font-semibold text-link tabular-nums underline-offset-2 hover:underline" onClick={() => controller.seek(p.start_ms)}>
                {t('tagging.review.setLine', { no: p.no, start: formatClock(p.start_ms), end: p.end_ms == null ? '…' : formatClock(p.end_ms) })}
              </button>
              {confidence && <span className="text-muted">{t('tagging.review.setSure', { confidence })}</span>}
              <span className="ml-auto flex gap-1">
                <Button size="md" variant="primary" onClick={press(() => controller.acceptPeriod(p.id))}>
                  {t('tagging.review.accept')}
                </Button>
                <Button size="md" variant="danger" onClick={press(() => controller.deletePeriod(p.id))}>
                  {t('tagging.review.reject')}
                </Button>
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Who served, who won and the score after the rally, by the teams' names: the coach's facts,
 * else WiePlay's suggestions; "check" with the reason when the automatic score disagrees with
 * something here. 1 and 2 call the serving team, 3 and 4 the winner (a fact the scoring keeps
 * to: the score line follows at once).
 */
function RallyFacts({ rally }: { rally: Segment }) {
  const { t } = useTranslation()
  const { review, narrow, video } = useWorkspace()
  const names = useSideNames()
  // The official scoresheet (docs/PLAN.md §10.3a, §10.5): who served, and its notes after the rally.
  const court = useSheetCourt(video.id, rally.sheet_point != null).data
  const onCourt = courtByRally(court).get(rally.id) ?? null
  const after = notesAfter(court, rally.id)
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
  // The official scoresheet (docs/PLAN.md §10.3a), where the scoring lined it up with this rally.
  const sheetSays = rally.sheet_point != null && rally.sheet_won_by ? rally.sheet_won_by : null
  const fromSheet = winner.suggested && sheetSays !== null && sheetSays === winner.value
  const wonText = winner.value
    ? `${sideLabel(t, winner.value, names)}${
        winner.suggested
          ? suggestedDetail(t, [
              fromSheet ? t('sheet.title') : fromLog ? t('tagging.scoring.log.label') : formatPercent(rally.score_confidence),
            ])
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
      {sheetSays && (
        <p data-sheet={sheetSays} className="text-sm text-muted tabular-nums" title={t('sheetReview.title')}>
          {t('sheetReview.point', { n: rally.sheet_point, team: sideLabel(t, sheetSays, names) })}
          {onCourt?.server != null && ` · ${t('sheetReview.server', { number: onCourt.server, team: sideLabel(t, onCourt.serving, names) })}`}
        </p>
      )}
      {after.length > 0 && (
        <ul aria-label={t('sheetCourt.afterRally')} className="flex flex-col gap-1 text-sm">
          {after.map((note) => (
            <SheetNoteLine key={`${note.kind}-${note.set}-${note.index}`} note={note} />
          ))}
        </ul>
      )}
      {logText && (
        <p data-log={logSays ?? undefined} className="text-sm text-muted tabular-nums" title={logDelay !== null ? t('tagging.scoring.log.title', { delay: logDelay }) : undefined}>
          {logText}
        </p>
      )}
      {rallyToCheck(rally) ? (
        <div data-needs-you="true" data-to-check="true" className="flex flex-col gap-1 rounded-lg border border-danger/40 bg-danger/5 p-2 text-sm">
          <p className="flex items-center gap-1.5 font-semibold text-danger">
            <AlertIcon className="size-4 shrink-0" />
            {t('tagging.review.toCheckTitle')}
          </p>
          {rally.score_reasons && rally.score_reasons.length > 0 ? (
            <ul className="flex list-disc flex-col gap-0.5 pl-6">
              {rally.score_reasons.map((reason, i) => (
                <li key={`${reason.code}-${i}`} data-reason={reason.code}>
                  {reasonText(t, reason, names)}
                </li>
              ))}
            </ul>
          ) : (
            <p>{t('tagging.review.whyRally', { confidence: formatPercent(rally.score_confidence ?? rally.confidence) || '–' })}</p>
          )}
          <RallyAlternative rally={rally} />
        </div>
      ) : (
        rally.score_conflict && (
          <p data-check="true" className="flex items-start gap-1.5 text-sm font-semibold text-danger">
            <AlertIcon className="mt-0.5 size-4 shrink-0" />
            {t('tagging.scoring.checkReason', { reason: scoreNoteText(t, rally.score_note) })}
          </p>
        )
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

/** One of the scoresheet's notes: what it is, where, and what disagrees there. */
function SheetNoteLine({ note, seek }: { note: SheetNote; seek?: (ms: number) => void }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const time = noteTime(note)
  const card = note.kind === 'sanction' ? cardTone(note.sanction) : null
  return (
    <li data-note={note.kind} data-issues={note.issues.join(' ') || undefined} className="flex flex-col gap-0.5">
      <span className={cx('flex flex-wrap items-center gap-1.5 tabular-nums', note.issues.length > 0 && 'font-semibold text-danger')}>
        {card && <CardMark tone={card} />}
        {note.issues.length > 0 && <AlertIcon className="size-4 shrink-0" />}
        {noteText(t, note, names)}
        {seek && time && note.start_ms != null ? (
          <button
            type="button"
            className="cursor-pointer text-link underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            onClick={() => seek(note.start_ms ?? 0)}
            title={t('sheetCourt.play', { time })}
          >
            {time}
          </button>
        ) : (
          time && <span className="text-muted">· {time}</span>
        )}
      </span>
      {note.issues.map((issue) => (
        <span key={issue} className="text-muted">
          {issueText(t, note, issue)}
        </span>
      ))}
    </li>
  )
}

/** A card's colour as a small chip (no person: the team only). */
export function CardMark({ tone }: { tone: 'yellow' | 'red' | 'dark' }) {
  return (
    <span
      aria-hidden="true"
      data-card={tone}
      className={cx(
        'inline-block h-3.5 w-2.5 shrink-0 rounded-[2px] ring-1 ring-fg/30',
        tone === 'yellow' ? 'bg-yellow-400' : tone === 'red' ? 'bg-red-600' : 'bg-fg',
      )}
    />
  )
}

/** The scoresheet's notes that disagree with the video, and its cards (docs/PLAN.md §10.3a). */
function SheetNotes() {
  const { t } = useTranslation()
  const { video, controller } = useWorkspace()
  const court = useSheetCourt(video.id).data
  const notes = notesToShow(court)
  const flagged = notes.filter((n) => n.issues.length > 0).length
  if (!court || court.notes.length === 0) return null
  return (
    <details className="border-t border-hairline pt-3" open={flagged > 0}>
      <summary className="cursor-pointer text-sm font-semibold">
        {t('sheetCourt.title')}
        <span className="ml-2 font-normal text-muted">{flagged > 0 ? t('sheetCourt.toCheck', { count: flagged }) : t('sheetCourt.allFit')}</span>
      </summary>
      <p className="mt-1 text-sm text-muted">{t('sheetCourt.intro')}</p>
      {notes.length > 0 && (
        <ul className="mt-2 flex flex-col gap-2 text-sm">
          {notes.map((note) => (
            <SheetNoteLine key={`${note.kind}-${note.set}-${note.index}`} note={note} seek={(ms) => controller.seek(ms)} />
          ))}
        </ul>
      )}
    </details>
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

/**
 * The breaks as buttons with their keys (T B W D O), under Accept and Reject, short names ("Sub",
 * "Other"; the full one as the tooltip). On a rally they say "not a rally, it's a timeout …": the
 * rally is rejected and the dead time around it becomes that break. On a break they say which one
 * it is (the current label pressed: it accepts it). Two columns, like Accept and Reject above.
 */
function BreakButtons({ segment }: { segment: Segment }) {
  const { t } = useTranslation()
  const { review, narrow, taxonomy } = useWorkspace()
  const id = useId()
  const gap = isGap(segment.kind)
  const basketball = taxonomy.sport === 'basketball'
  return (
    <div className="flex flex-col gap-1">
      <span id={`${id}-label`} className="text-xs font-semibold text-muted">
        {gap ? t('tagging.review.breakIs') : t('tagging.review.notARally')}
      </span>
      <div role="group" aria-labelledby={`${id}-label`} className="grid grid-cols-2 gap-2">
        {BREAK_BUTTONS.map((kind) => {
          const on = gap && segment.kind === kind
          const full = segmentKindLabel(t, kind, taxonomy.sport)
          const short = basketball && kind === 'set_break' ? full : t(`tagging.review.breaks.${kind}`)
          const key = KEY_OF[kind]
          return (
            <Button
              key={kind}
              size="touch"
              variant={on ? 'primary' : 'secondary'}
              aria-pressed={gap ? on : undefined}
              title={short === full ? undefined : full}
              className="px-3"
              onClick={press(() => review.relabel(kind))}
            >
              {short}
              {key && <Kbd hidden={narrow}>{key}</Kbd>}
            </Button>
          )
        })}
      </div>
    </div>
  )
}

/** Every rally and break is checked: the way to "rallies reviewed", which teaches WiePlay. */
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
    ['R', t('tagging.keys.review.rally')],
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

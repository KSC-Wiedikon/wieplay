import type { TFunction } from 'i18next'
import { useMemo, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon, CloseIcon, MergeIcon, SparkleIcon, SplitIcon, UndoIcon } from '../../components/icons'
import { EmptyState } from '../../components/Message'
import { ProgressBar } from '../../components/ProgressBar'
import { SuggestionChip } from '../../components/SuggestionChip'
import { useSetRalliesReviewed } from '../../lib/analysis'
import { cx } from '../../lib/cx'
import { formatClock } from '../../lib/labels'
import { periodAt } from '../../lib/rallies'
import { ACCEPT_ALL_MIN, formatPercent, formatSeconds, isOpen, reviewItems, reviewProgress, sureSuggestions } from '../../lib/suggestions'
import { useReview, useView, useWorkspace } from './context'
import type { ReviewNotice } from './review'

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

function noticeText(t: TFunction, notice: ReviewNotice): string {
  const p = notice.params ?? {}
  const time = p.time === undefined ? '' : formatClock(Number(p.time))
  switch (notice.key) {
    case 'accepted':
    case 'rejected':
    case 'split':
      return t(`tagging.review.notice.${notice.key}`, { time })
    case 'moved':
      return p.field === 'end' ? t('tagging.review.notice.movedEnd', { time }) : t('tagging.review.notice.movedStart', { time })
    case 'acceptedAll':
      return t('tagging.review.notice.acceptedAll', { count: Number(p.count) })
    case 'done':
      return t('tagging.review.doneTitle')
    default:
      return t(`tagging.review.notice.${notice.key}`)
  }
}

/**
 * The review of Nomal's suggestions (PLAN §10.1): the suggestion on screen, its boundaries,
 * the answers as big buttons (tablets) with their keys, the progress, and "accept all sure ones".
 */
export function ReviewPanel() {
  const { t } = useTranslation()
  const { review, video, narrow } = useWorkspace()
  const view = useView()
  const state = useReview()
  const items = useMemo(() => reviewItems(view.segments, state.pending), [view.segments, state.pending])
  const progress = useMemo(() => reviewProgress(view.segments, state.seen, state.rejectedBefore), [view.segments, state.seen, state.rejectedBefore])
  const sure = useMemo(() => sureSuggestions(view.segments), [view.segments])
  const current = items.find((s) => s.id === state.currentId) ?? null
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
                {t('tagging.review.current', { time: formatClock(current.start_ms) })}
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
        {state.notice ? noticeText(t, state.notice) : t('tagging.review.hint')}
      </p>

      {sure.length > 0 && (
        <div className="border-t border-hairline pt-3">
          <Button size="touch" className="w-full whitespace-normal" onClick={press(review.askAcceptAll)}>
            <CheckIcon className="size-4" />
            {t('tagging.review.acceptAll', { min, count: sure.length })}
          </Button>
        </div>
      )}

      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}

      <ConfirmDialog
        open={state.confirmAll}
        title={t('tagging.review.acceptAllTitle', { count: sure.length })}
        confirmLabel={t('tagging.review.acceptAllConfirm', { count: sure.length })}
        onCancel={review.cancelAcceptAll}
        onConfirm={review.confirmAcceptAll}
      >
        {t('tagging.review.acceptAllBody', { min })}
      </ConfirmDialog>
    </section>
  )
}

/** Every suggestion is answered: the way to "rallies reviewed", which teaches Nomal. */
function Done({ videoId, reviewed }: { videoId: string; reviewed: boolean }) {
  const { t } = useTranslation()
  const mark = useSetRalliesReviewed(videoId)
  return (
    <EmptyState
      icon={<CheckIcon />}
      title={t('tagging.review.doneTitle')}
      action={
        reviewed ? undefined : (
          <Button variant="primary" size="touch" disabled={mark.isPending} onClick={() => mark.mutate(true)}>
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
  const rows: [string, string][] = [
    ['← →', t('tagging.keys.review.start')],
    ['⇧ ← →', t('tagging.keys.review.end')],
    ['Alt', t('tagging.keys.review.frame')],
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

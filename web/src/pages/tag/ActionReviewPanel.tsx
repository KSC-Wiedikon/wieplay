import type { TFunction } from 'i18next'
import { useMemo, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon, CloseIcon, SparkleIcon, UndoIcon } from '../../components/icons'
import { EmptyState } from '../../components/Message'
import { ProgressBar } from '../../components/ProgressBar'
import { actionItems, actionProgress, factTags, gateText, GRADE_KEYS, isReviewedSuggestion, isSuggestedTag, openInRally } from '../../lib/actions'
import { useAnalysis, useSetActionsReviewed } from '../../lib/analysis'
import { cx } from '../../lib/cx'
import { skillByCode } from '../../lib/grammar'
import { formatClock, gradeLabel, sideLabel, skillLabel } from '../../lib/labels'
import { useSideNames, type SideNames } from '../../lib/sides'
import { periodAt, ralliesOf } from '../../lib/rallies'
import { formatPercent } from '../../lib/suggestions'
import type { Side, Tag, Taxonomy } from '../../lib/types'
import type { ActionNotice } from './actionReview'
import { useActionReview, useView, useWorkspace } from './context'

/** A pointer click leaves the focus on the button; give the keys back to the review. */
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
      className={cx('rounded-md border border-current/25 px-1 py-px font-mono text-[11px] leading-4 font-semibold opacity-80', hidden && 'hidden')}
    >
      {children}
    </kbd>
  )
}

function skillName(t: TFunction, taxonomy: Taxonomy, code: string | number | undefined): string {
  const value = String(code ?? '')
  return skillLabel(t, skillByCode(taxonomy, value), value)
}

function noticeText(t: TFunction, notice: ActionNotice, taxonomy: Taxonomy, names: SideNames | null): string {
  const p = notice.params ?? {}
  const time = p.time === undefined ? '' : formatClock(Number(p.time))
  const skill = skillName(t, taxonomy, p.code)
  switch (notice.key) {
    case 'accepted':
      return p.number ? t('actions.notice.acceptedNumber', { skill, time, number: p.number }) : t('actions.notice.accepted', { skill, time })
    case 'rejected':
      return t('actions.notice.rejected', { skill, time })
    case 'skill':
    case 'sameSkill':
      return t(`actions.notice.${notice.key}`, { skill })
    case 'unknownSkill':
      return t('actions.notice.unknownSkill', { code: String(p.code ?? '') })
    case 'grade':
      return t('actions.notice.grade', { grade: String(p.grade ?? '') })
    case 'side':
      return t('actions.notice.side', { side: sideLabel(t, p.side as Side, names) })
    case 'moved':
      return t('actions.notice.moved', { time })
    case 'acceptedRally':
      return t('actions.notice.acceptedRally', { count: Number(p.count) })
    default:
      return t(`actions.notice.${notice.key}`)
  }
}

/** "Suggested · 87%" while open; a small marker once accepted or corrected. */
export function ActionChip({ tag, className }: { tag: Tag; className?: string }) {
  const { t } = useTranslation()
  if (isSuggestedTag(tag)) {
    const confidence = formatPercent(tag.confidence)
    return (
      <span
        data-action-suggestion="open"
        className={cx(
          'inline-flex items-center gap-1 rounded-full border border-dashed border-accent bg-accent-soft px-2 py-0.5 text-xs leading-4 font-semibold whitespace-nowrap text-on-accent-soft',
          className,
        )}
      >
        <SparkleIcon className="size-3.5" />
        {confidence ? t('actions.suggestedConfidence', { confidence }) : t('actions.suggested')}
      </span>
    )
  }
  if (isReviewedSuggestion(tag)) {
    const full = tag.review === 'corrected' ? t('actions.corrected') : t('actions.confirmed')
    return (
      <span data-action-suggestion={tag.review} title={full} className={cx('inline-flex items-center gap-1 text-xs whitespace-nowrap text-muted', className)}>
        <SparkleIcon className="size-3.5" />
        <span className="sr-only">{full}</span>
      </span>
    )
  }
  return null
}

/**
 * The review of the actions Nomal suggested (PLAN §10.4a): the action on screen with its skill,
 * grade, side, player and contact time, each a key (or a big button on a tablet), the progress,
 * "accept the rest of this rally", and "actions reviewed" once everything is answered.
 */
export function ActionReviewPanel() {
  const { t } = useTranslation()
  const names = useSideNames()
  const { actions, video, narrow, taxonomy } = useWorkspace()
  const view = useView()
  const state = useActionReview()
  const items = useMemo(() => actionItems(view.events), [view.events])
  const progress = useMemo(() => actionProgress(view.events, state.seen, state.rejectedBefore), [view.events, state.seen, state.rejectedBefore])
  const current = items.find((e) => e.id === state.currentId) ?? null
  const rallies = useMemo(() => ralliesOf(view.segments), [view.segments])
  const rally = current
    ? (rallies.find((r) => r.id === current.segment_id) ?? rallies.find((r) => r.start_ms - 2000 <= current.t_ms && current.t_ms <= r.end_ms) ?? null)
    : null
  const restOfRally = useMemo(() => openInRally(view.events, rally), [view.events, rally])
  const set = current ? periodAt(view.periods, current.t_ms) : null
  const progressText = t('actions.progressLabel', { done: progress.done, total: progress.total })
  const reviewedAt = useActionsReviewedAt()

  return (
    <section aria-labelledby="actions-review-title" className="flex flex-col gap-3 rounded-card border border-hairline bg-surface p-4 shadow-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="actions-review-title" className="flex items-center gap-2 font-semibold">
          <SparkleIcon className="size-4" />
          {t('actions.title')}
        </h2>
        <p aria-live="polite" aria-atomic="true" className="text-lg font-bold tabular-nums">
          <span aria-hidden="true">{t('actions.progress', { done: progress.done, total: progress.total })}</span>
          <span className="sr-only">{progressText}</span>
        </p>
      </div>
      {progress.total > 0 && <ProgressBar value={progress.done / progress.total} label={progressText} />}

      {current ? (
        <>
          <div
            className={cx(
              'flex flex-col gap-2 rounded-xl border-2 p-3',
              isSuggestedTag(current) ? 'border-dashed border-accent bg-accent-soft/40' : 'border-hairline bg-sunken',
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold tabular-nums">
                {t('actions.current', { skill: skillName(t, taxonomy, current.code), time: formatClock(current.t_ms) })}
                <span className="font-normal text-muted">
                  {rally && ` · ${t('actions.inRally', { time: formatClock(rally.start_ms) })}`}
                  {set && ` · ${t('actions.set', { no: set.no })}`}
                </span>
              </h3>
              <ActionChip tag={current} />
            </div>
            <Facts tag={current} number={state.number} />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Button variant="primary" size="touch" onClick={press(actions.accept)}>
              <CheckIcon className="size-4" />
              {t('actions.accept')}
              <Kbd hidden={narrow}>Enter</Kbd>
            </Button>
            <Button variant="danger" size="touch" onClick={press(actions.reject)}>
              <CloseIcon className="size-4" />
              {t('actions.reject')}
              <Kbd hidden={narrow}>X</Kbd>
            </Button>
          </div>
          {restOfRally.length > 1 && (
            <Button size="touch" className="w-full whitespace-normal" onClick={press(actions.acceptRally)}>
              <CheckIcon className="size-4" />
              {t('actions.acceptRally', { count: restOfRally.length })}
              <Kbd hidden={narrow}>⇧ Enter</Kbd>
            </Button>
          )}
          <div className="flex flex-wrap items-center gap-1">
            <Button variant="ghost" size="touch" disabled={!view.canUndo} onClick={press(actions.undo)}>
              <UndoIcon className="size-4" />
              {t('actions.undo')}
              <Kbd hidden={narrow}>U</Kbd>
            </Button>
            <span className="ml-auto flex gap-1">
              <Button variant="ghost" size="touchIcon" aria-label={t('actions.previous')} title={t('actions.previous')} onClick={press(() => actions.move(-1))}>
                <ChevronUpIcon />
              </Button>
              <Button variant="ghost" size="touchIcon" aria-label={t('actions.next')} title={t('actions.next')} onClick={press(() => actions.move(1))}>
                <ChevronDownIcon />
              </Button>
            </span>
          </div>
        </>
      ) : progress.total > 0 ? (
        <Done videoId={video.id} reviewed={Boolean(reviewedAt)} />
      ) : (
        <Empty videoId={video.id} reviewed={Boolean(reviewedAt)} />
      )}

      <p role="status" className={cx('min-h-5 text-sm', state.notice?.tone === 'error' ? 'text-danger' : 'text-muted')}>
        {state.notice ? noticeText(t, state.notice, taxonomy, names) : t('actions.hint')}
      </p>

      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}
    </section>
  )
}

/** The tagging data's "actions reviewed" (the bundle as loaded; the mutation updates the cache). */
function useActionsReviewedAt(): string | null {
  const { bundle } = useWorkspace()
  return bundle.actions_reviewed_at ?? null
}

/** Skill, grade, side, player and contact time of the action on screen, each with its keys. */
function Facts({ tag, number }: { tag: Tag; number: string }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const { actions, narrow, taxonomy } = useWorkspace()
  const skill = skillByCode(taxonomy, tag.code)
  const grade = tag.attributes.grade ?? null
  return (
    <div className="flex flex-col gap-3 border-t border-hairline pt-2">
      <div className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-muted">{t('actions.skill')}</span>
        <div role="group" aria-label={t('actions.skill')} className="flex flex-wrap gap-1">
          {taxonomy.skills.map((s) => {
            const on = s.code === tag.code
            return (
              <Button
                key={s.code}
                size="touch"
                variant={on ? 'primary' : 'secondary'}
                aria-pressed={on}
                aria-label={t('actions.skillAs', { code: s.code, skill: skillLabel(t, s) })}
                title={skillLabel(t, s)}
                className="min-w-11 px-2 font-mono"
                onClick={press(() => actions.setSkill(s.code))}
              >
                {s.code}
              </Button>
            )
          })}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-muted">
          {t('actions.grade')}
          <span className="ml-1 font-normal">{grade ? `${grade} ${gradeLabel(t, skill, grade)}` : t('actions.noGrade')}</span>
        </span>
        <div role="group" aria-label={t('actions.grade')} className="flex flex-wrap gap-1">
          {GRADE_KEYS.map((g) => {
            const on = g === grade
            const meaning = gradeLabel(t, skill, g)
            return (
              <Button
                key={g}
                size="touch"
                variant={on ? 'primary' : 'secondary'}
                aria-pressed={on}
                aria-label={t('actions.gradeAs', { grade: g, meaning })}
                title={meaning}
                className="min-w-11 px-2 font-mono"
                onClick={press(() => actions.setGrade(g))}
              >
                {g}
              </Button>
            )
          })}
        </div>
      </div>
      <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 text-sm">
        <dt className="font-semibold text-muted">{t('actions.side')}</dt>
        <dd className="font-semibold">{sideLabel(t, tag.side, names)}</dd>
        <dd>
          <Button size="touch" onClick={press(actions.toggleSide)}>
            {t('actions.otherSide')}
            <Kbd hidden={narrow}>Tab</Kbd>
          </Button>
        </dd>
        <dt className="font-semibold text-muted">{t('actions.player')}</dt>
        <dd className={cx('col-span-2 flex items-center gap-2 tabular-nums', number && 'font-bold text-link')} aria-live="polite">
          {number ? t('actions.typing', { number }) : tag.number !== null ? `#${tag.number}` : t('actions.noPlayer')}
          {!number && <Kbd hidden={narrow}>0–9</Kbd>}
        </dd>
        <dt className="font-semibold text-muted">{t('actions.time')}</dt>
        <dd className="font-mono tabular-nums">
          {formatClock(tag.t_ms)}
          {tag.time_precision === 'frame' && <span className="ml-1 text-xs text-muted">{t('tagging.table.frameShort')}</span>}
        </dd>
        <dd className="flex gap-1">
          <Button size="touchIcon" aria-label={t('actions.earlier')} title={`${t('actions.earlier')} (←)`} onClick={press(() => actions.nudge(-1))}>
            <span aria-hidden="true">−</span>
          </Button>
          <Button size="touchIcon" aria-label={t('actions.later')} title={`${t('actions.later')} (→)`} onClick={press(() => actions.nudge(1))}>
            <span aria-hidden="true">+</span>
          </Button>
        </dd>
      </dl>
    </div>
  )
}

/**
 * Nothing suggested: below the bar (PLAN §10.4a) the reason plainly, and "actions reviewed" for
 * a game the coach tagged, which is how Nomal learns to suggest.
 */
function Empty({ videoId, reviewed }: { videoId: string; reviewed: boolean }) {
  const { t } = useTranslation()
  const { reload } = useWorkspace()
  const view = useView()
  const gate = useAnalysis(videoId, true).data?.actions?.gate
  const mark = useSetActionsReviewed(videoId)
  const tagged = factTags(view.events).length > 0
  const done = reviewed || mark.isSuccess
  const text = gateText(t, gate)
  return (
    <EmptyState
      icon={<SparkleIcon />}
      title={t('actions.emptyTitle')}
      action={
        tagged && !done ? (
          <Button variant="primary" size="touch" disabled={mark.isPending} onClick={() => mark.mutate(true, { onSuccess: reload })}>
            {t('actions.markReviewed')}
          </Button>
        ) : undefined
      }
    >
      {text ?? t('actions.emptyBody')}
      {done && tagged && <span className="mt-1 block">{t('actions.doneReviewed')}</span>}
    </EmptyState>
  )
}

/** Every suggestion answered: on to "actions reviewed", which teaches Nomal. */
function Done({ videoId, reviewed }: { videoId: string; reviewed: boolean }) {
  const { t } = useTranslation()
  const { reload } = useWorkspace()
  const mark = useSetActionsReviewed(videoId)
  const done = reviewed || mark.isSuccess
  return (
    <EmptyState
      icon={<CheckIcon />}
      title={t('actions.doneTitle')}
      action={
        done ? undefined : (
          <Button variant="primary" size="touch" disabled={mark.isPending} onClick={() => mark.mutate(true, { onSuccess: reload })}>
            {t('actions.markReviewed')}
          </Button>
        )
      }
    >
      {done ? t('actions.doneReviewed') : t('actions.doneBody')}
    </EmptyState>
  )
}

/** The keys the buttons don't show; ? lists them all. */
function KeyLegend() {
  const { t } = useTranslation()
  const rows: [string, string][] = [
    ['S R E A B D F', t('actions.keys.skill')],
    ['# + ! - / =', t('actions.keys.grade')],
    ['0–9 Enter', t('actions.keys.number')],
    ['Tab', t('actions.keys.side')],
    ['← →', t('actions.keys.nudge')],
    ['⇧ Enter', t('actions.keys.rally')],
    ['↑ ↓', t('actions.keys.move')],
    ['Space', t('actions.keys.space')],
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

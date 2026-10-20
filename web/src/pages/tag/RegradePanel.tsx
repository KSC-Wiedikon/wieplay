import type { TFunction } from 'i18next'
import { useId, useMemo, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { Select } from '../../components/Field'
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, GaugeIcon, UndoIcon } from '../../components/icons'
import { EmptyState } from '../../components/Message'
import { ProgressBar } from '../../components/ProgressBar'
import { Segmented } from '../../components/Segmented'
import { playerKey } from '../../lib/condensed'
import { cx } from '../../lib/cx'
import { countedTags } from '../../lib/facts'
import { skillByCode } from '../../lib/grammar'
import { formatClock, gradeLabel, periodLabel, playerLabel, skillLabel } from '../../lib/labels'
import { periodAt } from '../../lib/rallies'
import { useSideNames } from '../../lib/sides'
import type { Tag, Taxonomy } from '../../lib/types'
import { useRegrade, useView, useWorkspace } from './context'
import { regradeTags, type RegradeNotice } from './regrade'

/** A pointer click leaves the focus on the button; give the keys back to the queue. */
function press(action: () => void) {
  return (event: MouseEvent<HTMLButtonElement>) => {
    action()
    if (event.detail > 0) event.currentTarget.blur()
  }
}

function Kbd({ children, hidden }: { children: ReactNode; hidden?: boolean }) {
  return (
    <kbd aria-hidden="true" className={cx('rounded-md border border-current/25 px-1 py-px font-mono text-[11px] leading-4 font-semibold opacity-80', hidden && 'hidden')}>
      {children}
    </kbd>
  )
}

function noticeText(t: TFunction, notice: RegradeNotice, taxonomy: Taxonomy): string {
  const p = notice.params ?? {}
  const skill = skillLabel(t, skillByCode(taxonomy, String(p.code ?? '')), String(p.code ?? ''))
  const time = p.time === undefined ? '' : formatClock(Number(p.time))
  const grade = String(p.grade ?? '')
  const meaning = grade ? gradeLabel(t, skillByCode(taxonomy, String(p.code ?? '')), grade) : ''
  return t(`regrade.notice.${notice.key}`, { skill, time, grade: meaning && meaning !== grade ? `${grade} ${meaning}` : grade })
}

/**
 * The regrade queue (PLAN §9.7): pick a skill and which of its tags, then grade them one after
 * another; each moment plays in a loop. Keys on a computer, big buttons on a tablet or phone.
 */
export function RegradePanel() {
  const { t } = useTranslation()
  const state = useRegrade()
  return (
    <section aria-labelledby="regrade-title" className="flex flex-col gap-3 rounded-card border border-hairline bg-surface p-4 shadow-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="regrade-title" className="flex items-center gap-2 font-semibold">
          <GaugeIcon className="size-4" />
          {t('regrade.title')}
        </h2>
        {state.running && state.queue.length > 0 && (
          <p className="text-lg font-bold tabular-nums" aria-hidden="true">
            {t('regrade.progress', { n: Math.min(state.index + 1, state.queue.length), total: state.queue.length })}
          </p>
        )}
      </div>
      {state.running ? <Queue /> : <Setup />}
    </section>
  )
}

/** The pass: which skill, which of its tags, one player, one set. */
function Setup() {
  const { t } = useTranslation()
  const id = useId()
  const names = useSideNames()
  const { regrade, taxonomy, narrow } = useWorkspace()
  const view = useView()
  const state = useRegrade()
  const { setup } = state
  const count = useMemo(() => regradeTags(view.events, view.periods, setup).length, [view.events, view.periods, setup])
  const skill = skillByCode(taxonomy, setup.code)
  // Players with tags of the skill, ours first.
  const players = useMemo(() => {
    const seen = new Map<string, Tag>()
    for (const tag of countedTags(view.events)) if (tag.code === setup.code) seen.set(playerKey(tag.side, tag.number), tag)
    return [...seen.entries()].sort(([, a], [, b]) => (a.side === b.side ? (a.number ?? 100) - (b.number ?? 100) : a.side === 'us' ? -1 : 1))
  }, [view.events, setup.code])

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{t('regrade.intro')}</p>
      <div className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-muted">{t('regrade.skill')}</span>
        <div role="group" aria-label={t('regrade.skill')} className="flex flex-wrap gap-1">
          {taxonomy.skills
            .filter((s) => s.graded)
            .map((s) => {
              const on = s.code === setup.code
              return (
                <Button
                  key={s.code}
                  size="touch"
                  variant={on ? 'primary' : 'secondary'}
                  aria-pressed={on}
                  className="px-3"
                  onClick={press(() => regrade.setSetup({ code: s.code, player: null }))}
                >
                  {skillLabel(t, s)}
                </Button>
              )
            })}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-muted">{t('regrade.which')}</span>
        <Segmented
          className="self-start"
          label={t('regrade.which')}
          value={setup.which}
          options={[
            { value: 'ungraded', label: t('regrade.ungraded') },
            { value: 'all', label: t('regrade.all') },
          ]}
          onChange={(which, event) => {
            regrade.setSetup({ which })
            if (event.detail > 0) event.currentTarget.blur()
          }}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs font-semibold text-muted" htmlFor={`${id}-player`}>
          {t('regrade.player')}
          <Select id={`${id}-player`} value={setup.player ?? ''} onChange={(e) => regrade.setSetup({ player: e.target.value || null })}>
            <option value="">{t('regrade.everyone')}</option>
            {players.map(([key, tag]) => (
              <option key={key} value={key}>
                {playerLabel(t, tag.side, tag.number, view.roster, undefined, names)}
              </option>
            ))}
          </Select>
        </label>
        {view.periods.length > 0 && (
          <label className="flex flex-col gap-1 text-xs font-semibold text-muted" htmlFor={`${id}-set`}>
            {t('regrade.set')}
            <Select id={`${id}-set`} value={setup.set ?? ''} onChange={(e) => regrade.setSetup({ set: e.target.value || null })}>
              <option value="">{t('regrade.allSets')}</option>
              {view.periods.map((p) => (
                <option key={p.id} value={p.id}>
                  {periodLabel(t, taxonomy.sport, p.no)}
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-3">
        <p className="text-sm font-semibold tabular-nums" role="status">
          {count > 0 ? t('regrade.count', { count }) : <span className="font-normal text-muted">{t('regrade.none')}</span>}
        </p>
        <Button variant="primary" size="touch" disabled={count === 0} onClick={press(regrade.begin)}>
          {t('regrade.start')}
          {skill && ` · ${skillLabel(t, skill)}`}
          <Kbd hidden={narrow}>Enter</Kbd>
        </Button>
      </div>
      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}
    </div>
  )
}

/** The tag on screen, its grade buttons with what each grade means for the skill, the moves. */
function Queue() {
  const { t } = useTranslation()
  const { regrade, taxonomy, narrow } = useWorkspace()
  const view = useView()
  const state = useRegrade()
  const current = useMemo(() => {
    const id = state.queue[state.index]
    return id === undefined ? null : (view.events.find((e) => e.id === id) ?? null)
  }, [state.queue, state.index, view.events])
  const total = state.queue.length
  const done = state.index >= total
  const progressText = t('regrade.progressLabel', { n: Math.min(state.index + 1, total), total })

  return (
    <div className="flex flex-col gap-3">
      <ProgressBar value={total > 0 ? Math.min(state.index, total) / total : 0} label={progressText} />
      <p className="sr-only" aria-live="polite">
        {progressText}
      </p>
      {done || !current ? (
        <EmptyState
          icon={<CheckIcon />}
          title={t('regrade.doneTitle')}
          action={
            <span className="flex flex-wrap justify-center gap-2">
              <Button variant="primary" size="touch" onClick={press(regrade.setupAgain)}>
                {t('regrade.again')}
              </Button>
              <Button size="touch" onClick={press(() => regrade.move(-1))}>
                <ChevronLeftIcon className="size-4" />
                {t('regrade.previous')}
              </Button>
            </span>
          }
        >
          {t('regrade.done', { count: total, graded: state.graded.size })}
        </EmptyState>
      ) : (
        <Current tag={current} />
      )}
      <div className="flex flex-wrap items-center gap-1">
        <Button variant="ghost" size="touch" disabled={!view.canUndo} onClick={press(regrade.undo)}>
          <UndoIcon className="size-4" />
          {t('regrade.undo')}
          <Kbd hidden={narrow}>U</Kbd>
        </Button>
        <Button variant="ghost" size="touch" onClick={press(regrade.setupAgain)}>
          {t('regrade.change')}
          <Kbd hidden={narrow}>Esc</Kbd>
        </Button>
        <span className="ml-auto text-sm text-muted tabular-nums">{t('regrade.graded', { count: state.graded.size })}</span>
      </div>
      <p role="status" className={cx('min-h-5 text-sm', state.notice?.tone === 'error' ? 'text-danger' : 'text-muted')}>
        {state.notice ? noticeText(t, state.notice, taxonomy) : narrow ? t('regrade.phoneHint') : t('regrade.hint')}
      </p>
      {!narrow && (
        <div className="border-t border-hairline pt-3">
          <KeyLegend />
        </div>
      )}
    </div>
  )
}

function Current({ tag }: { tag: Tag }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const { regrade, taxonomy, narrow } = useWorkspace()
  const view = useView()
  const skill = skillByCode(taxonomy, tag.code)
  const grade = tag.attributes.grade ?? null
  const set = periodAt(view.periods, tag.t_ms)
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5 rounded-xl border-2 border-hairline bg-sunken p-3">
        <h3 className="font-semibold tabular-nums">
          <span>{playerLabel(t, tag.side, tag.number, view.roster, tag.member_id, names)}</span>{' '}
          <span className="font-normal text-muted">
            {[`· ${t('regrade.current', { skill: skillLabel(t, skill, tag.code), time: formatClock(tag.t_ms) })}`, set ? t('regrade.setNo', { no: set.no }) : null]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </h3>
        <p className="text-sm">
          {grade ? t('regrade.gradeNow', { grade: `${grade} ${gradeLabel(t, skill, grade)}` }) : <span className="text-muted">{t('regrade.noGrade')}</span>}
        </p>
      </div>
      <div role="group" aria-label={t('regrade.gradesLabel')} className="grid grid-cols-2 gap-2">
        {taxonomy.grades.map((g, i) => {
          const on = g === grade
          const meaning = gradeLabel(t, skill, g)
          return (
            <Button
              key={g}
              size="touch"
              variant={on ? 'primary' : 'secondary'}
              aria-pressed={on}
              aria-label={t('regrade.gradeAs', { grade: g, meaning, key: i + 1 })}
              className="h-auto min-h-14 justify-start px-3 py-2 text-left"
              onClick={press(() => regrade.grade(g))}
            >
              <span className="w-5 shrink-0 font-mono text-lg font-bold">{g}</span>
              <span className="min-w-0 flex-1 leading-tight">{meaning}</span>
              <Kbd hidden={narrow}>{i + 1}</Kbd>
            </Button>
          )
        })}
      </div>
      <div className="grid grid-cols-[auto_1fr_auto_auto] gap-2">
        <Button size="touchIcon" aria-label={t('regrade.previous')} title={t('regrade.previous')} onClick={press(() => regrade.move(-1))}>
          <ChevronLeftIcon />
        </Button>
        <Button size="touch" onClick={press(regrade.skip)}>
          {t('regrade.skip')}
          <Kbd hidden={narrow}>S</Kbd>
        </Button>
        <Button size="touch" variant="ghost" aria-pressed={grade === null} aria-label={t('regrade.clearGradeLabel')} onClick={press(() => regrade.grade(null))}>
          {t('regrade.clearGrade')}
          <Kbd hidden={narrow}>0</Kbd>
        </Button>
        <Button size="touchIcon" aria-label={t('regrade.next')} title={t('regrade.next')} onClick={press(() => regrade.move(1))}>
          <ChevronRightIcon />
        </Button>
      </div>
    </div>
  )
}

/** The keys the buttons don't show; ? lists them all. */
function KeyLegend() {
  const { t } = useTranslation()
  const rows: [string, string][] = [
    ['1–6', t('regrade.keys.grade')],
    ['# + ! - / =', t('regrade.keys.symbols')],
    ['0', t('regrade.keys.clear')],
    ['S Enter', t('regrade.keys.skip')],
    ['← → ↑ ↓', t('regrade.keys.move')],
    ['Space', t('regrade.keys.space')],
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

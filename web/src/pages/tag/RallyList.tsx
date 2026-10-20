import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { CheckIcon, CloseIcon } from '../../components/icons'
import { CheckMark, ScoreHead, SuggestedValue } from '../../components/ScoringMarks'
import { Segmented } from '../../components/Segmented'
import { SuggestionChip } from '../../components/SuggestionChip'
import { Table, TBody, Td, Th, THead, Tr } from '../../components/Table'
import { WinnerPicker } from '../../components/WinnerPicker'
import { cx } from '../../lib/cx'
import { firstReasonText, formatClock, segmentLabel, sideLabel } from '../../lib/labels'
import { periodAt, ralliesOf } from '../../lib/rallies'
import { useSideNames } from '../../lib/sides'
import { effectiveServer, formatPercent, formatSeconds, isGap, isOpen, isToCheck, isWiePlays, rallyToCheck, reviewItems, scoreAfter } from '../../lib/suggestions'
import type { Segment } from '../../lib/types'
import { useReview, useUi, useView, useWorkspace } from './context'

type Filter = 'all' | 'open'

/** Keeps a row visible inside the scrolling box without scrolling the page. */
function reveal(container: HTMLElement | null, id: string | null): void {
  if (!container || !id) return
  const row = container.querySelector<HTMLElement>(`[data-rally="${id}"]`)
  if (!row) return
  const header = container.querySelector('thead')?.getBoundingClientRect().height ?? 0
  const top = row.offsetTop - header
  const bottom = row.offsetTop + row.offsetHeight
  if (top < container.scrollTop) container.scrollTop = top
  else if (bottom > container.scrollTop + container.clientHeight) container.scrollTop = bottom - container.clientHeight
}

/**
 * The rallies of the video. While tagging: every rally (WiePlay's count like any other), those
 * still to check marked, with a filter for them (accept or reject them right here); who served
 * and who won, WiePlay's values muted, and "check" where the automatic score disagrees with
 * something. While reviewing: WiePlay's rallies and breaks in time order with their state, the
 * one on screen highlighted; by default only those still to check (docs/PLAN.md §10.1), each
 * with why.
 */
export function RallyList({ mode, toolbar }: { mode: 'tag' | 'review'; toolbar?: ReactNode }) {
  const { t } = useTranslation()
  const { controller, review, taxonomy } = useWorkspace()
  const names = useSideNames()
  const view = useView()
  const ui = useUi()
  const state = useReview()
  const [filter, setFilter] = useState<Filter>(mode === 'review' ? 'open' : 'all')
  const container = useRef<HTMLDivElement>(null)
  const reviewing = mode === 'review'

  const rows = useMemo(
    () => (reviewing ? reviewItems(view.segments, state.pending) : ralliesOf(view.segments)),
    [reviewing, view.segments, state.pending],
  )
  const numbers = useMemo(() => new Map(rows.map((r, i) => [r.id, i + 1])), [rows])
  // Still to check (a part split off in this session too); while reviewing also the one on screen,
  // so it stays in view.
  const open = useMemo(() => rows.filter((r) => isOpen(r, state.pending)), [rows, state.pending])
  const openShown = useMemo(
    () => (reviewing ? rows.filter((r) => isOpen(r, state.pending) || r.id === state.currentId) : open),
    [reviewing, rows, open, state.pending, state.currentId],
  )
  const shown = filter === 'open' ? openShown : rows
  const sets = useMemo(() => new Map(view.periods.map((p) => [p.id, p.no])), [view.periods])
  const currentId = reviewing ? state.currentId : ui.segmentId

  useEffect(() => reveal(container.current, currentId), [currentId])

  const pick = (segment: Segment) => {
    if (reviewing) {
      review.select(segment.id)
      return
    }
    controller.selectSegment(segment.id)
    controller.seek(segment.start_ms - 1000)
  }

  const options = reviewing
    ? ([
        { value: 'open', label: t('tagging.suggestions.reviewOpen', { count: open.length }) },
        { value: 'all', label: t('tagging.suggestions.reviewAll', { count: rows.length }) },
      ] as const)
    : ([
        { value: 'all', label: t('tagging.suggestions.all', { count: rows.length }) },
        { value: 'open', label: t('tagging.suggestions.onlyOpen', { count: open.length }) },
      ] as const)
  const caption = reviewing ? t('tagging.suggestions.listTitle') : t('tagging.suggestions.title')
  const empty = rows.length === 0 ? (reviewing ? t('tagging.suggestions.noneReview') : t('tagging.suggestions.none')) : t('tagging.suggestions.noneOpen')

  return (
    <section aria-label={caption} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {toolbar}
        {/* Hide the filter until there is something to filter. */}
        {(reviewing || open.length > 0) && (
          <Segmented label={t('tagging.suggestions.filterLabel')} value={filter} options={options} onChange={(value) => setFilter(value)} />
        )}
      </div>
      <Card className="overflow-hidden">
        <div ref={container} className="relative max-h-[28rem] overflow-y-auto">
          {shown.length === 0 ? (
            <p className="p-4 text-sm text-muted">{empty}</p>
          ) : (
            <Table caption={caption}>
              <THead className="sticky top-0 z-10">
                <tr>
                  <Th>{reviewing ? t('tagging.suggestions.suggestion') : t('tagging.suggestions.rally')}</Th>
                  <Th className="hidden sm:table-cell">{t('tagging.filters.set')}</Th>
                  <Th>{t('tagging.suggestions.start')}</Th>
                  <Th className="hidden sm:table-cell">{t('tagging.suggestions.length')}</Th>
                  {reviewing && <Th className="hidden sm:table-cell">{t('tagging.suggestions.what')}</Th>}
                  {reviewing && <Th className="min-w-36">{t('tagging.suggestions.whyColumn')}</Th>}
                  {!reviewing && <Th className="hidden md:table-cell">{t('tagging.suggestions.serve')}</Th>}
                  <ScoreHead />
                  {!reviewing && <Th className="hidden md:table-cell">{t('tagging.condensed.point')}</Th>}
                  <Th className={cx(reviewing && 'hidden sm:table-cell')}>{reviewing ? t('tagging.suggestions.state') : t('tagging.suggestions.source')}</Th>
                  {!reviewing && (
                    <Th className="text-right">
                      <span className="sr-only">{t('tagging.table.actions')}</span>
                    </Th>
                  )}
                </tr>
              </THead>
              <TBody>
                {shown.map((rally) => {
                  const n = numbers.get(rally.id) ?? 0
                  const current = rally.id === currentId
                  const set = rally.period_id ? sets.get(rally.period_id) : periodAt(view.periods, rally.start_ms)?.no
                  const time = formatClock(rally.start_ms)
                  const splitOff = state.pending.has(rally.id)
                  const gap = isGap(rally.kind)
                  const server = effectiveServer(rally)
                  const score = gap ? null : scoreAfter(rally)
                  return (
                    <Tr
                      key={rally.id}
                      data-rally={rally.id}
                      data-check={rally.score_conflict ? 'true' : undefined}
                      onActivate={() => pick(rally)}
                      aria-current={current ? 'true' : undefined}
                      className={cx(current && 'bg-accent-soft', current && reviewing && 'outline-2 -outline-offset-2 outline-accent')}
                    >
                      <Td tight>
                        <button
                          type="button"
                          onClick={() => pick(rally)}
                          aria-label={reviewing ? t('tagging.suggestions.goSuggestion', { n, time }) : t('tagging.suggestions.go', { n, time })}
                          className="min-h-11 cursor-pointer rounded-lg px-1 font-semibold link tabular-nums sm:min-h-9"
                        >
                          {n}
                        </button>
                      </Td>
                      <Td className="hidden tabular-nums sm:table-cell">{set ?? '–'}</Td>
                      <Td className="font-mono text-sm tabular-nums">{time}</Td>
                      <Td className="hidden text-muted tabular-nums sm:table-cell">{formatSeconds(rally.end_ms - rally.start_ms)}</Td>
                      {reviewing && (
                        <Td className={cx('hidden whitespace-nowrap sm:table-cell', gap && 'font-semibold')}>{segmentLabel(t, rally, taxonomy.sport, names)}</Td>
                      )}
                      {reviewing && (
                        // Why the rally is to check: the first reason (the panel lists them all).
                        <Td className="text-sm">
                          {rallyToCheck(rally) ? (
                            <span data-reason={rally.score_reasons?.[0]?.code ?? rally.score_note ?? undefined} className="text-danger">
                              {firstReasonText(t, rally, names)}
                            </span>
                          ) : null}
                        </Td>
                      )}
                      {!reviewing && (
                        <Td className="hidden md:table-cell">
                          {server.value ? <SuggestedValue suggested={server.suggested}>{sideLabel(t, server.value, names)}</SuggestedValue> : '–'}
                        </Td>
                      )}
                      <Td className="font-semibold tabular-nums">
                        <span className="inline-flex flex-wrap items-center gap-x-2">
                          {score ? <SuggestedValue suggested={score.suggested}>{score.text}</SuggestedValue> : '–'}
                          <CheckMark rally={rally} />
                        </span>
                      </Td>
                      {!reviewing && (
                        // Who won: the coach's call (a fact the score line follows), else WiePlay's, muted.
                        <Td tight className="hidden md:table-cell">
                          <WinnerPicker rally={rally} n={n} names={names} onPick={(won_by) => controller.updateSegment(rally.id, { won_by })} />
                        </Td>
                      )}
                      <Td className={cx(reviewing && 'hidden sm:table-cell')}>
                        {reviewing && !isToCheck(rally) && !splitOff ? (
                          <ReviewedState rally={rally} />
                        ) : (
                          <SuggestionChip segment={rally} splitOff={splitOff} />
                        )}
                      </Td>
                      {!reviewing && (
                        <Td tight className="text-right whitespace-nowrap">
                          {isWiePlays(rally) && isToCheck(rally) && (
                            <>
                              <Button
                                variant="ghost"
                                size="icon"
                                aria-label={t('tagging.suggestions.acceptRally', { n })}
                                title={t('tagging.suggestions.accept')}
                                onClick={() => controller.acceptSegment(rally.id)}
                              >
                                <CheckIcon className="size-4" />
                              </Button>
                              <Button
                                variant="ghostDanger"
                                size="icon"
                                aria-label={t('tagging.suggestions.rejectRally', { n })}
                                title={t('tagging.suggestions.reject')}
                                onClick={() => controller.deleteSegment(rally.id)}
                              >
                                <CloseIcon className="size-4" />
                              </Button>
                            </>
                          )}
                        </Td>
                      )}
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          )}
        </div>
      </Card>
    </section>
  )
}

/** A checked item in the review list: accepted or corrected (or sure), and how sure WiePlay was. */
function ReviewedState({ rally }: { rally: Segment }) {
  const { t } = useTranslation()
  const confidence = formatPercent(rally.confidence)
  if (rally.review !== 'confirmed' && rally.review !== 'corrected') return <SuggestionChip segment={rally} />
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 text-xs">
      <span className="inline-flex items-center gap-1 font-semibold">
        <CheckIcon className="size-3.5" />
        {rally.review === 'corrected' ? t('tagging.suggestions.correctedShort') : t('tagging.suggestions.confirmedShort')}
      </span>
      {confidence && <span className="text-muted tabular-nums">{confidence}</span>}
    </span>
  )
}

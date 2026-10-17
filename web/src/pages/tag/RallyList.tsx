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
import { formatClock, segmentKindLabel, sideLabel } from '../../lib/labels'
import { periodAt, ralliesOf } from '../../lib/rallies'
import { useSideNames } from '../../lib/sides'
import {
  effectiveServer,
  formatPercent,
  formatSeconds,
  isGap,
  isOpen,
  isProposal,
  isSuggestion,
  reviewItems,
  scoreAfter,
} from '../../lib/suggestions'
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
 * The rallies of the video. While tagging: every rally, with Nomal's suggestions marked and a
 * filter for those still to review (accept or reject them right here); who served and who won,
 * suggested values muted, and "check" where the automatic score disagrees with something.
 * While reviewing: the suggestions (rallies and breaks) in time order with their state, the one
 * on screen highlighted.
 */
export function RallyList({ mode, toolbar }: { mode: 'tag' | 'review'; toolbar?: ReactNode }) {
  const { t } = useTranslation()
  const { controller, review, taxonomy } = useWorkspace()
  const names = useSideNames()
  const view = useView()
  const ui = useUi()
  const state = useReview()
  const [filter, setFilter] = useState<Filter>('all')
  const container = useRef<HTMLDivElement>(null)
  const reviewing = mode === 'review'

  const rows = useMemo(
    () => (reviewing ? reviewItems(view.segments, state.pending) : ralliesOf(view.segments)),
    [reviewing, view.segments, state.pending],
  )
  const numbers = useMemo(() => new Map(rows.map((r, i) => [r.id, i + 1])), [rows])
  const open = useMemo(() => rows.filter((r) => isOpen(r, state.pending)), [rows, state.pending])
  const shown = filter === 'open' ? open : rows
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
        { value: 'all', label: t('tagging.suggestions.reviewAll', { count: rows.length }) },
        { value: 'open', label: t('tagging.suggestions.reviewOpen', { count: open.length }) },
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
                  {reviewing && <Th>{t('tagging.suggestions.what')}</Th>}
                  {!reviewing && <Th className="hidden md:table-cell">{t('tagging.suggestions.serve')}</Th>}
                  <ScoreHead />
                  {!reviewing && <Th className="hidden md:table-cell">{t('tagging.condensed.point')}</Th>}
                  <Th>{reviewing ? t('tagging.suggestions.state') : t('tagging.suggestions.source')}</Th>
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
                        <Td className={cx('whitespace-nowrap', gap && 'font-semibold')}>{segmentKindLabel(t, rally.kind, taxonomy.sport)}</Td>
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
                        // Who won: the coach's call (a fact the score line follows), else Nomal's, muted.
                        <Td tight className="hidden md:table-cell">
                          <WinnerPicker rally={rally} n={n} names={names} onPick={(won_by) => controller.updateSegment(rally.id, { won_by })} />
                        </Td>
                      )}
                      <Td>
                        {reviewing && !isProposal(rally) && !splitOff ? (
                          <ReviewedState rally={rally} />
                        ) : (
                          <SuggestionChip segment={rally} splitOff={splitOff} />
                        )}
                      </Td>
                      {!reviewing && (
                        <Td tight className="text-right whitespace-nowrap">
                          {isSuggestion(rally) && (
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

/** An answered suggestion in the review list: accepted or corrected, and how sure Nomal was. */
function ReviewedState({ rally }: { rally: Segment }) {
  const { t } = useTranslation()
  const confidence = formatPercent(rally.confidence)
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

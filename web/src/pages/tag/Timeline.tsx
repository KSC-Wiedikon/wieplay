import { useId, useMemo, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { Select } from '../../components/Field'
import { SparkleIcon } from '../../components/icons'
import { SuggestionChip } from '../../components/SuggestionChip'
import { isSuggestedTag } from '../../lib/actions'
import { commentMoments } from '../../lib/comments'
import { cx } from '../../lib/cx'
import { formatEntry } from '../../lib/grammar'
import { formatClock, periodLabel, scoreNoteText, segmentKindLabel, sideLabel } from '../../lib/labels'
import { useComments } from '../../lib/library'
import { usePlayhead } from '../../lib/playhead'
import { useSideNames } from '../../lib/sides'
import {
  formatPercent,
  GAP_KINDS,
  isGap,
  isGapSuggestion,
  isProposal,
  isReviewedProposal,
  isSetSuggestion,
  isSuggestion,
  scoreAfter,
} from '../../lib/suggestions'
import type { Segment, SegmentKind, Side, Tag } from '../../lib/types'
import { useActionReview, useReview, useUi, useView, useWorkspace } from './context'

const SPANS = [30_000, 60_000, 120_000, 300_000] as const

/**
 * WiePlay's open suggestions are outlined (dashed), not filled: they are not rallies (or breaks)
 * yet. A break WiePlay labelled is a lighter, dashed band in its colour.
 */
function segmentClass(segment: Pick<Segment, 'kind' | 'source' | 'review'>): string {
  if (isSuggestion(segment)) return 'border-2 border-dashed border-accent bg-accent-soft'
  if (isGapSuggestion(segment)) return suggestedGapClass(segment.kind)
  return kindClass(segment.kind)
}

function suggestionState(segment: Segment): string | undefined {
  if (isProposal(segment)) return 'open'
  return isReviewedProposal(segment) ? segment.review : undefined
}

function kindClass(kind: SegmentKind): string {
  switch (kind) {
    case 'rally':
    case 'possession':
      return 'bg-accent'
    case 'timeout':
      return 'bg-danger/60'
    case 'set_break':
      return 'bg-muted/70'
    case 'warmup':
      return 'bg-link/50'
    case 'stoppage':
      return 'bg-fg/30'
    default:
      return 'bg-muted/40'
  }
}

function suggestedGapClass(kind: SegmentKind): string {
  switch (kind) {
    case 'timeout':
      return 'border border-dashed border-danger bg-danger/15'
    case 'set_break':
      return 'border border-dashed border-muted bg-muted/25'
    case 'warmup':
      return 'border border-dashed border-link bg-link/15'
    case 'stoppage':
      return 'border border-dashed border-fg/50 bg-fg/10'
    default:
      return 'border border-dashed border-muted bg-muted/10'
  }
}

function useDuration(): number | null {
  const { playhead, video } = useWorkspace()
  const view = useView()
  const live = usePlayhead(playhead, (s) => s.duration_ms)
  const last = Math.max(0, ...view.segments.map((s) => s.end_ms), ...view.events.map((e) => e.t_ms))
  return live ?? video.duration_ms ?? (last > 0 ? last : null)
}

function fraction(event: MouseEvent<HTMLElement>): number | null {
  const box = event.currentTarget.getBoundingClientRect()
  if (box.width <= 0) return null
  return Math.min(1, Math.max(0, (event.clientX - box.left) / box.width))
}

/** The whole video (rally bands, dead time grey, breaks, tags, playhead) and a zoom around the playhead. */
export function Timeline() {
  const { t } = useTranslation()
  const id = useId()
  const duration = useDuration()
  const [span, setSpan] = useState<number>(60_000)
  if (!duration) return null

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-title`} className="font-semibold">
          {t('tagging.timeline.title')}
        </h2>
        <label className="flex items-center gap-2 text-sm font-semibold" htmlFor={`${id}-span`}>
          {t('tagging.timeline.zoom')}
          <Select id={`${id}-span`} inline value={span} onChange={(e) => setSpan(Number(e.target.value))}>
            {SPANS.map((s) => (
              <option key={s} value={s}>
                {s < 60_000 ? t('tagging.timeline.seconds', { n: s / 1000 }) : t('tagging.timeline.minutes', { n: s / 60_000 })}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <Overview duration={duration} span={span} />
      <Zoom duration={duration} span={span} />
      <Legend />
    </section>
  )
}

function Overview({ duration, span }: { duration: number; span: number }) {
  const { t } = useTranslation()
  const { controller, video } = useWorkspace()
  const view = useView()
  const comments = useComments(video.id)
  const marks = useMemo(() => commentMoments(comments.data ?? []), [comments.data])

  const layers = useMemo(() => {
    const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / duration) * 100))}%`
    return (
      <>
        {view.periods.map((p, i) => (
          <div
            key={p.id}
            data-set-start={p.no}
            className={cx(
              'absolute top-0 h-1.5 border-l-2',
              // A set WiePlay suggested: lighter, until a coach accepts it.
              isSetSuggestion(p) ? 'border-dashed border-fg/40 bg-fg/15' : cx('border-fg', i % 2 === 0 ? 'bg-fg/40' : 'bg-fg/25'),
            )}
            style={{ left: pct(p.start_ms), width: pct((p.end_ms ?? duration) - p.start_ms) }}
          />
        ))}
        {view.segments.map((s) => (
          <div
            key={s.id}
            data-suggestion={suggestionState(s)}
            data-gap={isGap(s.kind) ? s.kind : undefined}
            className={cx(
              'absolute top-2 bottom-3 min-w-px rounded-sm',
              isSuggestion(s) ? 'border border-dashed border-accent bg-accent-soft' : isGapSuggestion(s) ? suggestedGapClass(s.kind) : kindClass(s.kind),
            )}
            style={{ left: pct(s.start_ms), width: pct(s.end_ms - s.start_ms) }}
          />
        ))}
        {view.events.map((e) => (
          <div
            key={e.id}
            data-action-suggestion={isSuggestedTag(e) ? 'open' : undefined}
            // A suggested action: shorter and in the accent colour, until a coach accepts it.
            className={cx('absolute bottom-0 w-px', isSuggestedTag(e) ? 'h-1 bg-accent' : cx('h-2', e.side === 'us' ? 'bg-fg' : 'bg-muted'))}
            style={{ left: pct(e.t_ms) }}
          />
        ))}
        {marks.map((m) => (
          <div
            key={m.id}
            data-comment={m.id}
            className="absolute top-0.5 size-2 -translate-x-1/2 rotate-45 rounded-[1px] bg-link ring-1 ring-surface"
            style={{ left: pct(m.t_ms) }}
          />
        ))}
      </>
    )
  }, [view.periods, view.segments, view.events, marks, duration])

  return (
    <div
      className="relative h-12 cursor-pointer overflow-hidden rounded-lg bg-sunken ring-1 ring-hairline ring-inset"
      title={t('tagging.timeline.overview')}
      aria-hidden="true"
      onClick={(event) => {
        const f = fraction(event)
        if (f !== null) controller.seek(f * duration)
      }}
    >
      {layers}
      <OverviewLive duration={duration} span={span} />
    </div>
  )
}

/** Parts of the overview that move with the playhead. */
function OverviewLive({ duration, span }: { duration: number; span: number }) {
  const { playhead } = useWorkspace()
  const view = useView()
  const now = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 250) * 250)
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / duration) * 100))}%`
  const open = view.openRally
  return (
    <>
      {open && now > open.start_ms && (
        <div
          className="absolute top-2 bottom-3 rounded-sm border-2 border-dashed border-accent"
          style={{ left: pct(open.start_ms), width: pct(now - open.start_ms) }}
        />
      )}
      <div
        className="pointer-events-none absolute inset-y-0 rounded-sm ring-1 ring-fg/50"
        style={{ left: pct(now - span / 2), width: pct(span) }}
      />
      <div className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-danger" style={{ left: pct(now) }} />
    </>
  )
}

function tickStep(span: number): number {
  if (span <= 30_000) return 5_000
  if (span <= 60_000) return 10_000
  if (span <= 120_000) return 20_000
  return 60_000
}

function Zoom({ duration, span }: { duration: number; span: number }) {
  const { t } = useTranslation()
  const { playhead, controller, taxonomy, review, actions } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const reviewState = useReview()
  const selectedId = reviewState.active ? reviewState.currentId : ui.segmentId
  const now = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 50) * 50)
  const from = now - span / 2
  const to = now + span / 2
  const x = (ms: number) => `${((ms - from) / span) * 100}%`
  const segments = view.segments.filter((s) => s.end_ms > from && s.start_ms < to)
  const sets = view.periods.filter((p) => p.start_ms >= from && p.start_ms <= to)
  const tags = view.events.filter((e) => e.t_ms >= from && e.t_ms <= to)
  const actionReview = useActionReview()
  const step = tickStep(span)
  const ticks: number[] = []
  for (let tick = Math.ceil(Math.max(0, from) / step) * step; tick <= Math.min(to, duration); tick += step) ticks.push(tick)
  const open = view.openRally

  const pickSegment = (segment: Segment) => (event: MouseEvent) => {
    event.stopPropagation()
    // While reviewing, a click on a suggestion (or a part split off) puts it on screen.
    if (reviewState.active && review.items().some((s) => s.id === segment.id)) {
      if (segment.id !== reviewState.currentId) review.select(segment.id)
    } else {
      controller.selectSegment(segment.id)
    }
    const f = fraction(event as MouseEvent<HTMLElement>)
    if (f !== null) controller.seek(segment.start_ms + f * (segment.end_ms - segment.start_ms))
  }
  const pickTag = (tag: Tag) => (event: MouseEvent) => {
    event.stopPropagation()
    // In the actions queue a suggestion goes on screen there.
    if (actionReview.active && tag.source === 'model') actions.select(tag.id)
    else controller.select(tag.id, { seek: true })
  }

  return (
    <div
      className="relative h-24 cursor-pointer overflow-hidden rounded-lg bg-sunken ring-1 ring-hairline ring-inset"
      title={t('tagging.timeline.zoomTitle')}
      aria-hidden="true"
      onClick={(event) => {
        const f = fraction(event)
        if (f !== null) controller.seek(from + f * span)
      }}
    >
      {ticks.map((tick) => (
        <div key={tick} className="absolute top-0 bottom-0 border-l border-hairline" style={{ left: x(tick) }}>
          <span className="absolute top-0.5 left-1 text-[10px] text-muted tabular-nums">{formatClock(tick, { tenths: false })}</span>
        </div>
      ))}
      {from < 0 && <div className="absolute inset-y-0 left-0 bg-page" style={{ width: x(0) }} />}
      {to > duration && <div className="absolute inset-y-0 right-0 bg-page" style={{ left: x(duration) }} />}
      {sets.map((p) => {
        const suggested = isSetSuggestion(p)
        const name = periodLabel(t, taxonomy.sport, p.no)
        return (
          <div
            key={p.id}
            data-set-start={p.no}
            title={suggested ? t('tagging.markers.suggested', { label: name }) : name}
            className={cx('absolute top-0 bottom-0 border-l-2', suggested ? 'border-dashed border-fg/40' : 'border-fg')}
            style={{ left: x(p.start_ms) }}
          >
            <span className={cx('absolute bottom-0.5 left-1 rounded px-1 text-[10px] leading-4 font-bold whitespace-nowrap', suggested ? 'bg-surface text-muted italic' : 'bg-fg text-page')}>
              {name}
            </span>
          </div>
        )
      })}
      {segments.map((s) => {
        const open = isSuggestion(s)
        const gap = isGap(s.kind)
        const score = s.kind === 'rally' ? scoreAfter(s) : null
        const kind = segmentKindLabel(t, s.kind, taxonomy.sport)
        return (
          <div
            key={s.id}
            onClick={pickSegment(s)}
            data-suggestion={suggestionState(s)}
            data-gap={gap ? s.kind : undefined}
            title={isGapSuggestion(s) ? t('tagging.markers.suggested', { label: kind }) : s.score_conflict ? scoreNoteText(t, s.score_note) : undefined}
            className={cx(
              'absolute top-5 flex h-7 items-center gap-0.5 overflow-hidden rounded-md px-1 text-xs font-semibold whitespace-nowrap',
              segmentClass(s),
              open ? 'text-on-accent-soft' : s.kind === 'rally' || s.kind === 'possession' ? 'text-on-accent' : 'text-fg',
              isGapSuggestion(s) && 'font-normal italic',
              selectedId === s.id && 'ring-2 ring-fg',
            )}
            style={{ left: x(Math.max(s.start_ms, from)), width: `${((Math.min(s.end_ms, to) - Math.max(s.start_ms, from)) / span) * 100}%` }}
          >
            {/* A suggestion that was accepted or corrected keeps a small marker. */}
            {s.source === 'model' && (s.kind === 'rally' || gap) && <SparkleIcon className="size-3 shrink-0" />}
            {s.score_conflict && <span aria-hidden="true" className="rounded-sm bg-danger px-0.5 text-[10px] leading-3 text-page">!</span>}
            {open ? formatPercent(s.confidence) : s.kind === 'rally' ? <span className={cx(score?.suggested && 'italic opacity-80')}>{score?.text ?? ''}</span> : kind}
          </div>
        )
      })}
      {open && now > open.start_ms && (
        <div
          className="absolute top-5 h-7 rounded-md border-2 border-dashed border-accent"
          style={{ left: x(Math.max(open.start_ms, from)), width: `${((now - Math.max(open.start_ms, from)) / span) * 100}%` }}
        />
      )}
      {tags.map((tag) => (
        <TagMarker
          key={tag.id}
          tag={tag}
          left={x(tag.t_ms)}
          selected={tag.id === (actionReview.active ? actionReview.currentId : ui.selectedId)}
          onClick={pickTag(tag)}
        />
      ))}
      <div className="pointer-events-none absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-danger" />
    </div>
  )
}

function TagMarker({ tag, left, selected, onClick }: { tag: Tag; left: string; selected: boolean; onClick: (e: MouseEvent) => void }) {
  const { t } = useTranslation()
  const row: Record<Side, string> = { us: 'top-13', them: 'top-[4.25rem]' }
  const suggested = isSuggestedTag(tag)
  return (
    <div
      onClick={onClick}
      data-action-suggestion={suggested ? 'open' : undefined}
      title={suggested ? t('tagging.markers.suggested', { label: formatEntry(tag) }) : undefined}
      className={cx(
        'absolute -translate-x-1/2 rounded px-1 font-mono text-[11px] leading-4 font-bold whitespace-nowrap',
        row[tag.side],
        // A suggested action: dashed and light, until a coach accepts it.
        suggested
          ? 'border border-dashed border-accent bg-accent-soft font-semibold text-on-accent-soft italic'
          : tag.side === 'us'
            ? 'bg-fg text-page'
            : 'bg-surface text-fg ring-1 ring-hairline',
        selected && 'ring-2 ring-accent',
      )}
      style={{ left }}
    >
      {formatEntry(tag)}
    </div>
  )
}

function Legend() {
  const { t } = useTranslation()
  const { controller, taxonomy } = useWorkspace()
  const names = useSideNames()
  const view = useView()
  const suggested = view.segments.some((s) => s.source === 'model' && s.kind === 'rally')
  const gapSuggested = view.segments.some(isGapSuggestion)
  const actionSuggested = view.events.some(isSuggestedTag)
  // Warm-up, stoppage and other breaks only once there are some.
  const more = GAP_KINDS.filter((k) => k !== 'timeout' && k !== 'set_break' && view.segments.some((s) => s.kind === k))
  const items: [string, string][] = [
    // Basketball marks no rallies (M4), so there is no dead time between them either.
    ...(controller.playsRallies
      ? ([
          ['bg-accent', t('tagging.timeline.legend.rally')],
          ...(suggested ? [['border border-dashed border-accent bg-accent-soft', t('tagging.suggestions.legend')]] : []),
          ['bg-sunken ring-1 ring-hairline', t('tagging.timeline.legend.dead')],
        ] as [string, string][])
      : []),
    ['bg-danger/60', t('tagging.segment.kind.timeout')],
    ['bg-muted/70', segmentKindLabel(t, 'set_break', taxonomy.sport)],
    ...more.map((k): [string, string] => [kindClass(k), segmentKindLabel(t, k, taxonomy.sport)]),
    ...(gapSuggested ? [['border border-dashed border-muted bg-muted/10', t('tagging.timeline.legend.gapSuggested')] as [string, string]] : []),
    ...(view.periods.length > 0 ? [['rounded-none border-l-2 border-fg', t('tagging.timeline.legend.setStart')] as [string, string]] : []),
    ['bg-fg', t('tagging.timeline.legend.tagUs', { team: sideLabel(t, 'us', names) })],
    ['bg-muted', t('tagging.timeline.legend.tagThem', { team: sideLabel(t, 'them', names) })],
    ...(actionSuggested ? [['border border-dashed border-accent bg-accent-soft', t('actions.legend')] as [string, string]] : []),
    ['bg-link rotate-45 scale-75', t('tagging.timeline.legend.comment')],
  ]
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map(([swatch, label]) => (
        <li key={label} className="flex items-center gap-1.5">
          <span className={cx('inline-block size-3 rounded-sm', swatch)} aria-hidden="true" />
          {label}
        </li>
      ))}
    </ul>
  )
}

const KINDS: SegmentKind[] = ['rally', 'timeout', 'set_break', 'warmup', 'stoppage', 'other_dead']

/** The selected rally, timeout or break: kind, start, end, point; nudge or set to the playhead. */
export function SegmentPanel({ id }: { id: string }) {
  const { t } = useTranslation()
  const htmlId = useId()
  const names = useSideNames()
  const { controller, playhead, taxonomy } = useWorkspace()
  const view = useView()
  const [error, setError] = useState(false)
  const segment = view.segments.find((s) => s.id === id)
  if (!segment) return null
  // Basketball marks no rallies: a break can't become one (one that is a rally already stays).
  const kinds = controller.playsRallies || segment.kind === 'rally' ? KINDS : KINDS.filter((k) => k !== 'rally')
  const kindLabel = (kind: SegmentKind) => segmentKindLabel(t, kind, taxonomy.sport)

  const update = (patch: Parameters<typeof controller.updateSegment>[1]) => setError(!controller.updateSegment(segment.id, patch))
  const here = () => playhead.getState().t_ms
  const move = (field: 'start_ms' | 'end_ms', ms: number) =>
    update(field === 'start_ms' ? { start_ms: Math.max(0, ms) } : { end_ms: Math.max(0, ms) })
  const nudge = (field: 'start_ms' | 'end_ms', ms: number) => move(field, segment[field] + ms)

  return (
    <section aria-labelledby={`${htmlId}-title`} className="flex flex-col gap-3 rounded-card border border-hairline bg-surface shadow-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id={`${htmlId}-title`} className="flex flex-wrap items-center gap-2 font-semibold">
          {t('tagging.segment.title', { kind: kindLabel(segment.kind) })}
          <SuggestionChip segment={segment} />
        </h2>
        <span className="flex flex-wrap gap-2">
          {isProposal(segment) && (
            <Button variant="primary" onClick={() => controller.acceptSegment(segment.id)}>
              {t('tagging.suggestions.accept')}
            </Button>
          )}
          <Button variant="ghost" onClick={() => controller.selectSegment(null)}>
            {t('common.close')}
          </Button>
        </span>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${htmlId}-kind`}>
          {t('tagging.segment.kindLabel')}
          <Select id={`${htmlId}-kind`} inline value={segment.kind} onChange={(e) => update({ kind: e.target.value as SegmentKind })}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {kindLabel(k)}
              </option>
            ))}
          </Select>
        </label>
        {(['start_ms', 'end_ms'] as const).map((field) => (
          <div key={field} className="flex flex-col gap-1 text-sm">
            <span className="font-semibold">{field === 'start_ms' ? t('tagging.segment.start') : t('tagging.segment.end')}</span>
            <span className="flex items-center gap-1">
              <Button size="icon" onClick={() => nudge(field, -500)} aria-label={t('tagging.segment.earlier', { field: field === 'start_ms' ? t('tagging.segment.start') : t('tagging.segment.end') })}>
                <span aria-hidden="true">−</span>
              </Button>
              <span className="w-16 text-center tabular-nums">{formatClock(segment[field])}</span>
              <Button size="icon" onClick={() => nudge(field, 500)} aria-label={t('tagging.segment.later', { field: field === 'start_ms' ? t('tagging.segment.start') : t('tagging.segment.end') })}>
                <span aria-hidden="true">+</span>
              </Button>
              <Button onClick={() => move(field, here())}>{t('tagging.segment.toPlayhead')}</Button>
            </span>
          </div>
        ))}
        {segment.kind === 'rally' && (
          <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${htmlId}-won`}>
            {t('tagging.segment.point')}
            <Select
              id={`${htmlId}-won`}
              inline
              value={segment.won_by ?? ''}
              onChange={(e) => update({ won_by: e.target.value === '' ? null : (e.target.value as Side) })}
            >
              <option value="">{t('tagging.segment.none')}</option>
              <option value="us">{sideLabel(t, 'us', names)}</option>
              <option value="them">{sideLabel(t, 'them', names)}</option>
            </Select>
          </label>
        )}
        <Button variant="ghostDanger" onClick={() => controller.deleteSegment(segment.id)} className="ml-auto">
          {segment.source === 'model' ? t('tagging.suggestions.reject') : t('tagging.segment.delete')}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {t('tagging.segment.invalid')}
        </p>
      )}
    </section>
  )
}

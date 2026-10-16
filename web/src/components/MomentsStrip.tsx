import '../i18n/pages'
import type { MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import type { Playhead } from '../lib/playhead'
import type { GapKind } from '../lib/types'
import { PlayheadMark } from './RallyPanel'

/** A labelled break (warm-up, set break, timeout, …); Nomal's open suggestions are lighter. */
export interface BreakMark {
  id: string
  start_ms: number
  end_ms: number
  kind: GapKind
  label: string
  suggested: boolean
}

/** Where a set starts. */
export interface StartMark {
  id: string
  t_ms: number
  label: string
  suggested: boolean
}

interface MomentsStripProps {
  duration_ms: number
  /** Clips as bands. */
  ranges: readonly { id: string; start_ms: number; end_ms: number; shared?: boolean }[]
  /** Comments pinned to a moment, as markers. */
  marks: readonly { id: string; t_ms: number }[]
  /** Breaks as thin bands along the bottom. */
  breaks?: readonly BreakMark[]
  /** Set starts as lines across the strip. */
  starts?: readonly StartMark[]
  playhead: Playhead
  onSeek: (t_ms: number) => void
}

function breakClass(kind: GapKind, suggested: boolean): string {
  if (suggested) {
    switch (kind) {
      case 'timeout':
        return 'border border-dashed border-danger bg-danger/15'
      case 'warmup':
        return 'border border-dashed border-link bg-link/15'
      default:
        return 'border border-dashed border-muted bg-muted/20'
    }
  }
  switch (kind) {
    case 'timeout':
      return 'bg-danger/70'
    case 'warmup':
      return 'bg-link/60'
    case 'set_break':
      return 'bg-muted'
    default:
      return 'bg-muted/60'
  }
}

/**
 * Below the player: the whole video as a bar with the clips as bands, the comments as markers,
 * the breaks along the bottom and the set starts as lines (the native seek bar can't carry
 * them). A tap near a comment or a set start goes to it; elsewhere, to that point. The lists
 * below are the keyboard way to the same places.
 */
export function MomentsStrip({ duration_ms, ranges, marks, breaks = [], starts = [], playhead, onSeek }: MomentsStripProps) {
  const { t } = useTranslation()
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / duration_ms) * 100))}%`
  const named = (label: string, suggested: boolean) => (suggested ? t('tagging.markers.suggested', { label }) : label)

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width <= 0) return
    const ms = ((event.clientX - box.left) / box.width) * duration_ms
    // Within 8 px of a comment or a set start counts as a tap on it.
    const near = (8 / box.width) * duration_ms
    const mark = [...marks, ...starts].sort((a, b) => Math.abs(a.t_ms - ms) - Math.abs(b.t_ms - ms))[0]
    onSeek(mark && Math.abs(mark.t_ms - ms) <= near ? mark.t_ms : ms)
  }

  return (
    <div
      className="relative h-7 cursor-pointer overflow-hidden rounded-lg bg-sunken ring-1 ring-hairline ring-inset"
      onClick={onClick}
      title={t('comments.strip')}
      aria-hidden="true"
      data-testid="moments-strip"
    >
      {breaks.map((b) => (
        <div
          key={b.id}
          data-marker={b.kind}
          data-suggested={b.suggested ? 'true' : undefined}
          title={named(b.label, b.suggested)}
          className={cx('absolute bottom-0.5 h-1.5 min-w-0.5 rounded-sm', breakClass(b.kind, b.suggested))}
          style={{ left: pct(b.start_ms), width: `max(2px, ${pct(b.end_ms - b.start_ms)})` }}
        />
      ))}
      {ranges.map((r) => (
        <div
          key={r.id}
          className={cx('absolute top-1 h-2.5 min-w-0.5 rounded-sm', r.shared ? 'bg-accent' : 'bg-fg/40')}
          style={{ left: pct(r.start_ms), width: `max(2px, ${pct(r.end_ms - r.start_ms)})` }}
        />
      ))}
      {starts.map((s) => (
        <div
          key={s.id}
          data-marker="set-start"
          data-suggested={s.suggested ? 'true' : undefined}
          title={named(s.label, s.suggested)}
          className={cx('absolute inset-y-0 w-1 -translate-x-1/2 border-l-2', s.suggested ? 'border-dashed border-fg/40' : 'border-fg')}
          style={{ left: pct(s.t_ms) }}
        />
      ))}
      {marks.map((m) => (
        <div
          key={m.id}
          className="absolute bottom-1 size-2.5 -translate-x-1/2 rotate-45 rounded-[2px] bg-link ring-1 ring-surface"
          style={{ left: pct(m.t_ms) }}
        />
      ))}
      <PlayheadMark playhead={playhead} duration_ms={duration_ms} />
    </div>
  )
}

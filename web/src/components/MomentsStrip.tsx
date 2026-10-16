import '../i18n/pages'
import type { MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import type { Playhead } from '../lib/playhead'
import { PlayheadMark } from './RallyPanel'

interface MomentsStripProps {
  duration_ms: number
  /** Clips as bands. */
  ranges: readonly { id: string; start_ms: number; end_ms: number; shared?: boolean }[]
  /** Comments pinned to a moment, as markers. */
  marks: readonly { id: string; t_ms: number }[]
  playhead: Playhead
  onSeek: (t_ms: number) => void
}

/**
 * Below the player: the whole video as a bar with the clips as bands and the comments as
 * markers (the native seek bar can't carry them). A tap near a comment goes to it; elsewhere,
 * to that point. The lists below are the keyboard way to the same places.
 */
export function MomentsStrip({ duration_ms, ranges, marks, playhead, onSeek }: MomentsStripProps) {
  const { t } = useTranslation()
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / duration_ms) * 100))}%`

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width <= 0) return
    const ms = ((event.clientX - box.left) / box.width) * duration_ms
    // Within 8 px of a comment counts as a tap on it.
    const near = (8 / box.width) * duration_ms
    const mark = [...marks].sort((a, b) => Math.abs(a.t_ms - ms) - Math.abs(b.t_ms - ms))[0]
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
      {ranges.map((r) => (
        <div
          key={r.id}
          className={cx('absolute top-1 h-2.5 min-w-0.5 rounded-sm', r.shared ? 'bg-accent' : 'bg-fg/40')}
          style={{ left: pct(r.start_ms), width: `max(2px, ${pct(r.end_ms - r.start_ms)})` }}
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

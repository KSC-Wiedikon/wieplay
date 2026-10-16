import '../i18n/pages'
import { useId, useMemo, useState, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  filterRallies,
  NO_FILTER,
  playLength,
  playWindows,
  PRE_ROLL_MS,
  rallyAt,
  type TagFilter,
} from '../lib/condensed'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { formatClock, sideLabel } from '../lib/labels'
import { usePlayhead, type Playhead } from '../lib/playhead'
import { ralliesOf } from '../lib/rallies'
import { readStorage, writeStorage } from '../lib/storage'
import type { Segment, Tagging } from '../lib/types'
import { useCondensedPlayback } from '../lib/useCondensedPlayback'
import { Badge } from './Badge'
import { Card } from './Card'
import { Switch } from './Switch'
import { Table, TBody, Td, Th, THead, Tr } from './Table'
import { TagFilters } from './TagFilters'

const SKIP_KEY = 'nomal-skip-dead-time'

interface RallyPanelProps {
  tagging: Tagging
  duration_ms: number | null
  element: HTMLVideoElement | null
  playhead: Playhead
  /** While a clip plays, skipping dead time waits. */
  suspended?: boolean
  /** Next to the switch, e.g. "Download as MP4" for coaches. */
  action?: ReactNode
}

/** Condensed playback (PLAN §9.5) for everyone who can watch: rallies only, a rally list, filters. */
export function RallyPanel({ tagging, duration_ms, element, playhead, suspended = false, action }: RallyPanelProps) {
  const { t } = useTranslation()
  const id = useId()
  const [skip, setSkip] = useState(() => readStorage(SKIP_KEY) !== 'off')
  const [filter, setFilter] = useState<TagFilter>(NO_FILTER)

  const rallies = useMemo(() => ralliesOf(tagging.segments), [tagging.segments])
  const shown = useMemo(
    () => filterRallies(rallies, tagging.events, tagging.periods, filter),
    [rallies, tagging.events, tagging.periods, filter],
  )
  const duration = duration_ms ?? rallies.at(-1)?.end_ms ?? null
  const windows = useMemo(() => playWindows(shown, { duration_ms: duration }), [shown, duration])
  useCondensedPlayback(element, windows, skip && !suspended)

  const currentId = usePlayhead(playhead, (s) => rallyAt(shown, s.t_ms)?.id ?? null)
  const numbers = useMemo(() => new Map(rallies.map((r, i) => [r.id, i + 1])), [rallies])
  const sets = useMemo(() => new Map(tagging.periods.map((p) => [p.id, p.no])), [tagging.periods])

  const toggle = (on: boolean) => {
    setSkip(on)
    writeStorage(SKIP_KEY, on ? 'on' : 'off')
  }

  const play = (rally: Segment) => {
    playhead.seek(rally.start_ms - PRE_ROLL_MS)
    playhead.play()
  }

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {t('tagging.condensed.title')}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold">
            <Switch checked={skip} onChange={toggle} label={t('tagging.condensed.skip')} />
            <span aria-hidden="true">{t('tagging.condensed.skip')}</span>
          </label>
          {action}
        </div>
      </div>
      <p className="text-sm text-muted">
        {skip && duration
          ? t('tagging.condensed.skipOn', { length: formatDuration(playLength(windows)), total: formatDuration(duration) })
          : t('tagging.condensed.skipOff')}
      </p>
      <TagFilters
        filter={filter}
        onChange={setFilter}
        taxonomy={tagging.taxonomy}
        tags={tagging.events}
        periods={tagging.periods}
        roster={tagging.roster}
      />
      {duration !== null && (
        <RallyStrip
          rallies={rallies}
          shown={shown}
          duration_ms={duration}
          currentId={currentId}
          playhead={playhead}
          onPick={play}
          onSeek={(ms) => playhead.seek(ms)}
        />
      )}
      {shown.length === 0 ? (
        <p className="text-sm text-muted">{t('tagging.condensed.none')}</p>
      ) : (
        <Card className="overflow-hidden">
          <div className="max-h-80 overflow-y-auto">
            <Table caption={t('tagging.condensed.title')}>
              <THead>
                <tr>
                  <Th>{t('tagging.condensed.rally')}</Th>
                  <Th>{t('tagging.filters.set')}</Th>
                  <Th className="hidden sm:table-cell">{t('tagging.condensed.time')}</Th>
                  <Th>{t('tagging.condensed.score')}</Th>
                  <Th>{t('tagging.condensed.point')}</Th>
                </tr>
              </THead>
              <TBody>
                {shown.map((rally) => {
                  const n = numbers.get(rally.id) ?? 0
                  const current = rally.id === currentId
                  const set = rally.period_id ? sets.get(rally.period_id) : undefined
                  return (
                    <Tr
                      key={rally.id}
                      onActivate={() => play(rally)}
                      aria-current={current ? 'true' : undefined}
                      className={cx(current && 'bg-accent-soft')}
                    >
                      <Td tight>
                        <button
                          type="button"
                          onClick={() => play(rally)}
                          className={cx(
                            'min-h-11 cursor-pointer rounded-lg px-1 text-left font-semibold link tabular-nums sm:min-h-9',
                            current && 'text-fg',
                          )}
                          aria-label={t('tagging.condensed.playRally', { n })}
                        >
                          {n}
                        </button>
                      </Td>
                      <Td className="tabular-nums">{set ?? '–'}</Td>
                      <Td className="hidden text-muted tabular-nums sm:table-cell">{formatClock(rally.start_ms, { tenths: false })}</Td>
                      <Td className="font-semibold tabular-nums">
                        {rally.score_us_after !== null && rally.score_them_after !== null
                          ? `${rally.score_us_after}:${rally.score_them_after}`
                          : '–'}
                      </Td>
                      <Td>{rally.won_by ? <Badge tone={rally.won_by === 'us' ? 'accent' : 'neutral'}>{sideLabel(t, rally.won_by)}</Badge> : '–'}</Td>
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          </div>
        </Card>
      )}
    </section>
  )
}

interface RallyStripProps {
  rallies: readonly Segment[]
  shown: readonly Segment[]
  duration_ms: number
  currentId: string | null
  playhead: Playhead
  onPick: (rally: Segment) => void
  onSeek: (ms: number) => void
}

/** The whole video as a bar: rallies are bands, the rest is dead time. Click to jump. */
function RallyStrip({ rallies, shown, duration_ms, currentId, playhead, onPick, onSeek }: RallyStripProps) {
  const { t } = useTranslation()
  const visible = useMemo(() => new Set(shown.map((r) => r.id)), [shown])
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / duration_ms) * 100))}%`

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width <= 0) return
    const ms = ((event.clientX - box.left) / box.width) * duration_ms
    const rally = rallies.find((r) => visible.has(r.id) && r.start_ms - 1000 <= ms && ms <= r.end_ms + 1000)
    if (rally) onPick(rally)
    else onSeek(ms)
  }

  return (
    // Mouse shortcut only; the rally list below is the keyboard way to the same places.
    <div
      className="relative h-8 cursor-pointer overflow-hidden rounded-lg bg-sunken ring-1 ring-hairline ring-inset"
      onClick={onClick}
      title={t('tagging.condensed.strip')}
      aria-hidden="true"
    >
      {rallies.map((r) => (
        <div
          key={r.id}
          className={cx(
            'absolute inset-y-1 min-w-0.5 rounded-sm',
            visible.has(r.id) ? 'bg-accent' : 'bg-muted/30',
            r.id === currentId && 'inset-y-0 ring-2 ring-fg',
          )}
          style={{ left: pct(r.start_ms), width: `max(2px, ${pct(r.end_ms - r.start_ms)})` }}
        />
      ))}
      <PlayheadMark playhead={playhead} duration_ms={duration_ms} />
    </div>
  )
}

export function PlayheadMark({ playhead, duration_ms }: { playhead: Playhead; duration_ms: number }) {
  const t_ms = usePlayhead(playhead, (s) => s.t_ms)
  return (
    <div
      className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-danger"
      style={{ left: `${Math.min(100, Math.max(0, (t_ms / duration_ms) * 100))}%` }}
    />
  )
}

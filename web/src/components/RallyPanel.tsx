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
import { ApiError } from '../lib/api'
import { formatClock, sideLabel } from '../lib/labels'
import { usePlayhead, type Playhead } from '../lib/playhead'
import { ralliesOf } from '../lib/rallies'
import { fullNameOf, useSideNames } from '../lib/sides'
import { readStorage, writeStorage } from '../lib/storage'
import { effectiveWinner, scoreAfter } from '../lib/suggestions'
import { useToast } from '../lib/toast'
import type { Segment, Side, Tagging } from '../lib/types'
import { useCondensedPlayback } from '../lib/useCondensedPlayback'
import { useSetWinner } from '../lib/winner'
import { Badge } from './Badge'
import { Button } from './Button'
import { Card } from './Card'
import { UndoIcon } from './icons'
import { ScoreHead, SuggestedValue } from './ScoringMarks'
import { Switch } from './Switch'
import { Table, TBody, Td, Th, THead, Tr } from './Table'
import { TagFilters } from './TagFilters'
import { WinnerPicker } from './WinnerPicker'

const SKIP_KEY = 'nomal-skip-dead-time'

interface RallyPanelProps {
  tagging: Tagging
  duration_ms: number | null
  element: HTMLVideoElement | null
  playhead: Playhead
  /** While a clip plays, skipping dead time waits. */
  suspended?: boolean
  /** Next to the switch. */
  action?: ReactNode
  /** Coaches set who won a rally here (the video's id): a fact the score line follows. */
  editVideoId?: string | null
}

/** The last winner set here, to take back. */
interface LastCall {
  rally: Segment
  n: number
  before: Side | null
  after: Side | null
}

/** Condensed playback (PLAN §9.5) for everyone who can watch: rallies only, a rally list, filters. */
export function RallyPanel({ tagging, duration_ms, element, playhead, suspended = false, action, editVideoId = null }: RallyPanelProps) {
  const { t } = useTranslation()
  const id = useId()
  const names = useSideNames()
  const toast = useToast()
  const setWinner = useSetWinner(editVideoId ?? '')
  const [last, setLast] = useState<LastCall | null>(null)
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

  // Shown at once (the score line too), with a way back.
  const pick = (rally: Segment, n: number, won_by: Side | null, undo = false) => {
    setLast(undo ? null : { rally, n, before: rally.won_by ?? null, after: won_by })
    setWinner.mutate(
      { rally, won_by },
      {
        onError: (error) => {
          setLast(null)
          toast.error(t('winner.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') }))
        },
      },
    )
  }
  const undo = () => {
    if (!last) return
    const now = tagging.segments.find((s) => s.id === last.rally.id)
    if (now) pick(now, last.n, last.before, true)
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
                  <ScoreHead />
                  <Th>{t('tagging.condensed.point')}</Th>
                </tr>
              </THead>
              <TBody>
                {shown.map((rally) => {
                  const n = numbers.get(rally.id) ?? 0
                  const current = rally.id === currentId
                  const set = rally.period_id ? sets.get(rally.period_id) : undefined
                  // Where no coach decided the point, Nomal's winner fills the score line (muted).
                  const score = scoreAfter(rally)
                  const winner = effectiveWinner(rally)
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
                        {score ? <SuggestedValue suggested={score.suggested}>{score.text}</SuggestedValue> : '–'}
                      </Td>
                      {editVideoId ? (
                        <Td tight>
                          <WinnerPicker rally={rally} n={n} names={names} disabled={setWinner.isPending} onPick={(won_by) => pick(rally, n, won_by)} />
                        </Td>
                      ) : (
                        <Td>
                          {winner.value === null ? (
                            '–'
                          ) : winner.suggested ? (
                            <SuggestedValue suggested>{sideLabel(t, winner.value, names)}</SuggestedValue>
                          ) : (
                            <Badge tone={winner.value === 'us' ? 'accent' : 'neutral'} title={names ? fullNameOf(names, winner.value) : undefined}>
                              {sideLabel(t, winner.value, names)}
                            </Badge>
                          )}
                        </Td>
                      )}
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          </div>
        </Card>
      )}
      {last && (
        <p role="status" className="flex flex-wrap items-center gap-x-2 text-sm">
          <span>
            {last.after
              ? t('winner.changed', { n: last.n, team: sideLabel(t, last.after, names) })
              : t('winner.cleared', { n: last.n })}
          </span>
          <Button variant="ghost" onClick={undo} disabled={setWinner.isPending}>
            <UndoIcon className="size-4" />
            {t('winner.undo')}
          </Button>
        </p>
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

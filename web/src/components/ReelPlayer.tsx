import '../i18n/pages'
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { formatDate, formatDuration } from '../lib/format'
import { skillByCode } from '../lib/grammar'
import { formatClock, periodLabel, pieceFacts, pieceLabel } from '../lib/labels'
import { useTagging } from '../lib/queries'
import { scoreBefore, shotScoreAt } from '../lib/rallies'
import { ReelEngine } from '../lib/reelEngine'
import { elapsedIn, locate, reelTimeline } from '../lib/reelPlayer'
import type { Piece, Taxonomy } from '../lib/types'
import { Button } from './Button'
import { Card } from './Card'
import { PauseIcon, PlayIcon, SkipBackIcon, SkipForwardIcon } from './icons'
import { Spinner } from './Spinner'
import { Table, TBody, Td, Th, THead, Tr } from './Table'

interface ReelPlayerProps {
  pieces: readonly Piece[]
  /** For the video element's name. */
  title: string
  /** Skill and grade names until the current video's own tagging data is in. */
  taxonomy?: Taxonomy | null
}

/**
 * Plays a reel's pieces one after another across videos (PLAN §7.7): two players, one on
 * screen and one loading the next video. Below: where we are, the caption, the score at that
 * moment (from the video's tags), and the list of moments to jump to, each with its player and
 * the score after its rally.
 */
export function ReelPlayer({ pieces, title, taxonomy: fallback = null }: ReelPlayerProps) {
  const { t } = useTranslation()
  const [engine] = useState(() => new ReelEngine())
  const state = useSyncExternalStore(engine.subscribe, engine.getState)
  const ref0 = useCallback((element: HTMLVideoElement | null) => engine.attach(0, element), [engine])
  const ref1 = useCallback((element: HTMLVideoElement | null) => engine.attach(1, element), [engine])
  useEffect(() => {
    engine.setPieces(pieces)
    return () => engine.reset()
  }, [engine, pieces])

  const piece = pieces[state.index]
  const timeline = useMemo(() => reelTimeline(pieces), [pieces])
  const elapsed = piece ? elapsedIn(pieces, state.index, Math.max(state.t_ms, piece.start_ms)) : 0

  // The current video's tags: skill names and the score at this moment.
  const tagging = useTagging(piece?.video_id ?? '', piece !== undefined)
  const taxonomy = tagging.data?.taxonomy ?? fallback
  const label = (p: Piece) => pieceLabel(t, p, taxonomy && p.code ? skillByCode(taxonomy, p.code) : null)
  const event = piece?.event_id ? tagging.data?.events.find((e) => e.id === piece.event_id) : undefined
  const at = event?.t_ms ?? state.t_ms
  const data = tagging.data
  // Basketball counts the made shots (its tags); volleyball the rallies.
  const score = !data
    ? null
    : data.sport === 'basketball' && data.taxonomy
      ? shotScoreAt(at, data.periods, data.events, data.taxonomy)
      : scoreBefore(at, data.periods, data.segments)

  const list = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const row = list.current?.querySelector<HTMLElement>(`[data-piece="${state.index}"]`)
    const box = list.current
    if (!row || !box) return
    if (row.offsetTop < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTop = row.offsetTop - box.clientHeight / 2
    }
  }, [state.index])

  const onBar = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width <= 0) return
    const target = locate(pieces, ((event.clientX - box.left) / box.width) * timeline.total_ms)
    if (target) engine.jump(target.index, target.t_ms)
  }

  if (pieces.length === 0) return null
  const videoClass = 'aspect-video w-full'
  return (
    <section aria-label={t('reels.player.label')} className="flex flex-col gap-3">
      <div className="relative overflow-hidden rounded-2xl bg-navy">
        {([0, 1] as const).map((no) => (
          <video
            key={no}
            ref={no === 0 ? ref0 : ref1}
            controls={state.active === no}
            playsInline
            preload="auto"
            aria-label={title}
            aria-hidden={state.active !== no}
            className={state.active === no ? videoClass : 'hidden'}
          />
        ))}
        {state.waiting && (
          <div className="absolute inset-0 grid place-items-center bg-navy/60 text-light">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <Spinner className="text-light" label={t('reels.player.loading')} />
              {t('reels.player.loading')}
            </span>
          </div>
        )}
        {state.error && !state.waiting && (
          <div role="alert" className="absolute inset-x-0 bottom-0 bg-navy/80 p-3 text-sm text-light">
            {t('reels.player.error')}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="icon" onClick={engine.previous} disabled={state.index === 0 && state.t_ms <= (piece?.start_ms ?? 0) + 3000} aria-label={t('reels.player.previous')}>
          <SkipBackIcon />
        </Button>
        <Button variant="primary" size="icon" onClick={engine.toggle} aria-label={state.playing ? t('reels.player.pause') : t('reels.player.play')}>
          {state.playing ? <PauseIcon /> : <PlayIcon />}
        </Button>
        <Button size="icon" onClick={engine.next} disabled={state.index + 1 >= pieces.length} aria-label={t('reels.player.next')}>
          <SkipForwardIcon />
        </Button>
        <p className="text-sm font-semibold tabular-nums">{t('reels.player.position', { n: state.index + 1, total: pieces.length })}</p>
        <p className="ml-auto text-sm text-muted tabular-nums">
          {t('reels.player.time', { time: formatDuration(elapsed), total: formatDuration(timeline.total_ms) })}
        </p>
      </div>
      {/* Mouse and touch shortcut; the list below is the keyboard way to the same places. */}
      <div
        className="relative h-3 cursor-pointer overflow-hidden rounded-full bg-sunken ring-1 ring-hairline ring-inset"
        onClick={onBar}
        title={t('reels.player.progress')}
        aria-hidden="true"
      >
        <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${timeline.total_ms ? (elapsed / timeline.total_ms) * 100 : 0}%` }} />
        {timeline.starts.slice(1).map((start, i) => (
          <div key={i} className="absolute inset-y-0 w-px bg-surface" style={{ left: `${(start / timeline.total_ms) * 100}%` }} />
        ))}
      </div>

      {piece && (
        <div className="flex flex-col gap-0.5" aria-live="polite">
          <p className="font-semibold wrap-break-word">{label(piece)}</p>
          <p className="text-sm text-muted">
            {[
              piece.video_title,
              piece.recorded_on ? formatDate(piece.recorded_on) : null,
              score?.period && data ? `${periodLabel(t, data.sport, score.period.no)} · ${score.us}:${score.them}` : null,
              t('reels.player.inVideo', { time: formatClock(at, { tenths: false }) }),
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
          {state.ended && <p className="text-sm font-semibold">{t('reels.player.finished')}</p>}
        </div>
      )}

      <Card className="overflow-hidden">
        <div ref={list} className="relative max-h-80 overflow-y-auto">
          <Table caption={t('reels.player.moments')}>
            <THead className="sticky top-0 z-10 bg-surface">
              <tr>
                <Th className="w-0">#</Th>
                <Th>{t('reels.player.moments')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.date')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.duration')}</Th>
              </tr>
            </THead>
            <TBody>
              {pieces.map((p, index) => {
                const current = index === state.index
                const facts = pieceFacts(p)
                const where = [p.recorded_on ? formatDate(p.recorded_on) : null, p.video_title].filter(Boolean).join(' · ')
                return (
                  <Tr
                    key={index}
                    data-piece={index}
                    onActivate={() => void engine.show(index, true)}
                    aria-current={current ? 'true' : undefined}
                    className={cx(current && 'bg-sunken')}
                  >
                    <Td className="text-muted tabular-nums">{index + 1}</Td>
                    <Td tight>
                      <button
                        type="button"
                        onClick={() => void engine.show(index, true)}
                        aria-label={t('reels.player.playMoment', { n: index + 1 })}
                        className={cx(
                          'min-h-11 cursor-pointer rounded-lg px-1 text-left font-semibold text-link underline-offset-2 hover:underline sm:min-h-9',
                          current && 'text-fg',
                        )}
                      >
                        {label(p)}
                      </button>
                      <span className={cx('block px-1 pb-1 text-xs text-muted tabular-nums', !facts && 'sm:hidden')}>
                        {facts}
                        <span className="sm:hidden">{facts && where ? ` · ${where}` : where}</span>
                      </span>
                    </Td>
                    <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
                      {p.recorded_on ? formatDate(p.recorded_on) : '–'}
                      <span className="block max-w-56 truncate text-xs text-muted">{p.video_title}</span>
                    </Td>
                    <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDuration(p.end_ms - p.start_ms)}</Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        </div>
      </Card>
    </section>
  )
}

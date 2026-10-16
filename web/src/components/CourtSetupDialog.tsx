import '../i18n/pages'
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import {
  cameraMoves,
  CORNERS,
  GOOD_FIT_CM,
  LANDMARKS,
  listOf,
  nextLandmark,
  pointsOf,
  solveCourt,
  useRemoveCourt,
  useSaveCourt,
  withoutPoint,
  type CalibrationSegment,
  type CourtState,
  type Landmark,
  type SetupPoints,
  type Solution,
  type Suggestion,
} from '../lib/court'
import { cx } from '../lib/cx'
import { formatDate } from '../lib/format'
import { formatClock } from '../lib/labels'
import { useToast } from '../lib/toast'
import { Badge } from './Badge'
import { Button } from './Button'
import { Modal } from './Modal'

const SOLVE_DELAY_MS = 200
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

interface CourtSetupDialogProps {
  open: boolean
  onClose: () => void
  videoId: string
  state: CourtState
  /** The player: its paused frame is the picture to click on. */
  element: HTMLVideoElement | null
}

/**
 * "Set up the court" (PLAN §10.6): click the court's landmarks on the paused frame; the server
 * fits the homography, the lines come back drawn over the picture with the fit's error.
 * Pre-filled from the video's calibration (the court job's, to check, says where it came from),
 * else from the court job's guess or the team's last one. When the camera moved during the match,
 * each setup is checked on a frame of its own (the video seeks there); the setups nobody set
 * follow the coach's changes.
 */
export function CourtSetupDialog({ open, onClose, videoId, state, element }: CourtSetupDialogProps) {
  const { t } = useTranslation()
  return (
    <Modal open={open} title={t('court.setup.title')} onClose={onClose} size="xl">
      <Setup videoId={videoId} state={state} element={element} onClose={onClose} />
    </Modal>
  )
}

/** A setup's court as a solution to show (none when the camera showed no court then). */
function segmentSolution(seg: CalibrationSegment): Solution | null {
  if (!seg.image_to_court || !seg.court_to_image) return null
  return {
    image_to_court: seg.image_to_court,
    court_to_image: seg.court_to_image,
    camera: seg.camera,
    error_cm: seg.error_cm,
    max_error_cm: seg.max_error_cm,
    residuals_cm: {},
    lines: seg.lines,
  }
}

/** Moves the paused video into a setup (its middle, at most five minutes in): its own frame. */
function showSetup(video: HTMLVideoElement, seg: CalibrationSegment): void {
  const end = seg.to_ms ?? (Number.isFinite(video.duration) ? video.duration * 1000 : seg.from_ms + 60_000)
  video.currentTime = (seg.from_ms + Math.min(end - seg.from_ms, 10 * 60_000) / 2) / 1000
}

function solvedOf(points: CourtSetupDialogProps['state']['suggestions'][number]['points'], solution: Solution | null) {
  return { key: JSON.stringify(listOf(pointsOf(points))), solution, problem: null }
}

function Setup({ videoId, state, element, onClose }: Omit<CourtSetupDialogProps, 'open'>) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const save = useSaveCourt(videoId)
  const remove = useRemoveCourt(videoId)
  const calibration = state.calibration
  const segments = calibration?.segments && calibration.segments.length > 1 ? calibration.segments : null
  // The setup of the paused frame (the picture to click on), else the main one.
  const [segment, setSegment] = useState<number | null>(() => {
    if (!segments) return null
    const at = element && Number.isFinite(element.currentTime) ? element.currentTime * 1000 : null
    const here = at === null ? -1 : segments.findIndex((s) => s.from_ms <= at && (s.to_ms === null || at < s.to_ms))
    return here >= 0 ? here : Math.max(0, segments.findIndex((s) => s.main))
  })
  const initial = segments && segment !== null ? segments[segment] : null
  const [source, setSource] = useState<Suggestion | null>(() => (calibration ? null : (state.suggestions[0] ?? null)))
  const [points, setPoints] = useState<SetupPoints>(() => pointsOf(initial?.points ?? calibration?.points ?? state.suggestions[0]?.points ?? []))
  const [dirty, setDirty] = useState(false)
  const [active, setActive] = useState<Landmark | null>(() => nextLandmark(points) ?? 'near_left')
  // The server's answer for a set of points (keyed by them): shown while it is the current set.
  const [solved, setSolved] = useState<{ key: string; solution: Solution | null; problem: string | null } | null>(() =>
    initial ? solvedOf(initial.points, segmentSolution(initial)) : calibration ? solvedOf(calibration.points, calibration) : null,
  )
  const [hasFrame, setHasFrame] = useState(true)
  const canvas = useRef<HTMLCanvasElement>(null)
  const area = useRef<SVGSVGElement>(null)
  const dragging = useRef<Landmark | null>(null)
  const aspect = element && element.videoWidth > 0 ? element.videoWidth / element.videoHeight : state.aspect

  // The paused frame (a cross-origin frame can be drawn, only not read back); again after the
  // video moved to another camera setup.
  const draw = useCallback(() => {
    const c = canvas.current
    if (!c || !element || element.readyState < 2 || element.videoWidth === 0) {
      setHasFrame(false)
      return
    }
    c.width = element.videoWidth
    c.height = element.videoHeight
    try {
      const ctx = c.getContext('2d')
      ctx?.drawImage(element, 0, 0, c.width, c.height)
      setHasFrame(ctx !== null)
    } catch {
      setHasFrame(false)
    }
  }, [element])
  useEffect(() => {
    element?.pause()
    draw()
  }, [element, draw])
  useEffect(() => {
    if (!element) return
    element.addEventListener('seeked', draw)
    return () => element.removeEventListener('seeked', draw)
  }, [element, draw])

  const pickSegment = (index: number) => {
    const seg = segments?.[index]
    if (!seg) return
    setSegment(index)
    const next = pointsOf(seg.points)
    setPoints(next)
    setSolved(solvedOf(seg.points, segmentSolution(seg)))
    setDirty(false)
    setActive(nextLandmark(next) ?? 'near_left')
    if (element) showSetup(element, seg)
  }

  const list = useMemo(() => listOf(points), [points])
  const key = JSON.stringify(list)
  useEffect(() => {
    if (list.length < 4 || solved?.key === key) return
    const abort = new AbortController()
    const timer = setTimeout(() => {
      solveCourt(videoId, list, abort.signal)
        .then((solution) => setSolved({ key, solution, problem: null }))
        .catch((error: unknown) => {
          if (abort.signal.aborted) return
          setSolved({ key, solution: null, problem: error instanceof ApiError ? error.detail : t('common.error') })
        })
    }, SOLVE_DELAY_MS)
    return () => {
      clearTimeout(timer)
      abort.abort()
    }
  }, [list, key, solved?.key, videoId, t])
  const current = list.length >= 4 && solved?.key === key ? solved : null
  const solution = current?.solution ?? null
  const problem = current?.problem ?? null
  const solving = list.length >= 4 && current === null

  const place = useCallback(
    (name: Landmark, at: [number, number], advance: boolean) => {
      setDirty(true)
      setPoints((old) => {
        const next = { ...old, [name]: at }
        if (advance) setActive(nextLandmark(next, name) ?? name)
        return next
      })
    },
    [],
  )

  const at = (event: PointerEvent): [number, number] | null => {
    const box = area.current?.getBoundingClientRect()
    if (!box || box.width === 0 || box.height === 0) return null
    return [clamp01((event.clientX - box.left) / box.width), clamp01((event.clientY - box.top) / box.height)]
  }

  const onPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    const target = (event.target as Element).closest('[data-landmark]')
    const grabbed = target?.getAttribute('data-landmark') as Landmark | null
    if (grabbed) {
      dragging.current = grabbed
      setActive(grabbed)
      event.currentTarget.setPointerCapture?.(event.pointerId)
      return
    }
    const p = at(event)
    if (p && active) place(active, p, true)
  }

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const name = dragging.current
    if (!name) return
    const p = at(event)
    if (p) place(name, p, false)
  }

  const onPointerUp = () => {
    dragging.current = null
  }

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    if (!active) return
    const p = points[active]
    if ((event.key === 'Delete' || event.key === 'Backspace') && p) {
      event.preventDefault()
      setDirty(true)
      setPoints((old) => withoutPoint(old, active))
      return
    }
    const box = area.current?.getBoundingClientRect()
    const step = (event.shiftKey ? 10 : 1) / Math.max(1, box?.width ?? 1000)
    const stepY = (event.shiftKey ? 10 : 1) / Math.max(1, box?.height ?? 562)
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -stepY],
      ArrowDown: [0, stepY],
    }
    const move = moves[event.key]
    if (!move || !p) return
    event.preventDefault()
    place(active, [clamp01(p[0] + move[0]), clamp01(p[1] + move[1])], false)
  }

  const adopt = (suggestion: Suggestion) => {
    setSource(suggestion)
    const next = pointsOf(suggestion.points)
    setPoints(next)
    setDirty(false)
    setActive(nextLandmark(next) ?? 'near_left')
  }

  const picked = segments && segment !== null ? segments[segment] : null
  const keptMethod = picked ? picked.method : calibration?.method
  const onSave = () =>
    save.mutate(
      {
        points: list,
        method: dirty
          ? 'manual'
          : source
            ? source.source === 'team'
              ? 'copied'
              : source.source
            : keptMethod === 'auto' || keptMethod === 'auto-hall' || keptMethod === 'copied'
              ? keptMethod
              : 'manual',
        copied_from: !dirty && source?.source === 'team' ? source.calibration_id : null,
        segment: segments ? segment : null,
        at_ms: element && Number.isFinite(element.currentTime) ? Math.round(element.currentTime * 1000) : null,
      },
      {
        onSuccess: () => {
          toast.success(t('court.setup.saved'))
          onClose()
        },
        onError: (error) => toast.error(t('court.setup.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
      },
    )

  const onRemove = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        toast.success(t('court.setup.removed'))
        onClose()
      },
      onError: (error) => toast.error(t('court.setup.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
    })

  const error = solution?.error_cm ?? null
  const other = state.suggestions.find((s) => s !== source)
  const pct = (c: number | null | undefined) => (c === null || c === undefined ? null : t('court.setup.confidence', { pct: Math.round(c * 100) }))
  // Where the court came from: the court job (to check), a coach, a suggestion.
  const origin =
    calibration && !dirty
      ? calibration.checked === false
        ? { badge: 'auto', text: calibration.method === 'auto-hall' ? t('court.setup.autoHall') : t('court.setup.autoFound'), extra: pct(calibration.confidence) }
        : { badge: null, text: t('court.setup.yours'), extra: null }
      : null
  const allSetups = segments ?? state.setups ?? []
  const moves = cameraMoves(allSetups)
  const setupCount = allSetups.length
  const stateOf = (seg: CalibrationSegment) =>
    seg.method === 'none' ? 'none' : seg.checked ? 'checked' : seg.method === 'derived' ? 'derived' : 'auto'

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{t('court.setup.intro')}</p>
      {origin && (
        <p className="flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-sunken p-3 text-sm" role="status">
          {origin.badge && <Badge>{origin.badge}</Badge>}
          {origin.text}
          {origin.extra && <span className="text-muted">{`(${origin.extra})`}</span>}
        </p>
      )}
      {source && !dirty && (
        <p className="flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-sunken p-3 text-sm" role="status">
          <Badge>{source.source === 'team' ? formatDate(source.recorded_on) : 'auto'}</Badge>
          {source.source === 'team'
            ? t('court.setup.fromTeam', { title: source.video_title ?? '', date: formatDate(source.recorded_on) })
            : (source.confidence ?? 0) >= 0.6
              ? source.source === 'auto-hall'
                ? t('court.setup.autoHall')
                : t('court.setup.auto')
              : t('court.setup.guess')}
          {source.source !== 'team' && pct(source.confidence) && <span className="text-muted">{`(${pct(source.confidence)})`}</span>}
        </p>
      )}
      {setupCount > 1 && (
        <p className="text-sm" role="note">
          {moves.length > 0
            ? t('court.setup.moved', { count: setupCount, at: moves.map((ms) => formatClock(ms, { tenths: false })).join(', ') })
            : t('court.setup.drifted', { count: setupCount })}
        </p>
      )}
      {segments && (
        <div className="flex flex-col gap-1.5">
          <h3 id={`${id}-setups`} className="text-sm font-semibold">
            {t('court.setup.setups')}
          </h3>
          <p className="text-xs text-muted">{t('court.setup.setupsHint')}</p>
          <ul aria-labelledby={`${id}-setups`} className="flex flex-wrap gap-1.5">
            {segments.map((seg, k) => (
              <li key={seg.index}>
                <button
                  type="button"
                  aria-pressed={segment === k}
                  onClick={() => pickSegment(k)}
                  className={cx(
                    'flex min-h-11 cursor-pointer flex-col items-start rounded-control border px-2.5 py-1 text-left text-sm sm:min-h-9',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
                    segment === k ? 'border-transparent bg-accent-soft text-on-accent-soft' : 'border-hairline bg-surface hover:bg-hover',
                  )}
                >
                  <span className="font-semibold tabular-nums">
                    {t('court.setup.span', {
                      from: formatClock(seg.from_ms, { tenths: false }),
                      to: seg.to_ms === null ? t('court.setup.end') : formatClock(seg.to_ms, { tenths: false }),
                    })}
                  </span>
                  <span className="text-xs text-muted">{t(`court.setup.setupState.${stateOf(seg)}`)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {other && (
        <div>
          <Button variant="ghost" onClick={() => adopt(other)}>
            {`${t('court.setup.useSuggestion')}: ${other.source === 'auto' ? 'auto' : `${other.video_title ?? ''} (${formatDate(other.recorded_on)})`}`}
          </Button>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="relative w-full overflow-hidden rounded-xl bg-navy" style={{ aspectRatio: String(aspect) }}>
            <canvas ref={canvas} className="absolute inset-0 size-full" aria-hidden="true" />
            {!hasFrame && (
              <p className="absolute inset-x-0 top-0 m-3 rounded-lg bg-surface/90 p-2 text-sm text-fg">
                {element && element.readyState >= 2 ? t('court.setup.noImage') : t('court.setup.noFrame')}
              </p>
            )}
            <svg
              ref={area}
              role="application"
              aria-label={`${t('court.setup.image')}. ${active ? t('court.setup.place', { name: t(`court.setup.names.${active}`) }) : ''}. ${t('court.setup.nudge')}`}
              tabIndex={0}
              className="absolute inset-0 size-full cursor-crosshair touch-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onKeyDown={onKeyDown}
            >
              {solution?.lines.map((line, i) =>
                line.slice(1).map((p, k) => {
                  const a = line[k]
                  if (!a) return null
                  return (
                    <line
                      key={`${i}-${k}`}
                      x1={`${a[0] * 100}%`}
                      y1={`${a[1] * 100}%`}
                      x2={`${p[0] * 100}%`}
                      y2={`${p[1] * 100}%`}
                      stroke="#ff4fd8"
                      strokeWidth={2}
                      strokeLinecap="round"
                      data-testid="court-line"
                    />
                  )
                }),
              )}
              {list.map((p) => {
                const selected = p.landmark === active
                const corner = CORNERS.includes(p.landmark)
                return (
                  <g key={p.landmark} data-landmark={p.landmark} className="cursor-grab">
                    <circle cx={`${p.u * 100}%`} cy={`${p.v * 100}%`} r={selected ? 11 : 8} fill="rgb(19 32 58 / 0.35)" stroke="#ffffff" strokeWidth={selected ? 3 : 2} />
                    <circle cx={`${p.u * 100}%`} cy={`${p.v * 100}%`} r={2.5} fill={corner ? '#ffc72c' : '#7fb2ff'} />
                  </g>
                )
              })}
            </svg>
          </div>
          <p className="min-h-5 text-sm" role="status" aria-live="polite">
            {problem ? (
              <span className="text-danger">{problem}</span>
            ) : list.length < 4 ? (
              <span className="text-muted">{t('court.setup.need')}</span>
            ) : solving && !solution ? (
              <span className="text-muted">{t('court.setup.solving')}</span>
            ) : error === null ? (
              <span className="text-muted">{t('court.setup.exact')}</span>
            ) : (
              <span className={cx('font-semibold', error > GOOD_FIT_CM ? 'text-danger' : 'text-fg')}>
                {`${t('court.setup.fit', { cm: error.toLocaleString('de-CH', { maximumFractionDigits: 1 }), max: (solution?.max_error_cm ?? error).toLocaleString('de-CH', { maximumFractionDigits: 1 }) })} · ${error > GOOD_FIT_CM ? t('court.setup.fitPoor') : t('court.setup.fitGood')}`}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <h3 id={`${id}-points`} className="text-sm font-semibold">
            {t('court.setup.landmarks')}
          </h3>
          <ol aria-labelledby={`${id}-points`} className="flex flex-col gap-1">
            {LANDMARKS.map((name) => {
              const placed = points[name] !== undefined
              const required = CORNERS.includes(name)
              return (
                <li key={name}>
                  <button
                    type="button"
                    aria-pressed={active === name}
                    onClick={() => setActive(name)}
                    className={cx(
                      'flex min-h-11 w-full cursor-pointer items-center justify-between gap-2 rounded-control border px-2.5 text-left text-sm sm:min-h-9',
                      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
                      active === name ? 'border-transparent bg-accent-soft text-on-accent-soft' : 'border-hairline bg-surface hover:bg-hover',
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span aria-hidden="true" className={cx('size-2.5 shrink-0 rounded-full', placed ? 'bg-accent' : 'border border-hairline-strong')} />
                      <span className={cx(required && 'font-semibold')}>{t(`court.setup.names.${name}`)}</span>
                    </span>
                    <span className="text-xs text-muted">{placed ? t('court.setup.placed') : required ? t('court.setup.required') : t('court.setup.optional')}</span>
                  </button>
                </li>
              )
            })}
          </ol>
          <p className="text-xs text-muted">{t('court.setup.extra')}</p>
          <div className="flex flex-wrap gap-2">
            {active && points[active] && (
              <Button
                variant="ghost"
                onClick={() => {
                  setDirty(true)
                  setPoints((old) => withoutPoint(old, active))
                }}
              >
                {t('court.setup.clear')}
              </Button>
            )}
            {active && !points[active] && (
              <Button variant="ghost" onClick={() => setActive(nextLandmark(points, active))}>
                {t('court.setup.skip')}
              </Button>
            )}
            {list.length > 0 && (
              <Button
                variant="ghost"
                onClick={() => {
                  setDirty(true)
                  setPoints({})
                  setActive('near_left')
                }}
              >
                {t('court.setup.clearAll')}
              </Button>
            )}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        {state.calibration && (
          <Button variant="ghostDanger" onClick={onRemove} disabled={remove.isPending} className="mr-auto">
            {t('court.setup.remove')}
          </Button>
        )}
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button variant="primary" onClick={onSave} disabled={!solution || problem !== null || save.isPending}>
          {t('court.setup.save')}
        </Button>
      </div>
    </div>
  )
}

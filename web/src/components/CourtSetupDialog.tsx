import '../i18n/pages'
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import {
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
  type CourtState,
  type Landmark,
  type SetupPoints,
  type Solution,
  type Suggestion,
} from '../lib/court'
import { cx } from '../lib/cx'
import { formatDate } from '../lib/format'
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
 * Pre-filled from the video's calibration, else from the court finder or the team's last one.
 */
export function CourtSetupDialog({ open, onClose, videoId, state, element }: CourtSetupDialogProps) {
  const { t } = useTranslation()
  return (
    <Modal open={open} title={t('court.setup.title')} onClose={onClose} size="xl">
      <Setup videoId={videoId} state={state} element={element} onClose={onClose} />
    </Modal>
  )
}

function Setup({ videoId, state, element, onClose }: Omit<CourtSetupDialogProps, 'open'>) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const save = useSaveCourt(videoId)
  const remove = useRemoveCourt(videoId)
  const [source, setSource] = useState<Suggestion | null>(() => (state.calibration ? null : (state.suggestions[0] ?? null)))
  const [points, setPoints] = useState<SetupPoints>(() => pointsOf(state.calibration?.points ?? state.suggestions[0]?.points ?? []))
  const [dirty, setDirty] = useState(false)
  const [active, setActive] = useState<Landmark | null>(() => nextLandmark(points) ?? 'near_left')
  // The server's answer for a set of points (keyed by them): shown while it is the current set.
  const [solved, setSolved] = useState<{ key: string; solution: Solution | null; problem: string | null } | null>(() =>
    state.calibration ? { key: JSON.stringify(listOf(pointsOf(state.calibration.points))), solution: state.calibration, problem: null } : null,
  )
  const [hasFrame, setHasFrame] = useState(true)
  const canvas = useRef<HTMLCanvasElement>(null)
  const area = useRef<SVGSVGElement>(null)
  const dragging = useRef<Landmark | null>(null)
  const aspect = element && element.videoWidth > 0 ? element.videoWidth / element.videoHeight : state.aspect

  // The paused frame, drawn once (a cross-origin frame can be drawn, only not read back).
  useEffect(() => {
    element?.pause()
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

  const onSave = () =>
    save.mutate(
      {
        points: list,
        method: dirty || !source ? 'manual' : source.source === 'auto' ? 'auto' : 'copied',
        copied_from: !dirty && source?.source === 'team' ? source.calibration_id : null,
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

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{t('court.setup.intro')}</p>
      {source && !dirty && (
        <p className="flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-sunken p-3 text-sm" role="status">
          <Badge>{source.source === 'auto' ? 'auto' : formatDate(source.recorded_on)}</Badge>
          {source.source === 'auto'
            ? t('court.setup.auto')
            : t('court.setup.fromTeam', { title: source.video_title ?? '', date: formatDate(source.recorded_on) })}
        </p>
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

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode, type WheelEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { FitIcon, ZoomInIcon, ZoomOutIcon } from '../../components/icons'
import { cx } from '../../lib/cx'

const MIN = 1
const MAX = 8

interface View {
  scale: number
  x: number
  y: number
}

const FIT: View = { scale: 1, x: 0, y: 0 }

/**
 * One photo of the paper sheet, zoomed and panned: the wheel or a pinch zooms where it points, a
 * drag pans, a double click zooms in there, the buttons and +, −, 0 and the arrows do the same.
 * The image comes through the API (a 302 to a link signed for minutes; every load is audited).
 */
/** A cell to show (SS4): its four corners as fractions of the photo; `seq` changes on every ask. */
export interface PhotoFocus {
  quad: number[]
  seq: number
}

export function PhotoViewer({
  url,
  alt,
  className,
  toolbar,
  focus,
}: {
  url: string
  alt: string
  className?: string
  toolbar?: ReactNode
  focus?: PhotoFocus | null
}) {
  const { t } = useTranslation()
  const box = useRef<HTMLDivElement>(null)
  const image = useRef<HTMLImageElement>(null)
  const [view, setView] = useState<View>(FIT)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [attempt, setAttempt] = useState(0)
  // Where the image lies in the box at scale 1 (for the outline of a focused cell).
  const [layout, setLayout] = useState<Layout | null>(null)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ view: View; distance: number; mid: { x: number; y: number } } | null>(null)

  /** A point relative to the box's centre. */
  const local = (clientX: number, clientY: number) => {
    const rect = box.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 }
  }

  /** Keep some of the photo in view. */
  const bounded = (next: View): View => {
    const img = image.current
    const scale = Math.min(MAX, Math.max(MIN, next.scale))
    if (scale === 1) return FIT
    const w = ((img?.offsetWidth ?? 0) * scale) / 2
    const h = ((img?.offsetHeight ?? 0) * scale) / 2
    return { scale, x: Math.max(-w, Math.min(w, next.x)), y: Math.max(-h, Math.min(h, next.y)) }
  }

  /** Zoom to `scale`, keeping the point `at` (relative to the centre) where it is. */
  const zoomAt = (from: View, scale: number, at: { x: number; y: number }): View => {
    const s = Math.min(MAX, Math.max(MIN, scale))
    return bounded({ scale: s, x: at.x - ((at.x - from.x) * s) / from.scale, y: at.y - ((at.y - from.y) * s) / from.scale })
  }

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (state !== 'ready') return
    const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0015))
    setView((v) => zoomAt(v, v.scale * factor, local(event.clientX, event.clientY)))
  }

  // A cell read from the photo: zoom so it and its neighbours fill the view, centred.
  useEffect(() => {
    const img = image.current
    const el = box.current
    if (!focus || !img || !el || state !== 'ready' || focus.quad.length !== 8) return
    const xs = [focus.quad[0], focus.quad[2], focus.quad[4], focus.quad[6]] as number[]
    const ys = [focus.quad[1], focus.quad[3], focus.quad[5], focus.quad[7]] as number[]
    const w = img.offsetWidth
    const h = img.offsetHeight
    const cx = ((Math.min(...xs) + Math.max(...xs)) / 2 - 0.5) * w
    const cy = ((Math.min(...ys) + Math.max(...ys)) / 2 - 0.5) * h
    const cw = (Math.max(...xs) - Math.min(...xs)) * w
    const ch = (Math.max(...ys) - Math.min(...ys)) * h
    const scale = Math.min(MAX, Math.max(MIN, Math.min(el.clientWidth / (cw * 4), el.clientHeight / (ch * 4))))
    setLayout({ left: img.offsetLeft, top: img.offsetTop, width: w, height: h })
    setView(bounded({ scale, x: -cx * scale, y: -cy * scale }))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on a new ask (seq) or once loaded
  }, [focus?.seq, state])

  // React's wheel listener is passive: stop the page from scrolling while zooming the photo.
  useEffect(() => {
    const el = box.current
    if (!el) return
    const stop = (event: globalThis.WheelEvent) => event.preventDefault()
    el.addEventListener('wheel', stop, { passive: false })
    return () => el.removeEventListener('wheel', stop)
  }, [])

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (state !== 'ready' || (event.pointerType === 'mouse' && event.button !== 0)) return
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    event.currentTarget.setPointerCapture(event.pointerId)
    gesture.current = null
  }

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(event.pointerId)
    if (!last) return
    const now = { x: event.clientX, y: event.clientY }
    if (pointers.current.size >= 2) {
      pointers.current.set(event.pointerId, now)
      const [a, b] = [...pointers.current.values()]
      if (!a || !b) return
      const distance = Math.hypot(a.x - b.x, a.y - b.y)
      const mid = local((a.x + b.x) / 2, (a.y + b.y) / 2)
      if (!gesture.current) {
        gesture.current = { view, distance, mid }
        return
      }
      const start = gesture.current
      const zoomed = zoomAt(start.view, (start.view.scale * distance) / Math.max(1, start.distance), start.mid)
      setView(bounded({ ...zoomed, x: zoomed.x + mid.x - start.mid.x, y: zoomed.y + mid.y - start.mid.y }))
      return
    }
    pointers.current.set(event.pointerId, now)
    if (view.scale === 1) return
    setView((v) => bounded({ ...v, x: v.x + now.x - last.x, y: v.y + now.y - last.y }))
  }

  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size < 2) gesture.current = null
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = 40
    const keys: Record<string, (v: View) => View> = {
      '+': (v) => zoomAt(v, v.scale * 1.5, { x: 0, y: 0 }),
      '=': (v) => zoomAt(v, v.scale * 1.5, { x: 0, y: 0 }),
      '-': (v) => zoomAt(v, v.scale / 1.5, { x: 0, y: 0 }),
      '0': () => FIT,
      ArrowLeft: (v) => bounded({ ...v, x: v.x + step }),
      ArrowRight: (v) => bounded({ ...v, x: v.x - step }),
      ArrowUp: (v) => bounded({ ...v, y: v.y + step }),
      ArrowDown: (v) => bounded({ ...v, y: v.y - step }),
    }
    const change = keys[event.key]
    if (!change || state !== 'ready') return
    event.preventDefault()
    setView(change)
  }

  return (
    <div className={cx('relative flex min-h-0 flex-col overflow-hidden rounded-xl border border-hairline bg-sunken', className)}>
      <div
        ref={box}
        role="img"
        aria-label={alt}
        tabIndex={0}
        className="relative flex min-h-0 flex-1 cursor-grab items-center justify-center overflow-hidden select-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring active:cursor-grabbing"
        style={{ touchAction: view.scale === 1 ? 'pan-y' : 'none' }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={(event) => {
          if (state !== 'ready') return
          const at = local(event.clientX, event.clientY)
          setView((v) => (v.scale > 1.01 ? FIT : zoomAt(v, 2.5, at)))
        }}
        onKeyDown={onKeyDown}
      >
        <img
          key={attempt}
          ref={image}
          src={url}
          alt=""
          draggable={false}
          referrerPolicy="no-referrer"
          onLoad={() => setState('ready')}
          onError={() => setState('failed')}
          className={cx('max-h-full max-w-full object-contain will-change-transform', state !== 'ready' && 'invisible')}
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
        />
        {focus && state === 'ready' && layout && <FocusMark quad={focus.quad} layout={layout} view={view} />}
        {state === 'loading' && <span className="absolute text-sm text-muted">{t('sheet.photo.loading')}</span>}
        {state === 'failed' && (
          <span className="absolute flex flex-col items-center gap-2 p-4 text-center text-sm">
            {t('sheet.photo.loadFailed')}
            <Button
              variant="secondary"
              onClick={() => {
                setState('loading')
                setAttempt((n) => n + 1)
              }}
            >
              {t('sheet.photo.retry')}
            </Button>
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 border-t border-hairline bg-surface/90 px-1.5 py-1">
        {toolbar}
        <span className="ml-auto flex items-center gap-1">
          <span className="w-12 text-right font-mono text-xs text-muted tabular-nums" aria-live="polite">
            {Math.round(view.scale * 100)}%
          </span>
          <Button variant="ghost" size="icon" aria-label={t('sheet.photo.zoomOut')} disabled={view.scale <= MIN} onClick={() => setView((v) => zoomAt(v, v.scale / 1.5, { x: 0, y: 0 }))}>
            <ZoomOutIcon />
          </Button>
          <Button variant="ghost" size="icon" aria-label={t('sheet.photo.zoomIn')} disabled={view.scale >= MAX || state !== 'ready'} onClick={() => setView((v) => zoomAt(v, v.scale * 1.5, { x: 0, y: 0 }))}>
            <ZoomInIcon />
          </Button>
          <Button variant="ghost" size="icon" aria-label={t('sheet.photo.fit')} disabled={view.scale === 1} onClick={() => setView(FIT)}>
            <FitIcon />
          </Button>
        </span>
      </div>
    </div>
  )
}

interface Layout {
  left: number
  top: number
  width: number
  height: number
}

/** The outline of the cell being checked, over the zoomed photo. */
function FocusMark({ quad, layout, view }: { quad: number[]; layout: Layout; view: View }) {
  const w = layout.width
  const h = layout.height
  const cx = layout.left + w / 2
  const cy = layout.top + h / 2
  const points: string[] = []
  for (let k = 0; k < 4; k++) {
    const fx = quad[2 * k] ?? 0
    const fy = quad[2 * k + 1] ?? 0
    points.push(`${cx + ((fx - 0.5) * w) * view.scale + view.x},${cy + ((fy - 0.5) * h) * view.scale + view.y}`)
  }
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute inset-0 size-full overflow-visible">
      <polygon points={points.join(' ')} className="fill-none stroke-vb-yellow" strokeWidth={3} />
      <polygon points={points.join(' ')} className="fill-none stroke-navy" strokeWidth={1} strokeDasharray="4 3" />
    </svg>
  )
}


import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { BoxIcon, CloseIcon, CropIcon, RotateLeftIcon, RotateRightIcon, TrashIcon, UndoIcon, ZoomInIcon, ZoomOutIcon } from '../../components/icons'
import { cx } from '../../lib/cx'
import {
  clampRect,
  fromCorners,
  initialEdits,
  isCropped,
  redactToJpeg,
  turn,
  turnedSize,
  type Edits,
  type Rect,
} from '../../lib/redact'
import type { PhotoUpload } from '../../lib/sheetPhotos'

type Corner = 'nw' | 'ne' | 'sw' | 'se'
const CORNERS: Corner[] = ['nw', 'ne', 'sw', 'se']
const ZOOMS = [1, 1.5, 2, 3, 4]
const MIN_CROP = 0.1

type Drag =
  | { kind: 'move'; target: number | 'crop'; start: Rect; x: number; y: number }
  | { kind: 'resize'; target: number | 'crop'; corner: Corner; start: Rect }
  | { kind: 'draw'; x: number; y: number }

/** The corner opposite to `corner` of a rectangle, which stays put while resizing. */
function anchorOf(r: Rect, corner: Corner): [number, number] {
  return [corner.includes('w') ? r.x + r.w : r.x, corner.includes('n') ? r.y + r.h : r.y]
}

const pct = (v: number) => `${(v * 100).toFixed(3)}%`

/**
 * The redaction step before a scoresheet photo is uploaded (docs/PLAN.md §10.3a, SS3): the photo
 * with one black box already on it, which the coach moves over the dates of birth; more boxes,
 * undo, a quarter turn either way, a crop. Only the result leaves the device, as a new JPEG
 * whose pixels under the boxes are black (src/lib/redact.ts). Confirming that the dates of birth
 * are covered is required.
 */
export function PhotoRedactor({
  file,
  sets,
  initialSet,
  busy,
  error,
  onCancel,
  onUpload,
}: {
  file: Blob
  /** How many sets the sheet has, for "on the photo". */
  sets: number
  initialSet: number | null
  busy: boolean
  error: string | null
  onCancel: () => void
  onUpload: (photo: PhotoUpload) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const [url] = useState(() => URL.createObjectURL(file))
  useEffect(() => () => URL.revokeObjectURL(url), [url])
  const image = useRef<HTMLImageElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<[number, number] | null>(null)
  const [unreadable, setUnreadable] = useState(false)
  const [edits, setEdits] = useState<Edits | null>(null)
  const [past, setPast] = useState<Edits[]>([])
  const [selected, setSelected] = useState<number | null>(0)
  const [cropping, setCropping] = useState(false)
  const [zoom, setZoom] = useState(0)
  const [confirmed, setConfirmed] = useState(false)
  const [setNo, setSetNo] = useState<number | null>(initialSet)
  const [preparing, setPreparing] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const drag = useRef<{ drag: Drag; before: Edits } | null>(null)

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panel.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    return () => previous?.focus()
  }, [])

  const onLoad = () => {
    const img = image.current
    if (!img || !img.naturalWidth || !img.naturalHeight) {
      setUnreadable(true)
      return
    }
    setSize([img.naturalWidth, img.naturalHeight])
    setEdits((e) => e ?? initialEdits(img.naturalWidth, img.naturalHeight))
  }

  const commit = (next: Edits, before: Edits | null = edits) => {
    if (before) setPast((p) => [...p.slice(-49), before])
    setEdits(next)
  }

  const undo = () => {
    const previous = past[past.length - 1]
    if (!previous) return
    setPast(past.slice(0, -1))
    setEdits(previous)
    setSelected((s) => (s !== null && s < previous.boxes.length ? s : previous.boxes.length - 1 >= 0 ? previous.boxes.length - 1 : null))
  }

  const addBox = () => {
    if (!edits) return
    const c = edits.crop
    const box = clampRect({ x: c.x + c.w * 0.35, y: c.y + c.h * 0.4, w: c.w * 0.3, h: c.h * 0.2 })
    commit({ ...edits, boxes: [...edits.boxes, box] })
    setSelected(edits.boxes.length)
    setCropping(false)
  }

  const removeBox = (index: number | null = selected) => {
    if (!edits || index === null) return
    commit({ ...edits, boxes: edits.boxes.filter((_, k) => k !== index) })
    setSelected(null)
  }

  const rotate = (dir: 1 | -1) => {
    if (!edits) return
    commit(turn(edits, dir))
  }

  /** A pointer's place as fractions of the turned photo. */
  const at = (event: PointerEvent): [number, number] => {
    const rect = stage.current?.getBoundingClientRect()
    if (!rect || !rect.width || !rect.height) return [0, 0]
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height]
  }

  const start = (event: PointerEvent<HTMLElement>, next: Drag) => {
    if (!edits || busy || preparing) return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    event.stopPropagation()
    event.preventDefault()
    stage.current?.setPointerCapture(event.pointerId)
    drag.current = { drag: next, before: edits }
  }

  const withRect = (e: Edits, target: number | 'crop', r: Rect): Edits =>
    target === 'crop' ? { ...e, crop: r } : { ...e, boxes: e.boxes.map((b, k) => (k === target ? r : b)) }

  const onMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current
    if (!current || !edits) return
    const [x, y] = at(event)
    const d = current.drag
    if (d.kind === 'draw') {
      const r = fromCorners(d.x, d.y, x, y)
      const base = current.before
      setEdits({ ...base, boxes: [...base.boxes, r] })
      setSelected(base.boxes.length)
      return
    }
    if (d.kind === 'move') {
      const moved = { ...d.start, x: d.start.x + x - d.x, y: d.start.y + y - d.y }
      setEdits(withRect(edits, d.target, clampRect(moved)))
      return
    }
    const [ax, ay] = anchorOf(d.start, d.corner)
    const min = d.target === 'crop' ? MIN_CROP : 0.01
    const nx = d.corner.includes('w') ? Math.min(x, ax - min) : Math.max(x, ax + min)
    const ny = d.corner.includes('n') ? Math.min(y, ay - min) : Math.max(y, ay + min)
    setEdits(withRect(edits, d.target, clampRect(fromCorners(ax, ay, nx, ny))))
  }

  const onUp = () => {
    const current = drag.current
    drag.current = null
    if (!current || !edits) return
    if (current.drag.kind === 'draw') {
      const drawn = edits.boxes[edits.boxes.length - 1]
      // A click without a drag draws nothing.
      if (edits.boxes.length === current.before.boxes.length || !drawn || drawn.w < 0.01 || drawn.h < 0.01) {
        setEdits(current.before)
        setSelected(null)
        return
      }
    }
    if (edits !== current.before) setPast((p) => [...p.slice(-49), current.before])
  }

  const onStageDown = (event: PointerEvent<HTMLDivElement>) => {
    // A mouse draws a new box on the empty photo; a finger scrolls (when zoomed in).
    if (cropping || event.pointerType !== 'mouse') {
      setSelected(null)
      return
    }
    const [x, y] = at(event)
    start(event, { kind: 'draw', x, y })
  }

  const onBoxKey = (event: KeyboardEvent<HTMLElement>, index: number) => {
    if (!edits) return
    const box = edits.boxes[index]
    if (!box) return
    const step = event.shiftKey ? 0.05 : 0.01
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const move = moves[event.key]
    if (move) {
      event.preventDefault()
      commit(withRect(edits, index, clampRect({ ...box, x: box.x + move[0], y: box.y + move[1] })))
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      removeBox(index)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      if (cropping) setCropping(false)
      else if (!busy) onCancel()
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault()
      undo()
    }
  }

  const upload = async () => {
    if (!edits || !image.current || !confirmed) return
    setFailure(null)
    setPreparing(true)
    try {
      const blob = await redactToJpeg(image.current, edits)
      onUpload({ blob, setNo, boxes: edits.boxes.length, rotation: edits.rotation, cropped: isCropped(edits.crop) })
    } catch {
      setFailure(t('sheet.photo.editor.unreadable'))
    } finally {
      setPreparing(false)
    }
  }

  const [rw, rh] = size && edits ? turnedSize(size[0], size[1], edits.rotation) : [4, 3]
  const quarter = edits ? edits.rotation % 180 !== 0 : false
  const crop = edits?.crop
  const working = busy || preparing
  const message = failure ?? error

  const handle = (target: number | 'crop', corner: Corner, r: Rect, label: string) => (
    <span
      key={corner}
      role="presentation"
      aria-label={label}
      title={label}
      onPointerDown={(event) => start(event, { kind: 'resize', target, corner, start: r })}
      className="absolute z-10 grid size-8 -translate-x-1/2 -translate-y-1/2 place-items-center"
      style={{ left: corner.includes('w') ? '0%' : '100%', top: corner.includes('n') ? '0%' : '100%', touchAction: 'none', cursor: corner === 'nw' || corner === 'se' ? 'nwse-resize' : 'nesw-resize' }}
    >
      <span className="size-3.5 rounded-full border-2 border-white bg-accent shadow-card" />
    </span>
  )

  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${id}-title`}
      onKeyDown={onKeyDown}
      className="fixed inset-0 z-50 flex flex-col bg-page text-fg"
    >
      <header className="flex items-center gap-2 border-b border-hairline py-1.5 pr-2 pl-4">
        <h2 id={`${id}-title`} className="min-w-0 flex-1 truncate text-base font-semibold sm:text-lg">
          {t('sheet.photo.editor.title')}
        </h2>
        <Button variant="ghost" size="icon" aria-label={t('common.close')} disabled={busy} onClick={onCancel}>
          <CloseIcon />
        </Button>
      </header>

      <div role="toolbar" aria-label={t('sheet.photo.editor.tools')} className="flex items-center gap-1 overflow-x-auto border-b border-hairline bg-surface px-2 py-1">
        <Button variant={cropping ? 'ghost' : 'secondary'} className="px-3 sm:px-4" data-autofocus onClick={addBox} disabled={!edits || working} aria-label={t('sheet.photo.editor.addBox')}>
          <BoxIcon />
          <span className="hidden sm:inline">{t('sheet.photo.editor.addBox')}</span>
        </Button>
        <Button variant="ghost" size="icon" onClick={() => removeBox()} disabled={selected === null || cropping || working} aria-label={t('sheet.photo.editor.removeBox')} title={t('sheet.photo.editor.removeBox')}>
          <TrashIcon />
        </Button>
        <Button variant="ghost" size="icon" onClick={undo} disabled={past.length === 0 || working} aria-label={t('sheet.photo.editor.undo')} title={t('sheet.photo.editor.undo')}>
          <UndoIcon />
        </Button>
        <span className="mx-1 h-6 w-px bg-hairline" aria-hidden="true" />
        <Button variant="ghost" size="icon" onClick={() => rotate(-1)} disabled={!edits || working} aria-label={t('sheet.photo.editor.rotateLeft')} title={t('sheet.photo.editor.rotateLeft')}>
          <RotateLeftIcon />
        </Button>
        <Button variant="ghost" size="icon" onClick={() => rotate(1)} disabled={!edits || working} aria-label={t('sheet.photo.editor.rotateRight')} title={t('sheet.photo.editor.rotateRight')}>
          <RotateRightIcon />
        </Button>
        <Button
          variant={cropping ? 'primary' : 'ghost'}
          onClick={() => {
            setCropping((c) => !c)
            setSelected(null)
          }}
          disabled={!edits || working}
          aria-pressed={cropping}
          aria-label={cropping ? t('sheet.photo.editor.cropDone') : t('sheet.photo.editor.crop')}
          className="px-3 sm:px-4"
        >
          <CropIcon />
          <span className={cx(!cropping && 'hidden sm:inline')}>{cropping ? t('sheet.photo.editor.cropDone') : t('sheet.photo.editor.crop')}</span>
        </Button>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col">
        {/* Zoom stays in the corner while the photo scrolls under it. */}
        <span className="absolute right-3 bottom-3 z-20 flex items-center gap-1 rounded-full border border-hairline bg-surface/95 p-1 shadow-raised">
          <Button variant="ghost" size="icon" className="rounded-full" onClick={() => setZoom((z) => Math.max(0, z - 1))} disabled={zoom === 0} aria-label={t('sheet.photo.zoomOut')} title={t('sheet.photo.zoomOut')}>
            <ZoomOutIcon />
          </Button>
          <span className="w-10 text-center font-mono text-xs text-muted tabular-nums">{Math.round((ZOOMS[zoom] ?? 1) * 100)}%</span>
          <Button variant="ghost" size="icon" className="rounded-full" onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))} disabled={zoom === ZOOMS.length - 1 || !edits} aria-label={t('sheet.photo.zoomIn')} title={t('sheet.photo.zoomIn')}>
            <ZoomInIcon />
          </Button>
        </span>
        <div className="min-h-0 flex-1 overflow-auto bg-sunken p-3 pb-20 sm:p-4 sm:pb-20">
          {unreadable ? (
            <p role="alert" className="mx-auto max-w-md pt-10 text-center">
              {t('sheet.photo.editor.unreadable')}
            </p>
          ) : (
            <div
              ref={stage}
              data-testid="redaction-stage"
              className={cx('relative mx-auto bg-surface select-none', !edits && 'animate-pulse')}
              style={{
                aspectRatio: `${rw} / ${rh}`,
                width: `calc(min(100%, max(14rem, 100dvh - 17rem) * ${(rw / rh).toFixed(5)}) * ${ZOOMS[zoom] ?? 1})`,
                touchAction: 'pan-x pan-y',
                cursor: cropping ? 'default' : 'crosshair',
              }}
              onPointerDown={onStageDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
            >
              <img
                ref={image}
                src={url}
                alt={t('sheet.photo.editor.photo')}
                draggable={false}
                onLoad={onLoad}
                onError={() => setUnreadable(true)}
                className="pointer-events-none absolute top-1/2 left-1/2 max-w-none"
                style={{
                  width: quarter ? `${((rh / rw) * 100).toFixed(4)}%` : '100%',
                  height: quarter ? `${((rw / rh) * 100).toFixed(4)}%` : '100%',
                  transform: `translate(-50%, -50%) rotate(${edits?.rotation ?? 0}deg)`,
                }}
              />
              {!edits && !unreadable && <span className="absolute inset-0 grid place-items-center text-sm text-muted">{t('sheet.photo.editor.loading')}</span>}
              {edits && crop && (
                <>
                  {/* Outside the crop: dimmed (it is not uploaded). */}
                  {(
                    [
                      { left: 0, top: 0, width: '100%', height: pct(crop.y) },
                      { left: 0, top: pct(crop.y + crop.h), width: '100%', height: pct(1 - crop.y - crop.h) },
                      { left: 0, top: pct(crop.y), width: pct(crop.x), height: pct(crop.h) },
                      { left: pct(crop.x + crop.w), top: pct(crop.y), width: pct(1 - crop.x - crop.w), height: pct(crop.h) },
                    ] as const
                  ).map((style, k) => (
                    <span key={k} aria-hidden="true" className="pointer-events-none absolute bg-navy/60" style={style} />
                  ))}
                  {edits.boxes.map((box, k) => (
                    <div
                      key={k}
                      role="button"
                      tabIndex={cropping ? -1 : 0}
                      aria-label={t('sheet.photo.editor.box', { no: k + 1 })}
                      aria-pressed={selected === k}
                      onPointerDown={(event) => {
                        if (cropping) return
                        setSelected(k)
                        start(event, { kind: 'move', target: k, start: box, x: at(event)[0], y: at(event)[1] })
                      }}
                      onFocus={() => !cropping && setSelected(k)}
                      onKeyDown={(event) => onBoxKey(event, k)}
                      className={cx(
                        'absolute',
                        cropping ? 'pointer-events-none' : 'cursor-move',
                        selected === k && !cropping && 'outline-2 outline-offset-1 outline-accent',
                      )}
                      // Pure black, as it is burned into the uploaded picture (the theme has no black).
                      style={{ left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h), touchAction: 'none', backgroundColor: '#000' }}
                    >
                      {selected === k && !cropping && CORNERS.map((c) => handle(k, c, box, t('sheet.photo.editor.resize')))}
                    </div>
                  ))}
                  {cropping && (
                    <div
                      role="group"
                      aria-label={t('sheet.photo.editor.cropFrame')}
                      onPointerDown={(event) => start(event, { kind: 'move', target: 'crop', start: crop, x: at(event)[0], y: at(event)[1] })}
                      className="absolute cursor-move border-2 border-dashed border-white outline outline-1 outline-navy/70"
                      style={{ left: pct(crop.x), top: pct(crop.y), width: pct(crop.w), height: pct(crop.h), touchAction: 'none' }}
                    >
                      {CORNERS.map((c) => handle('crop', c, crop, t('sheet.photo.editor.resize')))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <footer className="flex flex-col gap-2.5 border-t border-hairline bg-surface px-4 py-3">
        <p className="text-sm text-muted">{cropping ? t('sheet.photo.editor.cropHint') : t('sheet.photo.editor.intro')}</p>
        {edits && edits.boxes.length === 0 && <p className="text-sm font-semibold text-danger">{t('sheet.photo.editor.noBoxes')}</p>}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-9">
            <span>{t('sheet.photo.editor.shows')}</span>
            <select
              value={setNo === null ? '' : String(setNo)}
              onChange={(event) => setSetNo(event.target.value ? Number(event.target.value) : null)}
              disabled={working}
              className="h-11 rounded-control border border-hairline-strong bg-surface px-2 sm:h-9"
            >
              <option value="">{t('sheet.photo.whole')}</option>
              {Array.from({ length: Math.max(1, Math.min(5, sets)) }, (_, k) => (
                <option key={k + 1} value={k + 1}>
                  {t('sheet.set', { no: k + 1 })}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-h-11 items-center gap-2.5 text-sm font-semibold sm:min-h-9">
            <input type="checkbox" className="size-5 accent-accent" checked={confirmed} disabled={working} onChange={(event) => setConfirmed(event.target.checked)} />
            {t('sheet.photo.editor.confirm')}
          </label>
        </div>
        {message && (
          <p role="alert" className="text-sm font-semibold text-danger">
            {message}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" onClick={() => void upload()} disabled={!edits || !confirmed || working}>
            {preparing ? t('sheet.photo.editor.preparing') : busy ? t('sheet.photo.editor.uploading') : t('sheet.photo.editor.upload')}
          </Button>
        </div>
      </footer>
    </div>,
    document.body,
  )
}

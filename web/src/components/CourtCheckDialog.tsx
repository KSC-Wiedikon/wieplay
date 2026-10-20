import '../i18n/pages'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { solveCourt, useConfirmCourt, type CourtState, type FoundCourt } from '../lib/court'
import { useToast } from '../lib/toast'
import { Badge } from './Badge'
import { Button } from './Button'
import { Modal } from './Modal'

interface CourtCheckDialogProps {
  open: boolean
  onClose: () => void
  /** Fix: the set-up dialog, pre-filled with the court WiePlay found. */
  onFix: () => void
  videoId: string
  state: CourtState
  found: FoundCourt
  /** The player: its paused frame is the picture the court is drawn on. */
  element: HTMLVideoElement | null
}

/**
 * "Check the court" (docs/PLAN.md §10.6): the court WiePlay found, drawn over the paused frame,
 * with Confirm (it is right: WiePlay uses it from now on) or Fix (the set-up dialog, pre-filled).
 * Shown when WiePlay is not sure of its court, and for a sure one nobody has looked at yet.
 */
export function CourtCheckDialog({ open, onClose, onFix, videoId, state, found, element }: CourtCheckDialogProps) {
  const { t } = useTranslation()
  return (
    <Modal open={open} title={t('court.check.title')} onClose={onClose} size="xl">
      <Check onClose={onClose} onFix={onFix} videoId={videoId} state={state} found={found} element={element} />
    </Modal>
  )
}

function Check({ onClose, onFix, videoId, state, found, element }: Omit<CourtCheckDialogProps, 'open'>) {
  const { t } = useTranslation()
  const toast = useToast()
  const confirm = useConfirmCourt(videoId)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [hasFrame, setHasFrame] = useState(true)
  const [lines, setLines] = useState<[number, number][][] | null>(found.lines)
  const aspect = element && element.videoWidth > 0 ? element.videoWidth / element.videoHeight : state.aspect

  // The paused frame (a cross-origin frame can be drawn, only not read back).
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

  // A proposal's lines: the server fits them to its points.
  useEffect(() => {
    if (found.lines) return
    const abort = new AbortController()
    solveCourt(videoId, found.points, abort.signal)
      .then((solution) => setLines(solution.lines))
      .catch(() => {
        if (!abort.signal.aborted) setLines([])
      })
    return () => abort.abort()
  }, [found, videoId])

  const onConfirm = () =>
    confirm.mutate(undefined, {
      onSuccess: () => {
        toast.success(t('court.check.confirmed'))
        onClose()
      },
      onError: (error) => toast.error(t('court.setup.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
    })

  const pct = found.confidence === null ? null : Math.round(found.confidence * 100)
  const origin = found.method === 'auto-hall' ? t('court.check.fromHall') : t('court.check.fromPicture')
  return (
    <div className="flex flex-col gap-4">
      <p className="flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-sunken p-3 text-sm" role="status">
        <Badge>{found.sure ? t('court.check.sure') : t('court.check.unsure')}</Badge>
        {found.sure ? t('court.check.sureText', { origin }) : t('court.check.unsureText', { origin })}
        {pct !== null && <span className="text-muted">{`(${t('court.setup.confidence', { pct })})`}</span>}
      </p>
      <p className="text-sm">{t('court.check.question')}</p>
      <div className="relative w-full overflow-hidden rounded-xl bg-navy" style={{ aspectRatio: String(aspect) }}>
        <canvas ref={canvas} className="absolute inset-0 size-full" aria-hidden="true" />
        {!hasFrame && (
          <p className="absolute inset-x-0 top-0 m-3 rounded-lg bg-surface/90 p-2 text-sm text-fg">
            {element && element.readyState >= 2 ? t('court.setup.noImage') : t('court.setup.noFrame')}
          </p>
        )}
        <svg className="absolute inset-0 size-full" role="img" aria-label={t('court.check.image')}>
          {(lines ?? []).map((line, i) =>
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
        </svg>
      </div>
      <p className="text-xs text-muted">{t('court.check.hint')}</p>
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={onClose} className="mr-auto">
          {t('common.cancel')}
        </Button>
        <Button onClick={onFix}>{t('court.check.fix')}</Button>
        <Button variant="primary" onClick={onConfirm} disabled={confirm.isPending}>
          {t('court.check.confirm')}
        </Button>
      </div>
    </div>
  )
}

import '../i18n/pages'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { formatDate } from '../lib/format'
import { keys } from '../lib/queryClient'
import {
  rectFrom,
  usable,
  useReadScoreboard,
  useRemoveScoreboard,
  useSaveScoreboard,
  useScoreboard,
  type BoardKind,
  type Rect,
  type ScoreboardState,
} from '../lib/scoreboard'
import { useToast } from '../lib/toast'
import type { Video } from '../lib/types'
import { Button } from './Button'
import { CheckIcon } from './icons'
import { Modal } from './Modal'
import { Segmented } from './Segmented'

type Which = 'score' | 'sets'
const COLOURS: Record<Which, string> = { score: '#ffc72c', sets: '#7fb2ff' }
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/**
 * "Mark the scoreboard" (PLAN §10.3, step 5): drag a box around the score on the paused frame,
 * optionally a second one around the sets won; say what kind of board it is. Pre-filled from the
 * hall's last box (or the team's). Saving reads the board on the worker; "No scoreboard in this
 * video" forgets it.
 */
export function ScoreboardDialog({ video, state, element, onClose }: { video: Video; state: ScoreboardState; element: HTMLVideoElement | null; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Modal open title={t('scoreboard.title')} onClose={onClose} size="xl">
      <Marker video={video} state={state} element={element} onClose={onClose} />
    </Modal>
  )
}

function Marker({ video, state, element, onClose }: { video: Video; state: ScoreboardState; element: HTMLVideoElement | null; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const save = useSaveScoreboard(video.id)
  const remove = useRemoveScoreboard(video.id)
  const suggestion = state.region ? null : state.suggestion
  const start = state.region ?? suggestion?.region ?? null
  const [boxes, setBoxes] = useState<Record<Which, Rect | null>>({ score: start?.rect ?? null, sets: start?.sets ?? null })
  const [kind, setKind] = useState<BoardKind>(start?.kind ?? 'auto')
  const [which, setWhich] = useState<Which>('score')
  const [copied, setCopied] = useState<string | null>(suggestion ? suggestion.region.id : null)
  const [hasFrame, setHasFrame] = useState(true)
  const canvas = useRef<HTMLCanvasElement>(null)
  const area = useRef<HTMLDivElement>(null)
  const drag = useRef<[number, number] | null>(null)
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

  const at = (event: PointerEvent): [number, number] | null => {
    const box = area.current?.getBoundingClientRect()
    if (!box || box.width === 0 || box.height === 0) return null
    return [clamp01((event.clientX - box.left) / box.width), clamp01((event.clientY - box.top) / box.height)]
  }
  const set = (rect: Rect | null) => {
    setCopied(null)
    setBoxes((old) => ({ ...old, [which]: rect }))
  }
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const p = at(event)
    if (!p) return
    drag.current = p
    event.currentTarget.setPointerCapture?.(event.pointerId)
    set(rectFrom(p, p))
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const from = drag.current
    const p = at(event)
    if (from && p) set(rectFrom(from, p))
  }
  const onPointerUp = () => {
    drag.current = null
  }
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const rect = boxes[which]
    const box = area.current?.getBoundingClientRect()
    if (!rect || !box) return
    const dx = (event.shiftKey ? 10 : 1) / Math.max(1, box.width)
    const dy = (event.shiftKey ? 10 : 1) / Math.max(1, box.height)
    const moves: Record<string, [number, number]> = { ArrowLeft: [-dx, 0], ArrowRight: [dx, 0], ArrowUp: [0, -dy], ArrowDown: [0, dy] }
    const move = moves[event.key]
    if (!move) return
    event.preventDefault()
    set({ ...rect, x: clamp01(Math.min(1 - rect.w, rect.x + move[0])), y: clamp01(Math.min(1 - rect.h, rect.y + move[1])) })
  }

  const score = boxes.score
  const onSave = () => {
    if (!usable(score)) return
    save.mutate(
      {
        rect: score,
        sets: usable(boxes.sets) ? boxes.sets : null,
        kind,
        at_ms: element && Number.isFinite(element.currentTime) ? Math.round(element.currentTime * 1000) : null,
        copied_from: copied,
      },
      {
        onSuccess: () => {
          toast.success(t('scoreboard.saved'))
          onClose()
        },
        onError: (error) => toast.error(t('scoreboard.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
      },
    )
  }
  const onRemove = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        toast.success(t('scoreboard.removed'))
        onClose()
      },
      onError: (error) => toast.error(t('scoreboard.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
    })

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{t('scoreboard.intro')}</p>
      {suggestion && copied && (
        <p className="rounded-xl border border-hairline bg-sunken p-3 text-sm" role="status">
          {t(suggestion.source === 'hall' ? 'scoreboard.fromHall' : 'scoreboard.fromTeam', {
            title: suggestion.video_title,
            date: formatDate(suggestion.recorded_on),
          })}
        </p>
      )}
      {suggestion && !copied && (
        <div>
          <Button
            variant="ghost"
            onClick={() => {
              setBoxes({ score: suggestion.region.rect, sets: suggestion.region.sets })
              setKind(suggestion.region.kind)
              setCopied(suggestion.region.id)
            }}
          >
            {t('scoreboard.useSuggestion')}
          </Button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Segmented
          label={t('scoreboard.box')}
          value={which}
          onChange={setWhich}
          options={[
            { value: 'score', label: t('scoreboard.score') },
            { value: 'sets', label: t('scoreboard.sets') },
          ]}
        />
        <Segmented
          label={t('scoreboard.kind')}
          value={kind}
          onChange={(value) => {
            setKind(value)
            setCopied(null)
          }}
          options={(['flip', 'led', 'auto'] as const).map((value) => ({ value, label: t(`scoreboard.kinds.${value}`) }))}
        />
      </div>
      {which === 'sets' && <p className="text-xs text-muted">{t('scoreboard.setsHint')}</p>}
      <div className="relative w-full overflow-hidden rounded-xl bg-navy" style={{ aspectRatio: String(aspect) }}>
        <canvas ref={canvas} className="absolute inset-0 size-full" aria-hidden="true" />
        {!hasFrame && (
          <p className="absolute inset-x-0 top-0 m-3 rounded-lg bg-surface/90 p-2 text-sm text-fg">
            {element && element.readyState >= 2 ? t('scoreboard.noImage') : t('scoreboard.noFrame')}
          </p>
        )}
        <div
          ref={area}
          role="application"
          aria-label={`${t('scoreboard.image')} (${which === 'score' ? t('scoreboard.score') : t('scoreboard.sets')}). ${t('scoreboard.nudge')}`}
          tabIndex={0}
          className="absolute inset-0 cursor-crosshair touch-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
        >
          {(['score', 'sets'] as const).map((name) => {
            const rect = boxes[name]
            if (!rect) return null
            return (
              <div
                key={name}
                data-testid={`box-${name}`}
                className="pointer-events-none absolute border-2"
                style={{
                  left: `${rect.x * 100}%`,
                  top: `${rect.y * 100}%`,
                  width: `${rect.w * 100}%`,
                  height: `${rect.h * 100}%`,
                  borderColor: COLOURS[name],
                  boxShadow: name === which ? '0 0 0 9999px rgb(19 32 58 / 0.35)' : undefined,
                }}
              />
            )
          })}
        </div>
      </div>
      <p className="min-h-5 text-sm text-muted" role="status" aria-live="polite">
        {usable(score) ? null : t('scoreboard.need')}
      </p>
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        {state.region && (
          <Button variant="ghostDanger" onClick={onRemove} disabled={remove.isPending} className="mr-auto">
            {t('scoreboard.remove')}
          </Button>
        )}
        {boxes[which] && (
          <Button variant="ghost" onClick={() => set(null)}>
            {t('scoreboard.clear')}
          </Button>
        )}
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button variant="primary" onClick={onSave} disabled={!usable(score) || save.isPending}>
          {t('scoreboard.save')}
        </Button>
      </div>
    </div>
  )
}

/** Below the player (volleyball): "Mark the scoreboard" for those who may tag, and how the reading went. */
export function ScoreboardTools({ video, element }: { video: Video; element: HTMLVideoElement | null }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const board = useScoreboard(video.id)
  const again = useReadScoreboard(video.id)
  const [open, setOpen] = useState(false)
  const status = board.data?.job?.status
  // The score line changes once the board is read: fetch the tagging data again then.
  const last = useRef(status)
  useEffect(() => {
    if (last.current && ['queued', 'running'].includes(last.current) && status === 'done') {
      void client.invalidateQueries({ queryKey: keys.tagging(video.id) })
      void client.invalidateQueries({ queryKey: keys.analysis(video.id) })
    }
    last.current = status
  }, [status, client, video.id])
  const data = board.data
  if (!data || (!data.can_edit && !data.region)) return null
  const segments = data.reading?.segments.length ?? 0
  const note =
    status === 'queued' || status === 'running'
      ? data.job?.progress
        ? t('scoreboard.readingProgress', { pct: Math.floor(data.job.progress * 100) })
        : t('scoreboard.reading')
      : status === 'failed'
        ? t('scoreboard.readFailed')
        : data.region && data.reading
          ? segments > 0
            ? t('scoreboard.read', { count: segments })
            : t('scoreboard.readNothing')
          : null
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {data.can_edit && (
        <Button onClick={() => setOpen(true)} aria-haspopup="dialog">
          {data.region && <CheckIcon className="size-4" />}
          {data.region ? t('scoreboard.edit') : t('scoreboard.open')}
        </Button>
      )}
      {note && (
        <span className="text-sm text-muted" role="status">
          {note}
        </span>
      )}
      {data.can_edit && data.region && status === 'failed' && (
        <Button variant="ghost" onClick={() => again.mutate(undefined)} disabled={again.isPending}>
          {t('scoreboard.readAgain')}
        </Button>
      )}
      {open && <ScoreboardDialog video={video} state={data} element={element} onClose={() => setOpen(false)} />}
    </div>
  )
}

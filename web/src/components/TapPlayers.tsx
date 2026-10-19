import '../i18n/pages'
import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { numbersToOffer, usePlayersFrame, useRestore, useTap, type CardState, type FrameBox } from '../lib/identify'
import { cx } from '../lib/cx'
import { parseNumber } from '../lib/players'
import { useToast } from '../lib/toast'
import type { RosterEntry } from '../lib/types'
import { Button } from './Button'


/**
 * Name a player by tapping them in the paused video (docs/PLAN.md §10.5), like Balltime: the
 * people WiePlay followed at that moment are outlined (from the players index); a tap asks for
 * their number (ours from the roster) or "not a player", and their card gets it.
 */
export function TapLayer({ videoId, video, roster, onDone }: { videoId: string; video: HTMLVideoElement | null; roster: RosterEntry[]; onDone?: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [at, setAt] = useState<number | null>(null)
  const [picked, setPicked] = useState<FrameBox | null>(null)
  const [typed, setTyped] = useState('')
  const frame = usePlayersFrame(videoId, at)
  const tap = useTap(videoId)
  const restore = useRestore(videoId)
  const [lastUndo, setLastUndo] = useState<CardState[] | null>(null)

  // Paused: the people of that moment; playing: nothing to tap.
  useEffect(() => {
    if (!video) return
    const update = () => {
      setPicked(null)
      setAt(video.paused ? Math.round(video.currentTime * 1000) : null)
    }
    update()
    video.addEventListener('pause', update)
    video.addEventListener('play', update)
    video.addEventListener('seeked', update)
    return () => {
      video.removeEventListener('pause', update)
      video.removeEventListener('play', update)
      video.removeEventListener('seeked', update)
    }
  }, [video])

  const name = (number: number | null, notPlayer: boolean) => {
    if (!picked || at === null) return
    tap.mutate(
      { t_ms: at, track: picked.track, number, not_player: notPlayer },
      {
        onSuccess: (data) => {
          setPicked(null)
          setLastUndo(data.undo)
          toast.success(t('identify.tap.saved'))
          onDone?.()
        },
      },
    )
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const n = parseNumber(typed)
    if (n !== undefined && n !== null) name(n, false)
  }

  // The picture inside the element (object-contain): where the frame's 0..1 lands.
  const rect = (() => {
    if (!video || !video.videoWidth || !video.videoHeight) return { left: 0, top: 0, width: 100, height: 100 }
    const ew = video.clientWidth
    const eh = video.clientHeight
    const scale = Math.min(ew / video.videoWidth, eh / video.videoHeight)
    const w = (video.videoWidth * scale) / ew
    const h = (video.videoHeight * scale) / eh
    return { left: ((1 - w) / 2) * 100, top: ((1 - h) / 2) * 100, width: w * 100, height: h * 100 }
  })()
  const boxes = at !== null ? (frame.data?.boxes ?? []) : []
  const offer = picked ? numbersToOffer(picked.team, [], roster) : []

  return (
    <div className="pointer-events-none absolute inset-0" data-testid="tap-layer">
      <div className="absolute" style={{ left: `${rect.left}%`, top: `${rect.top}%`, width: `${rect.width}%`, height: `${rect.height}%` }}>
        {boxes.map((b) => (
          <button
            key={b.track}
            type="button"
            aria-label={b.number !== null ? `#${b.number}` : t('identify.tap.who')}
            onClick={() => setPicked(b)}
            className={cx(
              'pointer-events-auto absolute cursor-pointer rounded-sm border-2 focus-visible:outline-2 focus-visible:outline-ring',
              picked?.track === b.track ? 'border-accent bg-accent/20' : b.sure ? 'border-white/80' : 'border-yellow-300',
            )}
            style={{ left: `${b.box[0] * 100}%`, top: `${b.box[1] * 100}%`, width: `${(b.box[2] - b.box[0]) * 100}%`, height: `${(b.box[3] - b.box[1]) * 100}%` }}
          >
            {b.number !== null && <span className="absolute -top-5 left-0 rounded bg-navy/80 px-1 text-xs font-bold text-white">#{b.number}</span>}
          </button>
        ))}
      </div>
      {at !== null && frame.isSuccess && boxes.length === 0 && (
        <p className="absolute inset-x-0 top-2 text-center text-sm text-white drop-shadow">{t('identify.tap.none')}</p>
      )}
      {lastUndo && !picked && (
        <Button
          variant="secondary"
          className="pointer-events-auto absolute bottom-2 left-2"
          disabled={restore.isPending}
          onClick={() => restore.mutate(lastUndo, { onSuccess: () => setLastUndo(null) })}
        >
          {t('identify.tap.undo')}
        </Button>
      )}
      {picked && (
        <div role="dialog" aria-label={t('identify.tap.who')} className="pointer-events-auto absolute right-2 bottom-2 left-2 flex max-h-[70%] flex-col gap-2 overflow-y-auto rounded-card bg-surface p-3 shadow-card sm:left-auto sm:w-80">
          <p className="font-semibold">{t('identify.tap.who')}</p>
          <div className="grid grid-cols-4 gap-1.5">
            {offer.map((o) => (
              <Button key={o.number} size="touch" variant="secondary" disabled={tap.isPending} onClick={() => name(o.number, false)}>
                #{o.number}
              </Button>
            ))}
          </div>
          <form onSubmit={submit} className="flex items-center gap-2">
            <input
              inputMode="numeric"
              aria-label={t('identify.typeNumber')}
              className="h-11 w-20 rounded-control border border-hairline-strong bg-surface px-2 text-center font-mono sm:h-9"
              value={typed}
              onChange={(e) => setTyped(e.target.value.replace(/\D/g, '').slice(0, 2))}
            />
            <Button type="submit" disabled={tap.isPending || parseNumber(typed) == null}>
              {t('identify.save')}
            </Button>
          </form>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={tap.isPending} onClick={() => name(null, true)}>
              {t('identify.notPlayer')}
            </Button>
            <Button variant="ghost" onClick={() => setPicked(null)}>
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

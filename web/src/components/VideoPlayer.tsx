import type Hls from 'hls.js'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Video } from '../lib/types'
import { Button } from './Button'
import { Select } from './Field'
import { Message } from './Message'

const HLS_TYPE = 'application/vnd.apple.mpegurl'
const SEEK_SECONDS = 5
/** Fatal network errors hls.js may try to recover from before we give up. */
const MAX_RECOVERIES = 3

type Mode = 'loading' | 'hls' | 'native' | 'unsupported' | 'error'

interface Level {
  index: number
  height: number
  fps: number | null
}

/** Keys typed into these keep their own meaning. */
function isInteractive(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest('input, textarea, select, button, a[href], [contenteditable="true"], [role="alertdialog"]') !== null
  )
}

interface VideoPlayerProps {
  video: Video
  /** Receives the <video> element (null when it goes away), for tagging and condensed playback. */
  videoRef?: (element: HTMLVideoElement | null) => void
  /** The player's own keys (space, arrows, comma and period). Off where the page has its own. */
  keyboard?: boolean
  /** Frame rate of the rendition playing now. */
  onFrameRate?: (fps: number) => void
}

/**
 * HLS playback through the API: hls.js where Media Source Extensions exist
 * (also iOS 17.1+), native HLS otherwise (older iPhones). Segment requests
 * are answered with short-lived redirects to object storage.
 */
export function VideoPlayer({ video, videoRef, keyboard = true, onFrameRate }: VideoPlayerProps) {
  const { t } = useTranslation()
  const id = useId()
  const ref = useRef<HTMLVideoElement>(null)
  const setRef = useCallback(
    (element: HTMLVideoElement | null) => {
      ref.current = element
      videoRef?.(element)
    },
    [videoRef],
  )
  const hlsRef = useRef<Hls | null>(null)
  const [mode, setMode] = useState<Mode>('loading')
  const [levels, setLevels] = useState<Level[]>([])
  const [chosen, setChosen] = useState(-1)
  const [current, setCurrent] = useState(-1)
  const [attempt, setAttempt] = useState(0)
  const src = `/api/media/${video.id}/master.m3u8`

  useEffect(() => {
    const element = ref.current
    if (!element) return
    let cancelled = false
    let hls: Hls | null = null
    let recoveries = 0

    import('hls.js/light')
      .then(({ default: HlsClass }) => {
        if (cancelled) return
        if (HlsClass.isSupported()) {
          // No cap to the player size: tagging needs the 1080p detail even in a small player.
          const instance = new HlsClass()
          hls = instance
          hlsRef.current = instance
          instance.on(HlsClass.Events.MANIFEST_PARSED, (_event, data) => {
            setLevels(
              data.levels
                .map((level, index) => ({ index, height: level.height, fps: level.frameRate || null }))
                .sort((a, b) => b.height - a.height),
            )
            setMode('hls')
          })
          instance.on(HlsClass.Events.LEVEL_SWITCHED, (_event, data) => setCurrent(data.level))
          instance.on(HlsClass.Events.ERROR, (_event, data) => {
            if (!data.fatal) return
            const manifest = data.details === HlsClass.ErrorDetails.MANIFEST_LOAD_ERROR
            if (data.type === HlsClass.ErrorTypes.NETWORK_ERROR && !manifest && recoveries < MAX_RECOVERIES) {
              recoveries += 1
              instance.startLoad()
            } else if (data.type === HlsClass.ErrorTypes.MEDIA_ERROR && recoveries < MAX_RECOVERIES) {
              recoveries += 1
              instance.recoverMediaError()
            } else {
              instance.destroy()
              hlsRef.current = null
              setMode('error')
            }
          })
          instance.loadSource(src)
          instance.attachMedia(element)
        } else if (element.canPlayType(HLS_TYPE)) {
          element.src = src
          setMode('native')
        } else {
          setMode('unsupported')
        }
      })
      .catch(() => {
        if (!cancelled) setMode('error')
      })

    return () => {
      cancelled = true
      hls?.destroy()
      hlsRef.current = null
      element.removeAttribute('src')
    }
  }, [src, attempt])

  const playing = levels.find((level) => level.index === current)
  // One frame of the rendition playing now (540p runs at 25 fps, 1080p at 50).
  const fps = playing?.fps || Math.max(0, ...video.renditions.map((r) => r.fps)) || video.fps || 25
  const frame = 1 / fps

  useEffect(() => {
    onFrameRate?.(fps)
  }, [fps, onFrameRate])

  useEffect(() => {
    if (!keyboard) return
    const onKey = (event: KeyboardEvent) => {
      const element = ref.current
      if (!element || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      if (isInteractive(event.target)) return
      const end = Number.isFinite(element.duration) ? element.duration : Infinity
      const seek = (to: number) => {
        element.currentTime = Math.min(end, Math.max(0, to))
      }
      switch (event.key) {
        case ' ':
          if (element.paused) void element.play().catch(() => {})
          else element.pause()
          break
        case 'ArrowLeft':
          seek(element.currentTime - SEEK_SECONDS)
          break
        case 'ArrowRight':
          seek(element.currentTime + SEEK_SECONDS)
          break
        case ',':
          if (!element.paused) return
          seek(element.currentTime - frame)
          break
        case '.':
          if (!element.paused) return
          seek(element.currentTime + frame)
          break
        default:
          return
      }
      // Captured before the focused <video> sees it, so its own controls don't act twice.
      event.preventDefault()
      event.stopPropagation()
    }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true })
  }, [frame, keyboard])

  const chooseLevel = (value: number) => {
    setChosen(value)
    if (hlsRef.current) hlsRef.current.currentLevel = value
  }

  const autoLabel = chosen === -1 && playing ? t('video.player.autoWith', { quality: `${playing.height}p` }) : t('video.player.auto')

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-hidden rounded-2xl bg-navy">
        <video
          ref={setRef}
          controls
          playsInline
          preload="metadata"
          poster={video.has_thumbs ? `/api/media/${video.id}/poster.jpg` : undefined}
          aria-label={video.title}
          className="aspect-video w-full"
        />
      </div>
      {mode === 'unsupported' && <Message>{t('video.player.unsupported')}</Message>}
      {mode === 'error' && (
        <Message
          action={
            <Button
              onClick={() => {
                setMode('loading')
                setAttempt((n) => n + 1)
              }}
            >
              {t('common.retry')}
            </Button>
          }
        >
          <span role="alert">{t('video.player.error')}</span>
        </Message>
      )}
      {mode === 'hls' && levels.length > 1 && (
        <div className="flex items-center gap-2">
          <label htmlFor={`${id}-quality`} className="text-sm font-semibold">
            {t('video.player.quality')}
          </label>
          <Select
            id={`${id}-quality`}
            inline
            value={chosen}
            onChange={(event) => chooseLevel(Number(event.target.value))}
          >
            <option value={-1}>{autoLabel}</option>
            {levels.map((level) => (
              <option key={level.index} value={level.index}>
                {`${level.height}p`}
              </option>
            ))}
          </Select>
        </div>
      )}
      {keyboard && <p className="hidden text-xs text-muted sm:block">{t('video.player.keys')}</p>}
    </div>
  )
}

// A small HLS source for a <video> without controls (the identify queue's zoomed pictures):
// hls.js where Media Source Extensions exist, native HLS otherwise. The full player with its
// controls, levels and keys is components/VideoPlayer.tsx.

import type Hls from 'hls.js'
import { useEffect, useState } from 'react'

export type HlsState = 'loading' | 'ready' | 'unsupported' | 'error'

export function useHlsSource(element: HTMLVideoElement | null, src: string): HlsState {
  const [state, setState] = useState<HlsState>('loading')
  useEffect(() => {
    if (!element) return
    let cancelled = false
    let hls: Hls | null = null
    import('hls.js/light')
      .then(({ default: HlsClass }) => {
        if (cancelled) return
        if (HlsClass.isSupported()) {
          hls = new HlsClass()
          hls.on(HlsClass.Events.MANIFEST_PARSED, () => setState('ready'))
          hls.on(HlsClass.Events.ERROR, (_event, data) => {
            if (data.fatal) setState('error')
          })
          hls.loadSource(src)
          hls.attachMedia(element)
        } else if (element.canPlayType('application/vnd.apple.mpegurl')) {
          element.src = src
          setState('ready')
        } else {
          setState('unsupported')
        }
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })
    return () => {
      cancelled = true
      hls?.destroy()
      element.removeAttribute('src')
    }
  }, [element, src])
  return state
}

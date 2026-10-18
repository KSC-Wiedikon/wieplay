import { useSyncExternalStore } from 'react'

/** A media query's answer, live; `fallback` where the browser has none (tests). */
export function useMediaQuery(query: string, fallback: boolean): boolean {
  return useSyncExternalStore(
    (change) => {
      if (typeof window.matchMedia !== 'function') return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', change)
      return () => list.removeEventListener('change', change)
    },
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : fallback),
    () => fallback,
  )
}

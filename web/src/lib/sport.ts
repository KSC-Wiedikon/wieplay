import { useEffect, useSyncExternalStore } from 'react'
import type { SportName } from './types'

/** Short sport code used for the accent colour, the logo and the favicon. */
export type Sport = 'vb' | 'bb'

export function sportOf(name: SportName): Sport {
  return name === 'basketball' ? 'bb' : 'vb'
}

let current: Sport = 'vb'
const listeners = new Set<() => void>()

function apply(sport: Sport): void {
  document.documentElement.dataset.sport = sport
  const links = document.querySelectorAll<HTMLLinkElement>('link[rel~="icon"], link[rel="apple-touch-icon"]')
  for (const link of links) {
    const href = link.getAttribute('href')
    if (href) link.setAttribute('href', href.replace(/\/icons\/(vb|bb)\//, `/icons/${sport}/`))
  }
}

/** Switches the accent, logo and favicon (data-sport on <html>). */
export function setSport(sport: Sport): void {
  if (sport === current) return
  current = sport
  apply(sport)
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useSport(): Sport {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => 'vb',
  )
}

/** Use the given sport while the calling component is mounted; back to vb afterwards. */
export function useSportAccent(sport: Sport): void {
  useEffect(() => {
    setSport(sport)
    return () => setSport('vb')
  }, [sport])
}

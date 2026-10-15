import { useSyncExternalStore } from 'react'
import { readStorage, writeStorage } from './storage'

export type Theme = 'light' | 'dark'

const KEY = 'nomal-theme'
const listeners = new Set<() => void>()
const media =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

function parse(value: string | null): Theme | null {
  return value === 'light' || value === 'dark' ? value : null
}

/** The manual choice; null means "follow the system". */
let chosen: Theme | null = parse(readStorage(KEY))

export function currentTheme(): Theme {
  return chosen ?? (media?.matches ? 'dark' : 'light')
}

function apply(): void {
  const theme = currentTheme()
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.classList.toggle('light', theme === 'light')
}

function changed(): void {
  apply()
  listeners.forEach((listener) => listener())
}

/** Call once before the first render. */
export function initTheme(): void {
  apply()
  media?.addEventListener('change', () => {
    if (!chosen) changed()
  })
}

export function toggleTheme(): void {
  chosen = currentTheme() === 'dark' ? 'light' : 'dark'
  writeStorage(KEY, chosen)
  changed()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme, () => 'light')
}

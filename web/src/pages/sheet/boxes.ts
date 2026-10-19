// The service boxes' ids and keys, focus and digits (src/pages/sheet/ServiceGrid.tsx).

import type { Side } from '../../lib/types'

/** How a box is marked by the live check: the box a check doubts, the next one to type. */
export type BoxMark = 'error' | 'next' | 'warning'

export function boxKey(side: Side, position: number, box: number): string {
  return `${side}-${position}-${box}`
}

export function boxId(prefix: string, side: Side, position: number, box: number): string {
  return `${prefix}-box-${boxKey(side, position, box)}`
}

/** Focus a box (and select its digits, so typing replaces them). */
export function focusBox(prefix: string, side: Side, position: number, box: number): boolean {
  const el = document.getElementById(boxId(prefix, side, position, box))
  if (!(el instanceof HTMLInputElement)) return false
  el.focus()
  el.select()
  return true
}

/** Up to two digits; empty: no number. */
export function parseDigits(raw: string): number | null {
  const digits = raw.replace(/\D/g, '').slice(0, 2)
  return digits === '' ? null : Number(digits)
}

/** Focus a field of a set's panel by its key (the reading's field keys, `data-field`). */
export function focusField(no: number, key: string): boolean {
  const el = document.querySelector(`[data-set="${no}"] [data-field="${key}"]`)
  const target = el instanceof HTMLInputElement || (el instanceof HTMLDivElement && el.tabIndex >= 0) ? el : el?.querySelector('input')
  if (!(target instanceof HTMLElement)) return false
  target.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  target.focus({ preventScroll: true })
  return true
}

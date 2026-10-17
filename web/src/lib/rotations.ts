// Rotation charts and the setter analysis (docs/PLAN.md §9.7, step 5a/5b): the drawing's
// geometry and small helpers. The numbers come from the server (backend/nomal/core/stats.py).

/** Zones I..VI in Roman numerals, as on a lineup sheet. */
export const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI'] as const

/**
 * Where a zone's player stands on the rotation chart: our half seen from behind our end line,
 * the net at the top, drawn as two rows of three 3 m zones (not to scale): front row IV III II,
 * back row V VI I. Index 0 is zone I.
 */
export const ZONE_SPOTS: readonly (readonly [number, number])[] = [
  [7.5, 4.5], // I: back right, the server
  [7.5, 1.5], // II: front right
  [4.5, 1.5], // III: front middle
  [1.5, 1.5], // IV: front left
  [1.5, 4.5], // V: back left
  [4.5, 4.5], // VI: back middle
]

/** "Jonas Keller" → "J. Keller"; a single name stays as it is. */
export function initialName(name: string): string {
  const parts = name.trim().split(/\s+/)
  if (parts.length < 2) return name
  return `${parts[0]?.charAt(0) ?? ''}. ${parts.slice(1).join(' ')}`
}

/** "+4", "−2", "±0" (a real minus sign). */
export function signed(n: number): string {
  if (n > 0) return `+${n}`
  if (n < 0) return `−${Math.abs(n)}`
  return '±0'
}

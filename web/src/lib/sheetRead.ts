// Reading the scoresheet's photo on club hardware (docs/PLAN.md §10.3a, SS4): the server reads
// the photo (a job on the club's Lenovo server), decodes it with the rules and writes the sets as
// drafts; the page shows which cells to check, zooms the photo to a cell, and a coach accepts the
// sure cells, checks the doubtful ones and confirms each set. A draft is never scored; once
// confirmed it is a typed set like any other. Never over what a person typed: the server keeps
// typed cells, the page only marks what the reading proposed.
//
// The pure parts (which cells are left to check, what an edit accepted, the field keys) are
// tested without a browser (sheetRead.test.ts).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'
import { keys } from './queryClient'
import { scoresheetKey, type Scoresheet, type SheetIn, type SheetSetDoc } from './scoresheet'
import type { Side } from './types'

/** One field of the form as the reading proposed it. */
export interface ReadCell {
  v: unknown
  /** The reader's likeliest readings, with their probabilities. */
  alts: [unknown, number][]
  /** The coach should look at it. */
  check: boolean
  /** rules: the rules took another value than the reader's first; unsure: another reading is
   * almost as likely; off_path: no score of the set fits; not_decoded: the set did not decode;
   * on_court: a substitute already in the lineup. */
  why?: 'rules' | 'unsure' | 'off_path' | 'not_decoded' | 'on_court'
  /** The photo it was read on, and where (x, y of four corners, fractions of the photo). Only
   * for those who may see the photo. */
  img?: string
  quad?: number[]
}

export interface ReadSet {
  state: 'draft' | 'confirmed' | 'skipped' | 'discarded'
  us_half?: 'L' | 'R'
  decoded?: boolean
  note?: string | null
  to_check?: number
  cells?: Record<string, ReadCell>
  changed?: number
}

export interface SheetReading {
  job: { status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'; error: string | null; created_at: string | null } | null
  at: string | null
  worker: string | null
  model: string | null
  us_letter: 'A' | 'B' | null
  error: string | null
  to_check: number
  drafts: number[]
  sets: Record<string, ReadSet>
}

// --- field keys (the same as the server's, wieplay/ml/sheet_read.py) -----------------------------

export const boxField = (side: Side, position: number, box: number) => `box.${side}.${position}.${box}`
export const lineupField = (side: Side, position: number) => `lineup.${side}.${position}`
export const timeoutField = (side: Side, slot: number) => `to.${side}.${slot}`
export const subField = (side: Side, position: number, part: 'number' | 'at' | 'back') => `sub.${side}.${position}.${part}`

/** How a field shows: to check (doubtful, not looked at yet), read (proposed, not accepted), or
 * nothing special (typed, or accepted). */
export type ReadMark = 'check' | 'read' | null

/** A field read from the photo: doubtful ones stand out, sure ones are marked lightly. */
export function readClass(mark: ReadMark): string {
  return mark === 'check' ? 'border-2 border-vb-yellow bg-vb-yellow/20' : mark === 'read' ? 'border-dashed border-accent bg-accent-soft/40' : ''
}

export function markOf(cells: Record<string, ReadCell> | undefined, accepted: ReadonlySet<string>, key: string): ReadMark {
  const cell = cells?.[key]
  if (!cell || accepted.has(key)) return null
  return cell.check ? 'check' : 'read'
}

/** The fields of a draft set still to look at: doubtful ones first, in the form's order. */
export function remaining(cells: Record<string, ReadCell> | undefined, accepted: ReadonlySet<string>): { check: string[]; sure: string[] } {
  const check: string[] = []
  const sure: string[] = []
  for (const [key, cell] of Object.entries(cells ?? {})) {
    if (accepted.has(key) || key === 'first_serve') continue
    ;(cell.check ? check : sure).push(key)
  }
  return { check: check.sort(byForm), sure: sure.sort(byForm) }
}

const KIND_ORDER = ['lineup', 'box', 'to', 'sub', 'start', 'end']

/** The form's order: lineups, boxes (in the sheet's order: box 1 of I-VI, then box 2, ...),
 * timeouts, substitutions, times; ours before theirs. */
function byForm(a: string, b: string): number {
  const pa = a.split('.')
  const pb = b.split('.')
  const ka = KIND_ORDER.indexOf(pa[0] ?? '')
  const kb = KIND_ORDER.indexOf(pb[0] ?? '')
  if (ka !== kb) return ka - kb
  if (pa[1] !== pb[1]) return pa[1] === 'us' ? -1 : 1
  if (pa[0] === 'box') return Number(pa[3]) - Number(pb[3]) || Number(pa[2]) - Number(pb[2])
  return a < b ? -1 : a > b ? 1 : 0
}

/** All fields a set's reading proposed, accepted: what "accept the sure ones" adds. */
export function sureFields(cells: Record<string, ReadCell> | undefined): string[] {
  return Object.entries(cells ?? {})
    .filter(([key, cell]) => !cell.check && key !== 'first_serve')
    .map(([key]) => key)
}

/** A set can be confirmed once every field the reading proposed was looked at. */
export function canConfirm(cells: Record<string, ReadCell> | undefined, accepted: ReadonlySet<string>): boolean {
  const left = remaining(cells, accepted)
  return left.check.length === 0 && left.sure.length === 0
}

/** The fields an edit changed (a person typed them: they count as looked at). */
export function changedFields(before: SheetSetDoc, after: SheetSetDoc): string[] {
  const out: string[] = []
  for (const side of ['us', 'them'] as const) {
    const ga = side === 'us' ? before.boxes_us : before.boxes_them
    const gb = side === 'us' ? after.boxes_us : after.boxes_them
    for (let p = 0; p < Math.max(ga.length, gb.length); p++) {
      const ca = ga[p] ?? []
      const cb = gb[p] ?? []
      for (let b = 0; b < Math.max(ca.length, cb.length); b++) {
        if ((ca[b] ?? null) !== (cb[b] ?? null)) out.push(boxField(side, p + 1, b + 1))
      }
    }
    const la = (side === 'us' ? before.lineup_us : before.lineup_them) ?? []
    const lb = (side === 'us' ? after.lineup_us : after.lineup_them) ?? []
    for (let p = 0; p < 6; p++) if ((la[p] ?? null) !== (lb[p] ?? null)) out.push(lineupField(side, p + 1))
    const ta = before.timeouts.filter((t) => t.side === side)
    const tb = after.timeouts.filter((t) => t.side === side)
    for (let k = 0; k < 2; k++) {
      if (JSON.stringify(ta[k] ?? null) !== JSON.stringify(tb[k] ?? null)) out.push(timeoutField(side, k + 1))
    }
    for (let p = 1; p <= 6; p++) {
      const sa = before.subs.find((s) => s.side === side && s.position === p)
      const sb = after.subs.find((s) => s.side === side && s.position === p)
      if ((sa?.number ?? null) !== (sb?.number ?? null)) out.push(subField(side, p, 'number'))
      if (JSON.stringify(sa?.at ?? null) !== JSON.stringify(sb?.at ?? null)) out.push(subField(side, p, 'at'))
      if (JSON.stringify(sa?.back ?? null) !== JSON.stringify(sb?.back ?? null)) out.push(subField(side, p, 'back'))
    }
  }
  if (before.start !== after.start) out.push('start')
  if (before.end !== after.end) out.push('end')
  return out
}

/** A timeout's read field: the slot whose proposal is this score, else the slot's own. */
export function timeoutKey(cells: Record<string, ReadCell> | undefined, side: Side, slot: number, own: number | null, opp: number | null): string {
  for (const k of [1, 2]) {
    const v = cells?.[timeoutField(side, k)]?.v
    if (Array.isArray(v) && v[0] === own && v[1] === opp) return timeoutField(side, k)
  }
  return timeoutField(side, slot + 1)
}

/** The draft sets and how many of their cells are still to check. */
export function draftSummary(reading: SheetReading | null, accepted: Record<number, ReadonlySet<string>>): { sets: number[]; check: number } {
  const sets = reading?.drafts ?? []
  let check = 0
  for (const no of sets) check += remaining(reading?.sets[String(no)]?.cells, accepted[no] ?? new Set()).check.length
  return { sets, check }
}

/** The first doubtful field left, from this set on: [set, field]. */
export function nextCheck(reading: SheetReading | null, accepted: Record<number, ReadonlySet<string>>, from: number): [number, string] | null {
  const sets = [...(reading?.drafts ?? [])].sort((a, b) => a - b)
  const order = [...sets.filter((n) => n >= from), ...sets.filter((n) => n < from)]
  for (const no of order) {
    const left = remaining(reading?.sets[String(no)]?.cells, accepted[no] ?? new Set()).check
    if (left[0]) return [no, left[0]]
  }
  return null
}

/** What the reader read, for a tooltip: "15 (18? 16?)". */
export function altsText(cell: ReadCell | undefined): string {
  if (!cell) return ''
  const show = (v: unknown) => (Array.isArray(v) ? v.join(':') : v === null || v === undefined || v === '' ? '–' : String(v))
  const [first, ...rest] = cell.alts
  if (!first) return show(cell.v)
  const others = rest.filter(([, p]) => p >= 0.02).map(([v]) => `${show(v)}?`)
  return others.length ? `${show(first[0])} (${others.join(' ')})` : show(first[0])
}

export function isWorking(reading: SheetReading | null | undefined): boolean {
  return reading?.job?.status === 'queued' || reading?.job?.status === 'running'
}

// --- the API --------------------------------------------------------------------------------------

function afterSheet(client: ReturnType<typeof useQueryClient>, videoId: string, sheet: Scoresheet) {
  client.setQueryData(scoresheetKey(videoId), sheet)
  void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
  void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
  void client.invalidateQueries({ queryKey: keys.video(videoId) })
}

/** Read the photos again (a job), or decode the last reading as team A or B. */
export function useReadPhotos(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (us: 'A' | 'B' | null) => apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet/read`, { method: 'POST', body: { us } }),
    onSuccess: (sheet) => afterSheet(client, videoId, sheet),
  })
}

export function useDiscardReading(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet/read`, { method: 'DELETE' }),
    onSuccess: (sheet) => afterSheet(client, videoId, sheet),
  })
}

/** Save the sheet as checked and confirm these drafts. */
export function useConfirmSets(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: SheetIn & { confirm: number[] }) =>
      apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet/confirm`, { method: 'POST', body }),
    onSuccess: (sheet) => {
      afterSheet(client, videoId, sheet)
      void client.invalidateQueries({ queryKey: keys.videoStats(videoId) })
    },
  })
}

// The official scoresheet of a match (docs/PLAN.md §10.3a; research:
// docs/research/scoresheets-2026-10.md): what a coach types (GET/PUT/DELETE
// /api/videos/{id}/scoresheet, POST …/scoresheet/check), and the decoder that turns the service
// boxes into every point while they type. The server decodes the same way
// (backend wieplay/ml/sheet.py; both are tested against src/lib/scoresheet.fixture.json) and is
// the one that saves, repairs and scores; this copy is for the live check on every keystroke.
//
// The rule: when the serving team loses the serve, the scorer writes that team's own score in
// the box of the player who served. Read in service-turn order (box 1 of positions I-VI, then
// box 2, …; the team that received first has the X in position I box 1) and interleaved starting
// with the first server, each box minus the team's score when it won the serve back is its run
// of break points, and each box ends with one side-out point for the other team.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'
import { keys } from './queryClient'
import type { SheetPhoto } from './sheetPhotos'
import type { SheetReading } from './sheetRead'
import type { Side } from './types'

export const BOXES = 8
export const BOXES_DECIDING = 6
export const POSITIONS = ['I', 'II', 'III', 'IV', 'V', 'VI'] as const
export const SANCTION_KINDS = ['W', 'P', 'E', 'D', 'DW', 'DP'] as const
export type SanctionKind = (typeof SANCTION_KINDS)[number]
const POINT_KINDS: readonly SanctionKind[] = ['P', 'DP']

/** A team's service boxes: [position 0-5][box 0-7], the score written or null. */
export type Grid = (number | null)[][]

export interface TimeoutDoc {
  side: Side
  /** The requesting team's score, written first on the sheet. */
  own: number
  opp: number
}

export interface SubDoc {
  side: Side
  /** The starting player's position (column), 1-6. */
  position: number
  number: number | null
  /** [own, other] when the substitute came in; when the starting player came back. */
  at: [number, number] | null
  back: [number, number] | null
}

export interface SanctionDoc {
  side: Side
  kind: SanctionKind
  own: number
  opp: number
}

export interface CancelledDoc {
  us: number
  them: number
  rallies: number
}

/** One set as typed (and stored). */
export interface SheetSetDoc {
  no: number
  deciding?: boolean | null
  first_serve: Side | null
  lineup_us: (number | null)[] | null
  lineup_them: (number | null)[] | null
  boxes_us: Grid
  boxes_them: Grid
  /** [position, box] of the circled final. */
  circled_us: [number, number] | null
  circled_them: [number, number] | null
  timeouts: TimeoutDoc[]
  subs: SubDoc[]
  sanctions: SanctionDoc[]
  cancelled: CancelledDoc[]
  start: string | null
  end: string | null
  /** Line the scoring up with this set. */
  use: boolean
  /** Boxes changed from the paper because other records showed the scorer's slip. */
  corrections?: CorrectionDoc[]
}

/** A box corrected from the paper (POST …/scoresheet/correct): the paper's value, the new one. */
export interface CorrectionDoc {
  side: Side
  position: number
  box: number
  paper: number | null
  value: number | null
  by: CorrectionSource[]
}

/** On whose word: the club scoreboard's log, the video's serves, a coach's calls, the board. */
export type CorrectionSource = 'log' | 'video' | 'coach' | 'board'

export interface BoxRef {
  side: Side
  position: number
  box: number
}

export type Severity = 'error' | 'incomplete' | 'warning'

export interface Issue {
  code: string
  severity: Severity
  box: BoxRef | null
  params: Record<string, unknown>
}

export interface SheetPoint {
  winner: Side
  server: Side
  us: number
  them: number
  /** The server's starting position (1-6). */
  position: number
  rot_us: number
  rot_them: number
  penalty: boolean
}

export interface Decoded {
  points: SheetPoint[]
  final: [number, number]
  issues: Issue[]
  last: BoxRef | null
  /** Every box read and the set ends on a set end. */
  complete: boolean
  /** Complete, and nothing contradicts it. */
  consistent: boolean
}

// --- the turn order ---------------------------------------------------------------------------

export function isDeciding(set: Pick<SheetSetDoc, 'no' | 'deciding'>): boolean {
  return set.deciding ?? set.no === 5
}

export function boxesFor(deciding: boolean): number {
  return deciding ? BOXES_DECIDING : BOXES
}

export function targetFor(deciding: boolean): number {
  return deciding ? 15 : 25
}

export function isOver(a: number, b: number, target: number): boolean {
  return Math.max(a, b) >= target && Math.abs(a - b) >= 2
}

export function other(side: Side): Side {
  return side === 'us' ? 'them' : 'us'
}

/** [position 1-6, box] of a team's service turns in order; the first receiver skips I/1 (the X). */
export function cells(first: boolean, deciding: boolean): [number, number][] {
  const out: [number, number][] = []
  for (let k = first ? 0 : 1; k < 6 * boxesFor(deciding); k++) out.push([(k % 6) + 1, Math.floor(k / 6) + 1])
  return out
}

export function gridOf(set: SheetSetDoc, side: Side): Grid {
  return side === 'us' ? set.boxes_us : set.boxes_them
}

export function valueAt(set: SheetSetDoc, side: Side, position: number, box: number): number | null {
  return gridOf(set, side)[position - 1]?.[box - 1] ?? null
}

/** A team's turns up to its last filled box: [position, box, value or null]. */
export function turns(set: SheetSetDoc, side: Side): [number, number, number | null][] {
  const out = cells(set.first_serve === side, isDeciding(set)).map(([p, b]) => [p, b, valueAt(set, side, p, b)] as [number, number, number | null])
  let last = -1
  out.forEach(([, , v], i) => {
    if (v !== null) last = i
  })
  return out.slice(0, last + 1)
}

export function hasBoxes(set: SheetSetDoc): boolean {
  return [set.boxes_us, set.boxes_them].some((g) => g.some((col) => col.some((v) => v !== null)))
}

// --- decoding ---------------------------------------------------------------------------------

function issue(code: string, severity: Severity, box: BoxRef | null = null, params: Record<string, unknown> = {}): Issue {
  return { code, severity, box, params }
}

/** The set's points from its service boxes, with the checks (the module comment). */
export function decode(set: SheetSetDoc, official: [number, number] | null = null): Decoded {
  const deciding = isDeciding(set)
  const target = targetFor(deciding)
  const issues: Issue[] = []
  const empty = (list: Issue[]): Decoded => ({ points: [], final: [0, 0], issues: list, last: null, complete: false, consistent: false })
  if (set.first_serve === null) return empty([hasBoxes(set) ? issue('first_serve', 'incomplete') : issue('empty', 'incomplete')])
  const first = set.first_serve
  const seqs: Record<Side, [number, number, number | null][]> = { us: turns(set, 'us'), them: turns(set, 'them') }
  for (const side of ['us', 'them'] as const) {
    for (const [p, b, v] of seqs[side]) if (v === null) issues.push(issue('hole', 'error', { side, position: p, box: b }))
  }
  if (seqs.us.length === 0 && seqs.them.length === 0) return empty([issue('empty', 'incomplete')])

  const points: SheetPoint[] = []
  const score: Record<Side, number> = { us: 0, them: 0 }
  const used: Record<Side, number> = { us: 0, them: 0 }
  const rot: Record<Side, number> = { us: 0, them: 0 }
  let serving: Side = first
  let last: BoxRef | null = null
  let readAll = false
  const now = () => ({ us: score.us, them: score.them })
  const add = (winner: Side, server: Side, position: number) => {
    const [ru, rt] = [rot.us, rot.them]
    score[winner] += 1
    points.push({ winner, server, us: score.us, them: score.them, position, rot_us: ru, rot_them: rt, penalty: false })
    if (winner !== server) rot[winner] += 1
  }
  const nextBox = (side: Side): BoxRef | null => {
    const n = used[side]
    const typed = seqs[side][n]
    if (typed) return { side, position: typed[0], box: typed[1] }
    const cell = cells(side === first, deciding)[n]
    return cell ? { side, position: cell[0], box: cell[1] } : null
  }

  for (;;) {
    const over = isOver(score.us, score.them, target)
    const k = used[serving]
    const turn = seqs[serving][k]
    if (!turn) {
      const rest = used[other(serving)] < seqs[other(serving)].length
      if (over && rest) issues.push(issue('over', 'error', nextBox(other(serving)), now()))
      else issues.push(issue('missing', 'incomplete', nextBox(serving), now()))
      break
    }
    const [position, boxNo, value] = turn
    const ref: BoxRef = { side: serving, position, box: boxNo }
    if (value === null) break // a hole (flagged above)
    used[serving] += 1
    const run = value - score[serving]
    if (run < 0) {
      issues.push(issue('low', 'error', ref, { value, score: score[serving] }))
      break
    }
    if (over && run > 0) {
      issues.push(issue('over', 'error', ref, now()))
      break
    }
    let early = false
    for (let i = 0; i < run; i++) {
      add(serving, serving, position)
      if (isOver(score.us, score.them, target) && score[serving] < value) {
        issues.push(issue('over', 'error', ref, now()))
        early = true
        break
      }
    }
    if (early) break
    last = ref
    if (used.us >= seqs.us.length && used.them >= seqs.them.length) {
      readAll = true
      const won = other(serving)
      const a = score.us + (won === 'us' ? 1 : 0)
      const b = score.them + (won === 'them' ? 1 : 0)
      if (!isOver(score.us, score.them, target) && isOver(a, b, target)) {
        add(won, serving, position)
        issues.push(issue('final_missing', 'warning', nextBox(won), now()))
      }
      break
    }
    if (isOver(score.us, score.them, target)) {
      const rest = used[other(serving)] < seqs[other(serving)].length
      issues.push(issue('over', 'error', rest ? nextBox(other(serving)) : nextBox(serving), now()))
      break
    }
    add(other(serving), serving, position) // the side-out
    serving = other(serving)
  }

  const final: [number, number] = [score.us, score.them]
  const complete = readAll && isOver(final[0], final[1], target)
  if (readAll && !complete) issues.push(issue('not_over', 'incomplete', last, { us: final[0], them: final[1] }))
  if (complete) {
    for (const side of ['us', 'them'] as const) {
      const mark = side === 'us' ? set.circled_us : set.circled_them
      if (!mark) continue
      const seq = seqs[side]
      const lastBox = seq[seq.length - 1]
      const teamFinal = side === 'us' ? final[0] : final[1]
      if (!lastBox || lastBox[0] !== mark[0] || lastBox[1] !== mark[1] || lastBox[2] !== teamFinal) {
        issues.push(
          issue('circled', 'error', { side, position: mark[0], box: mark[1] }, {
            final: teamFinal,
            value: valueAt(set, side, mark[0], mark[1]),
            expected: lastBox ? { side, position: lastBox[0], box: lastBox[1] } : null,
          }),
        )
      }
    }
    if (official && (official[0] !== final[0] || official[1] !== final[1])) {
      issues.push(issue('official', 'error', last, { us: final[0], them: final[1], official_us: official[0], official_them: official[1] }))
    }
  }
  placeNotes(set, points, complete, issues)
  checkLineups(set, issues)
  const consistent = complete && !issues.some((i) => i.severity === 'error' || i.severity === 'incomplete')
  return { points, final, issues, last, complete, consistent }
}

/** How many points were played when the score was this (null: never). */
export function indexOf(points: SheetPoint[], us: number, them: number): number | null {
  if (us === 0 && them === 0) return 0
  const k = points.findIndex((p) => p.us === us && p.them === them)
  return k < 0 ? null : k + 1
}

function placeNotes(set: SheetSetDoc, points: SheetPoint[], complete: boolean, issues: Issue[]) {
  const known = complete && points.length > 0
  const perSide: Record<Side, number> = { us: 0, them: 0 }
  for (const t of set.timeouts) {
    perSide[t.side] += 1
    const [u, h] = t.side === 'us' ? [t.own, t.opp] : [t.opp, t.own]
    if (indexOf(points, u, h) === null && known) {
      issues.push(issue(indexOf(points, h, u) !== null ? 'timeout_mirror' : 'timeout_off', 'warning', null, { side: t.side, own: t.own, opp: t.opp }))
    }
  }
  for (const side of ['us', 'them'] as const) if (perSide[side] > 2) issues.push(issue('timeouts_many', 'warning', null, { side }))
  for (const sub of set.subs) {
    const places = [sub.at, sub.back].map((s) => {
      if (!s) return null
      const [u, h] = sub.side === 'us' ? s : [s[1], s[0]]
      const at = indexOf(points, u, h)
      if (at === null && known) issues.push(issue('sub_off', 'warning', null, { side: sub.side, position: sub.position, own: s[0], opp: s[1] }))
      return at
    })
    const [came, went] = places
    if (came != null && went != null && went < came) {
      issues.push(issue('sub_order', 'warning', null, { side: sub.side, position: sub.position }))
    }
  }
  for (const x of set.sanctions) {
    const [u, h] = x.side === 'us' ? [x.own, x.opp] : [x.opp, x.own]
    const at = indexOf(points, u, h)
    if (at === null) {
      if (known) issues.push(issue('sanction_off', 'warning', null, { side: x.side, kind: x.kind, own: x.own, opp: x.opp }))
      continue
    }
    if (!POINT_KINDS.includes(x.kind)) continue
    const p = points[at]
    if (p && p.winner === other(x.side)) p.penalty = true
    else if (known) issues.push(issue('penalty_off', 'warning', null, { side: x.side, kind: x.kind, own: x.own, opp: x.opp }))
  }
}

function checkLineups(set: SheetSetDoc, issues: Issue[]) {
  for (const side of ['us', 'them'] as const) {
    const lineup = side === 'us' ? set.lineup_us : set.lineup_them
    if (!lineup) continue
    const numbers = lineup.filter((n): n is number => n !== null)
    if (new Set(numbers).size !== numbers.length) issues.push(issue('lineup_twice', 'warning', null, { side }))
    for (const sub of set.subs) {
      if (sub.side === side && sub.number !== null && numbers.includes(sub.number)) {
        issues.push(issue('sub_on_court', 'warning', null, { side, position: sub.position, number: sub.number }))
      }
    }
  }
}

/** The first issue to show: errors first, then what is not finished, then notes. */
export function firstIssue(issues: Issue[]): Issue | null {
  for (const severity of ['error', 'incomplete', 'warning'] as const) {
    const found = issues.find((i) => i.severity === severity && i.code !== 'empty')
    if (found) return found
  }
  return null
}

// --- making and editing a set -------------------------------------------------------------------

export function emptyGrid(deciding: boolean): Grid {
  return Array.from({ length: 6 }, () => Array.from({ length: boxesFor(deciding) }, () => null))
}

export function emptySet(no: number, lineupUs: (number | null)[] | null = null): SheetSetDoc {
  const deciding = no === 5
  return {
    no,
    deciding: null,
    first_serve: null,
    lineup_us: lineupUs ? [...lineupUs] : [null, null, null, null, null, null],
    lineup_them: [null, null, null, null, null, null],
    boxes_us: emptyGrid(deciding),
    boxes_them: emptyGrid(deciding),
    circled_us: null,
    circled_them: null,
    timeouts: [],
    subs: [],
    sanctions: [],
    cancelled: [],
    start: null,
    end: null,
    use: true,
  }
}

/** A stored set with every field the form needs (older or partial rows filled in). */
export function normalised(set: Partial<SheetSetDoc> & { no: number }): SheetSetDoc {
  const base = emptySet(set.no)
  const deciding = isDeciding({ no: set.no, deciding: set.deciding ?? null })
  const grid = (g: Grid | undefined): Grid =>
    Array.from({ length: 6 }, (_, p) => Array.from({ length: boxesFor(deciding) }, (_, b) => g?.[p]?.[b] ?? null))
  return {
    ...base,
    ...set,
    lineup_us: set.lineup_us ?? base.lineup_us,
    lineup_them: set.lineup_them ?? base.lineup_them,
    boxes_us: grid(set.boxes_us),
    boxes_them: grid(set.boxes_them),
    timeouts: set.timeouts ?? [],
    subs: set.subs ?? [],
    sanctions: set.sanctions ?? [],
    cancelled: set.cancelled ?? [],
  }
}

export function withBox(set: SheetSetDoc, side: Side, position: number, box: number, value: number | null): SheetSetDoc {
  const key = side === 'us' ? 'boxes_us' : 'boxes_them'
  const grid = set[key].map((col, p) => (p === position - 1 ? col.map((v, b) => (b === box - 1 ? value : v)) : col))
  return { ...set, [key]: grid }
}

/**
 * A repair the server suggested, applied: a box changed (or an empty one filled); a box inserted
 * (the team's boxes from there move one on in the sheet's order) or deleted (they move one back).
 */
export function applyRepair(set: SheetSetDoc, repair: Pick<RepairOut, 'kind' | 'box' | 'new'>): SheetSetDoc {
  const { side, position, box } = repair.box
  if (repair.kind === 'change') return withBox(set, side, position, box, repair.new)
  const order = cells(set.first_serve === side, isDeciding(set))
  const at = order.findIndex(([p, b]) => p === position && b === box)
  if (at < 0) return set
  const values = order.map(([p, b]) => valueAt(set, side, p, b))
  if (repair.kind === 'insert') values.splice(at, 0, repair.new)
  else values.splice(at, 1)
  let out = set
  order.forEach(([p, b], i) => {
    out = withBox(out, side, p, b, values[i] ?? null)
  })
  return out
}

/** The stored shape: trailing empty boxes kept (fixed size), nothing else added. */
export function forSaving(sets: SheetSetDoc[]): SheetSetDoc[] {
  return sets.map((s) => ({ ...s }))
}

// --- what the server says ------------------------------------------------------------------------

export interface RepairOut {
  kind: 'change' | 'insert' | 'delete'
  box: BoxRef
  old: number | null
  new: number | null
  cost: number
  final: [number, number]
}

export interface LineOut {
  points: number
  lost: number
  served: number
  break_won: number
  received: number
  side_out_won: number
  side_out_pct: number | null
  break_point_pct: number | null
}

export interface RotationOut {
  rotation: number
  server: number | null
  setter_zone: number | null
  line: LineOut
}

export interface SideStatsOut {
  team: LineOut
  rotations: RotationOut[]
  longest_run: number
}

export interface SetStatsOut {
  us: SideStatsOut
  them: SideStatsOut
}

export interface NoteOut {
  index: number
  at_points: number | null
  at_ms: number | null
  back_points?: number | null
  back_ms?: number | null
}

export type SetStatus = 'empty' | 'incomplete' | 'inconsistent' | 'consistent'

export interface SetCheckOut {
  no: number
  deciding: boolean
  status: SetStatus
  use: 'path' | 'hint' | 'read' | null
  wanted: boolean
  final: [number, number]
  official: [number, number] | null
  last: BoxRef | null
  issues: Issue[]
  repair: RepairOut | null
  repairs: RepairOut[]
  points: SheetPoint[]
  timeouts: NoteOut[]
  subs: NoteOut[]
  stats: SetStatsOut | null
  /** The newest scoring with the sheet (lined up, missed, penalties, extra, flagged). */
  scoring: Record<string, unknown> | null
  /**
   * Where the newest scoring departs from this typed set (the board log and the video against
   * the paper): the boxes to change, between which scores, on whose word. Null while it keeps
   * to the set, or once the set changed.
   */
  correction?: CorrectionOut | null
}

export interface CorrectionOut {
  boxes: RepairOut[]
  from: [number, number]
  to: [number, number]
  sources: CorrectionSource[]
}

export interface ChecksOut {
  checks: SetCheckOut[]
  trusted: boolean
  match: SetStatsOut | null
  match_by_setter: boolean
}

export interface Scoresheet extends ChecksOut {
  video_id: string
  exists: boolean
  can_edit: boolean
  version: number
  use_in_scoring: boolean
  source: string | null
  sets: SheetSetDoc[]
  entered_by: string | null
  entered_at: string | null
  updated_by: string | null
  updated_at: string | null
  conflicts: { set: number; field: string; coach: unknown; sheet: unknown }[]
  official_sets: [number, number][] | null
  roster: { number: number; setter: boolean; libero: boolean }[]
  previous_lineup: (number | null)[] | null
  no_scoresheet: boolean
  /** Photos of the paper sheet (SS3): null for whoever may not see them (players, helpers). */
  images: SheetPhoto[] | null
  can_add_images: boolean
  /** SS4: "draft" while a set read from the photo waits for a coach's confirmation. */
  status: 'draft' | 'entered'
  /** The photo's reading on the club's server: its job, the drafts, the cells to check. */
  read: SheetReading | null
  can_read: boolean
}

export interface SheetIn {
  base_version: number
  sets: SheetSetDoc[]
  use_in_scoring: boolean
}

export const scoresheetKey = (videoId: string) => ['videos', videoId, 'scoresheet'] as const

export function useScoresheet(videoId: string, enabled = true) {
  return useQuery({
    queryKey: scoresheetKey(videoId),
    queryFn: ({ signal }) => apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet`, { signal }),
    enabled,
    // While the photo is being read on the club's server: a few seconds, then the drafts.
    refetchInterval: (query) => (['queued', 'running'].includes(query.state.data?.read?.job?.status ?? '') ? 2500 : false),
  })
}

export function useSaveScoresheet(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: SheetIn) => apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet`, { method: 'PUT', body }),
    onSuccess: (sheet) => {
      client.setQueryData(scoresheetKey(videoId), sheet)
      // The sets, the score line and the scoring follow the sheet.
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      void client.invalidateQueries({ queryKey: keys.video(videoId) })
      void client.invalidateQueries({ queryKey: keys.videoStats(videoId) })
    },
  })
}

export function useDeleteScoresheet(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<undefined>(`/api/videos/${videoId}/scoresheet`, { method: 'DELETE' }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: scoresheetKey(videoId) })
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
    },
  })
}

/**
 * Apply the scoring's correction to a set (docs/PLAN.md §10.3a): its boxes change as the board
 * log and the video say, saved as a new version (the paper's stays in the revisions).
 */
export function useCorrectScoresheet(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { set: number; base_version: number }) =>
      apiRequest<Scoresheet>(`/api/videos/${videoId}/scoresheet/correct`, { method: 'POST', body }),
    onSuccess: (sheet) => {
      client.setQueryData(scoresheetKey(videoId), sheet)
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      void client.invalidateQueries({ queryKey: keys.video(videoId) })
      void client.invalidateQueries({ queryKey: keys.videoStats(videoId) })
    },
  })
}

/** The server's checks (and repairs) of a sheet not saved yet. */
export function checkScoresheet(videoId: string, body: SheetIn, signal?: AbortSignal): Promise<ChecksOut> {
  return apiRequest<ChecksOut>(`/api/videos/${videoId}/scoresheet/check`, { method: 'POST', body, signal })
}

/** "I/3", "IV/2": a box by its position and its printed small number. */
export function boxName(box: Pick<BoxRef, 'position' | 'box'>): string {
  return `${POSITIONS[box.position - 1] ?? box.position}/${box.box}`
}

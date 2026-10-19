// Who was on court at each rally and the scoresheet's notes in the video (docs/PLAN.md §10.3a,
// §10.5): GET /api/videos/{id}/scoresheet/court. Per rally the sheet was lined up with: who
// served (team and number) and both teams' six by zone; per timeout, substitution and card the
// break it falls in, and what disagrees there (the score line, the club scoreboard's log, a
// break too short for a timeout, a rally lined up with a penalty point). Shirt numbers only;
// a card names the team, never a person.

import type { TFunction } from 'i18next'
import { useQuery } from '@tanstack/react-query'
import { apiRequest } from './api'
import { formatClock } from './labels'
import type { Side } from './types'

export interface CourtRally {
  rally: string
  set: number
  /** 1-based, in the set's points. */
  point: number
  serving: Side
  server: number | null
  /** Zones I-VI. */
  us: (number | null)[] | null
  them: (number | null)[] | null
  rotation_us: number
  rotation_them: number
  winner: Side
  penalty: boolean
}

export type NoteKind = 'timeout' | 'sub' | 'sub_back' | 'sanction'
export type NoteIssue = 'score_line' | 'log' | 'short_break' | 'penalty_rally' | 'off_path' | 'no_rally'
/** W warning, P penalty, E expulsion, D disqualification, DW delay warning, DP delay penalty. */
export type SanctionKind = 'W' | 'P' | 'E' | 'D' | 'DW' | 'DP'

export interface SheetNote {
  set: number
  kind: NoteKind
  index: number
  side: Side
  /** [us, them] as the sheet has it. */
  score: [number, number]
  at_points: number | null
  sanction: SanctionKind | null
  number_in: number | null
  position: number | null
  rally: string | null
  start_ms: number | null
  end_ms: number | null
  line: [number, number] | null
  log: [number, number] | null
  issues: NoteIssue[]
}

export interface SheetCourt {
  rallies: CourtRally[]
  notes: SheetNote[]
  liberos: Partial<Record<Side, number[]>>
}

export const sheetCourtKey = (videoId: string) => ['videos', videoId, 'scoresheet', 'court'] as const

export function useSheetCourt(videoId: string, enabled = true) {
  return useQuery({
    queryKey: sheetCourtKey(videoId),
    queryFn: ({ signal }) => apiRequest<SheetCourt>(`/api/videos/${videoId}/scoresheet/court`, { signal }),
    enabled,
    staleTime: 30_000,
  })
}

export function courtByRally(court: SheetCourt | undefined): Map<string, CourtRally> {
  return new Map((court?.rallies ?? []).map((r) => [r.rally, r]))
}

/** The notes in the break after a rally. */
export function notesAfter(court: SheetCourt | undefined, rallyId: string): SheetNote[] {
  return (court?.notes ?? []).filter((n) => n.rally === rallyId)
}

/** The notes the review asks to check, and every card (a marker even when nothing disagrees). */
export function notesToShow(court: SheetCourt | undefined): SheetNote[] {
  return (court?.notes ?? []).filter((n) => n.issues.length > 0 || n.kind === 'sanction')
}

interface RallySpan {
  id: string
  kind: string
  start_ms: number
  end_ms: number
}

/** A serve tag's leeway before its rally starts (the toss), as the server links them. */
const SERVE_SLACK_MS = 2000

/**
 * The sheet's server for a serve tagged at `t_ms` by `side`: the rally it falls in was lined up
 * with a point that team served. Null otherwise (a new rally the sheet is not lined up with
 * yet, the other team served, no sheet).
 */
export function sheetServer(
  court: SheetCourt | undefined,
  rallies: readonly RallySpan[],
  t_ms: number,
  side: Side,
): number | null {
  if (!court || court.rallies.length === 0) return null
  const rally = rallies.find((r) => r.kind === 'rally' && r.start_ms - SERVE_SLACK_MS <= t_ms && t_ms <= r.end_ms)
  if (!rally) return null
  const found = courtByRally(court).get(rally.id)
  return found && found.serving === side ? found.server : null
}

/** A card's colour: yellow for warnings, red for a point to the other team, darker for more. */
export function cardTone(kind: SanctionKind | null): 'yellow' | 'red' | 'dark' {
  if (kind === 'W' || kind === 'DW') return 'yellow'
  if (kind === 'P' || kind === 'DP') return 'red'
  return 'dark'
}

type Names = { us: string; them: string } | null

function team(t: TFunction, side: Side, names: Names): string {
  return names ? names[side] : t(side === 'us' ? 'sheetCourt.us' : 'sheetCourt.them')
}

function other(side: Side): Side {
  return side === 'us' ? 'them' : 'us'
}

/** "Yellow card (Volero) at 14:12", "Red card: point to KSCW H3 at 14:12", "Timeout (us) at 5:4". */
export function noteText(t: TFunction, note: SheetNote, names: Names = null): string {
  const score = `${note.score[0]}:${note.score[1]}`
  const who = team(t, note.side, names)
  switch (note.kind) {
    case 'timeout':
      return t('sheetCourt.timeout', { team: who, score })
    case 'sub':
      return t('sheetCourt.sub', { team: who, number: note.number_in ?? '?', score })
    case 'sub_back':
      return t('sheetCourt.subBack', { team: who, position: note.position ?? '?', score })
    case 'sanction': {
      const kind = note.sanction ?? 'W'
      if (kind === 'P' || kind === 'DP') return t(`sheetCourt.card.${kind}`, { team: who, to: team(t, other(note.side), names), score })
      return t(`sheetCourt.card.${kind}`, { team: who, score })
    }
  }
}

/** What disagrees at the note's break, in words. */
export function issueText(t: TFunction, note: SheetNote, issue: NoteIssue): string {
  const line = note.line ? `${note.line[0]}:${note.line[1]}` : '–'
  const log = note.log ? `${note.log[0]}:${note.log[1]}` : '–'
  switch (issue) {
    case 'score_line':
      return t('sheetCourt.issue.score_line', { score: line })
    case 'log':
      return t('sheetCourt.issue.log', { score: log })
    case 'short_break':
      return t('sheetCourt.issue.short_break', {
        seconds: note.start_ms != null && note.end_ms != null ? Math.round((note.end_ms - note.start_ms) / 1000) : '?',
      })
    case 'penalty_rally':
      return t('sheetCourt.issue.penalty_rally')
    case 'off_path':
      return t('sheetCourt.issue.off_path')
    case 'no_rally':
      return t('sheetCourt.issue.no_rally')
  }
}

/** Where the note falls in the video ("14:12"), or null. */
export function noteTime(note: SheetNote): string | null {
  return note.start_ms != null ? formatClock(note.start_ms, { tenths: false }) : null
}

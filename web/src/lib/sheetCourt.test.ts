import { describe, expect, it } from 'vitest'
import i18n from '../i18n'
import '../i18n/pages'
import { cardTone, courtByRally, issueText, notesAfter, notesToShow, noteText, sheetServer, type CourtRally, type SheetCourt, type SheetNote } from './sheetCourt'

const t = i18n.getFixedT('en')

const lineup = (rally: string, point: number, serving: 'us' | 'them', server: number): CourtRally => ({
  rally,
  set: 1,
  point,
  serving,
  server,
  us: [7, 3, 11, 5, 9, 1],
  them: [4, 8, 12, 14, 2, 6],
  rotation_us: 0,
  rotation_them: 0,
  winner: 'us',
  penalty: false,
})

const note = (patch: Partial<SheetNote>): SheetNote => ({
  set: 1,
  kind: 'timeout',
  index: 0,
  side: 'them',
  score: [5, 4],
  at_points: 9,
  sanction: null,
  number_in: null,
  position: null,
  rally: 'r9',
  start_ms: 852_000,
  end_ms: 900_000,
  line: [5, 4],
  log: [5, 4],
  issues: [],
  ...patch,
})

const court: SheetCourt = {
  rallies: [lineup('r1', 1, 'us', 7), lineup('r2', 2, 'them', 8)],
  notes: [note({}), note({ kind: 'sanction', sanction: 'W', rally: 'r1', index: 0 }), note({ kind: 'sub', index: 1, issues: ['score_line'], line: [4, 4], number_in: 17, side: 'us' })],
  liberos: { us: [10] },
}

const rallies = [
  { id: 'r1', kind: 'rally', start_ms: 10_000, end_ms: 18_000 },
  { id: 'r2', kind: 'rally', start_ms: 40_000, end_ms: 46_000 },
  { id: 'r3', kind: 'rally', start_ms: 70_000, end_ms: 76_000 },
]

describe('the scoresheet in the video', () => {
  it('names a serve’s server when its team served that rally', () => {
    expect(sheetServer(court, rallies, 12_000, 'us')).toBe(7)
    expect(sheetServer(court, rallies, 9_000, 'us')).toBe(7) // the toss, just before the rally
    expect(sheetServer(court, rallies, 12_000, 'them')).toBeNull() // we served it
    expect(sheetServer(court, rallies, 41_000, 'them')).toBe(8)
    expect(sheetServer(court, rallies, 71_000, 'us')).toBeNull() // not lined up
    expect(sheetServer(undefined, rallies, 12_000, 'us')).toBeNull()
  })

  it('finds the notes after a rally and those to check', () => {
    expect(courtByRally(court).get('r2')?.server).toBe(8)
    expect(notesAfter(court, 'r1').map((n) => n.kind)).toEqual(['sanction'])
    // The ones that disagree, and every card.
    expect(notesToShow(court).map((n) => n.kind)).toEqual(['sanction', 'sub'])
  })

  it('says what a note is without naming a person', () => {
    expect(noteText(t, note({}))).toBe('Timeout (them) at 5:4')
    expect(noteText(t, note({ kind: 'sanction', sanction: 'W' }), { us: 'KSCW H3', them: 'Volero' })).toBe('Yellow card (Volero) at 5:4')
    expect(noteText(t, note({ kind: 'sanction', sanction: 'P', side: 'us' }), { us: 'KSCW H3', them: 'Volero' })).toBe('Red card (KSCW H3): point to Volero at 5:4')
    expect(noteText(t, note({ kind: 'sub', number_in: 17, side: 'us' }))).toBe('Substitution (us): #17 in at 5:4')
    expect(cardTone('W')).toBe('yellow')
    expect(cardTone('DP')).toBe('red')
    expect(cardTone('E')).toBe('dark')
  })

  it('says what disagrees at the break', () => {
    const n = note({ issues: ['score_line', 'log', 'short_break'], line: [4, 4], log: [3, 4], start_ms: 0, end_ms: 12_000 })
    expect(issueText(t, n, 'score_line')).toBe('the score line says 4:4 here')
    expect(issueText(t, n, 'log')).toBe('the scoreboard’s log says 3:4 here')
    expect(issueText(t, n, 'short_break')).toMatch(/only 12 s/)
  })
})

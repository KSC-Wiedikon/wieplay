import { describe, expect, it } from 'vitest'
import i18n from '../i18n'
import '../i18n/pages'
import { gradeLabel, periodLabel, pieceFacts, pieceLabel, reasonText, segmentKindLabel, skillLabel } from './labels'
import type { Piece } from './types'
import { basketball, volleyball } from '../test/tagging'

const t = i18n.getFixedT('en')
const attack = volleyball.skills.find((s) => s.code === 'A') ?? null

const piece = (patch: Partial<Piece>): Piece => ({
  video_id: 'v1',
  start_ms: 15_000,
  end_ms: 20_000,
  event_id: 'e1',
  clip_id: null,
  caption: null,
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  code: 'A',
  grade: '#',
  side: 'us',
  number: 7,
  member_id: 107,
  player_name: 'Alex Muster',
  score_us: 1,
  score_them: 0,
  ...patch,
})

describe('pieceLabel', () => {
  it('names the player from the piece, then the skill and grade', () => {
    expect(pieceLabel(t, piece({}), attack)).toBe('#7 Alex Muster · Attack · # Kill')
    expect(pieceLabel(t, piece({ player_name: null }), attack)).toBe('#7 · Attack · # Kill')
    expect(pieceLabel(t, piece({ side: 'them', number: 12, player_name: null }), attack)).toBe('Them #12 · Attack · # Kill')
    expect(pieceLabel(t, piece({ side: 'them', number: 12, player_name: null, them_name: 'Volero' }), attack)).toBe('Volero #12 · Attack · # Kill')
    expect(pieceLabel(t, piece({ number: null, us_name: 'KSCW H3' }), attack)).toBe('KSCW H3 · Attack · # Kill')
  })

  it('prefers the caption, else the video for a piece without a tag', () => {
    expect(pieceLabel(t, piece({ caption: 'From zone 4' }), attack)).toBe('From zone 4')
    expect(pieceLabel(t, piece({ code: null }), null)).toBe('KSCW H3 – Volley Näfels')
  })
})

describe('pieceFacts', () => {
  it('gives the score after the rally; the player only when the label does not name them', () => {
    expect(pieceFacts(piece({}))).toBe('1:0')
    expect(pieceFacts(piece({ caption: 'From zone 4' }))).toBe('Alex Muster · 1:0')
    expect(pieceFacts(piece({ caption: 'From zone 4', score_us: null, score_them: null }))).toBe('Alex Muster')
  })

  it('is empty when neither is known', () => {
    expect(pieceFacts(piece({ score_us: null, score_them: null }))).toBe('')
    expect(pieceFacts(piece({ code: null, event_id: null, member_id: null, player_name: null, score_us: null, score_them: null }))).toBe('')
  })
})

describe('basketball labels', () => {
  const de = i18n.getFixedT('de')
  const skill = (code: string) => basketball.skills.find((s) => s.code === code) ?? null

  it('names every basketball skill in English and German', () => {
    expect(basketball.skills.map((s) => skillLabel(t, s))).toEqual([
      'Two-pointer', 'Three-pointer', 'Free throw', 'Offensive rebound', 'Defensive rebound', 'Assist', 'Turnover', 'Steal', 'Block', 'Foul',
    ])
    expect(basketball.skills.map((s) => skillLabel(de, s))).toEqual([
      'Zweier', 'Dreier', 'Freiwurf', 'Offensivrebound', 'Defensivrebound', 'Assist', 'Ballverlust', 'Steal', 'Block', 'Foul',
    ])
  })

  it('says made or missed for shots', () => {
    expect(gradeLabel(t, skill('S3'), '+')).toBe('Made')
    expect(gradeLabel(t, skill('FT'), '-')).toBe('Missed')
    expect(gradeLabel(de, skill('S2'), '+')).toBe('Getroffen')
    expect(gradeLabel(de, skill('S2'), '-')).toBe('Verfehlt')
    expect(gradeLabel(t, skill('RO'), '+')).toBe('+')
  })

  it('counts quarters, then overtimes', () => {
    expect([1, 4, 5, 6].map((no) => periodLabel(t, 'basketball', no))).toEqual(['Q1', 'Q4', 'OT', 'OT2'])
    expect([1, 5, 6].map((no) => periodLabel(de, 'basketball', no))).toEqual(['1. Viertel', 'Verlängerung', '2. Verlängerung'])
    expect(periodLabel(t, 'volleyball', 2)).toBe('Set 2')
  })

  it('calls a break between quarters a quarter break', () => {
    expect(segmentKindLabel(t, 'set_break', 'basketball')).toBe('Quarter break')
    expect(segmentKindLabel(t, 'set_break', 'volleyball')).toBe('Set break')
    expect(segmentKindLabel(t, 'timeout', 'basketball')).toBe('Timeout')
    expect(skillLabel(t, volleyball.skills[0])).toBe('Serve')
  })
})

describe('reasonText: the typed sheet against the board log', () => {
  const names = { us: 'KSCW H3', them: 'Volero', usFull: 'KSC Wiedikon H3', themFull: 'VBC Voléro Zürich' }
  const de = i18n.getFixedT('de')

  it('says who says what, and the side the video takes', () => {
    const reason = { code: 'sheet_log' as const, log: 'us' as const, sheet: 'them' as const, log_score: [4, 2] as [number, number], sheet_score: [3, 3] as [number, number], video: 'log' as const, follows: 'log' as const }
    expect(reasonText(t, reason, names)).toBe('The scoresheet and the board log disagree here (log: KSCW H3, sheet: Volero). The video sides with the board log.')
    expect(reasonText(de, reason, names)).toBe('Matchblatt und Anzeigetafel-Protokoll sind hier uneins (Protokoll: KSCW H3, Matchblatt: Volero). Das Video gibt dem Anzeigetafel-Protokoll recht.')
    expect(reasonText(t, { ...reason, video: null }, names)).toMatch(/The video can’t tell\.$/)
    expect(reasonText(t, { ...reason, video: 'sheet' }, names)).toMatch(/The video sides with the scoresheet\.$/)
  })

  it('names the scores where only the score after the rally differs', () => {
    const reason = { code: 'sheet_log' as const, log: 'them' as const, sheet: 'them' as const, log_score: [4, 3] as [number, number], sheet_score: [3, 4] as [number, number], video: 'log' as const }
    expect(reasonText(t, reason, names)).toBe('The scoresheet and the board log disagree on the score here (log 4:3, sheet 3:4). The video sides with the board log.')
  })

  it('keeps the old wording for a reason stored before the video was named', () => {
    expect(reasonText(t, { code: 'sheet_log', log: 'us', sheet: 'them' }, names)).toBe('The scoresheet and the board log disagree here (log: KSCW H3, sheet: Volero).')
  })

  it('says where the score line leaves the sheet', () => {
    expect(reasonText(t, { code: 'sheet', team: 'them', score: [3, 3] }, names)).toBe('The score line leaves the scoresheet here (the sheet: Volero, 3:3).')
    expect(reasonText(t, { code: 'sheet' }, names)).toBe('The official scoresheet says otherwise.')
  })
})

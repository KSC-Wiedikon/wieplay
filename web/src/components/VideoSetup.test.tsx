import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CourtState } from '../lib/court'
import type { PlayersSummary } from '../lib/players'
import type { ScoreboardState, ScoreLogState } from '../lib/scoreboard'
import type { Scoresheet } from '../lib/scoresheet'
import { analysisState, ballState, courtState as courtStepState, namesState, playersState, scoreboardState, sheetState } from '../lib/setup'
import type { Analysis, TeamNames, Video } from '../lib/types'
import { CORNER_POINTS, courtState, savedCalibration } from '../test/court'
import { PLAYERS_VIDEO_ID as ID, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { VideoSetup } from './VideoSetup'

const BOX = { x: 0, y: 0.84, w: 0.03, h: 0.05 }
const region = { id: 'r1', rect: BOX, sets: null, kind: 'flip' as const, hall_name: 'Halle Test', at_ms: null, copied_from: null, updated_at: '2026-10-17T10:00:00Z' }
const reading = {
  at: '2026-10-17T10:00:00Z',
  summary: {},
  segments: [
    { first_ms: 0, last_ms: 4000, left: 0, right: 0, set_no: 1, confidence: 0.9, kind: 'start' as const },
    { first_ms: 9000, last_ms: 14000, left: 1, right: 0, set_no: 1, confidence: 0.9, kind: 'step' as const },
  ],
}

const names = (patch: Partial<TeamNames> = {}): TeamNames => ({
  us: 'KSCW H3',
  them: 'Volley Näfels',
  us_full: 'KSC Wiedikon H3',
  them_full: 'Volley Näfels',
  them_from: 'game',
  us_set: null,
  them_set: null,
  ...patch,
})

const board = (over: Partial<ScoreboardState> = {}): ScoreboardState => ({
  video_id: ID,
  can_edit: true,
  aspect: 16 / 9,
  region: null,
  suggestion: null,
  job: null,
  reading: null,
  ...over,
})

const scoreLog = (patch: Partial<ScoreLogState> = {}): ScoreLogState => ({
  source: 'live_scores_revisions',
  imported_at: '2026-10-18T10:00:00Z',
  trusted: true,
  sets: [1, 2, 3, 4].map((no) => ({ no, final: [25, 20], official: [25, 20], agrees: true, points: 45, matched: 40, trusted: true })),
  points: 180,
  matched: 160,
  matched_share: 0.89,
  shifts: 1,
  rallies_told: 160,
  path_agrees: 160,
  disagree_with_video: 12,
  ...patch,
})

const sheet = (patch: Partial<Scoresheet> = {}): Scoresheet => ({
  video_id: ID,
  exists: false,
  can_edit: true,
  version: 0,
  use_in_scoring: true,
  source: null,
  sets: [],
  entered_by: null,
  entered_at: null,
  updated_by: null,
  updated_at: null,
  conflicts: [],
  official_sets: [
    [25, 20],
    [25, 20],
    [25, 20],
  ],
  roster: [],
  previous_lineup: null,
  no_scoresheet: false,
  images: null,
  can_add_images: false,
  status: 'entered',
  read: null,
  can_read: false,
  checks: [],
  trusted: false,
  match: null,
  match_by_setter: false,
  ...patch,
})

const setCheck = (no: number, status: 'consistent' | 'inconsistent') => ({
  no,
  deciding: false,
  status,
  use: status === 'consistent' ? ('path' as const) : ('hint' as const),
  wanted: true,
  final: [25, 20] as [number, number],
  official: [25, 20] as [number, number],
  last: null,
  issues: [],
  repair: null,
  repairs: [],
  points: [],
  timeouts: [],
  subs: [],
  stats: null,
  scoring: null,
})

const players = (patch: Partial<PlayersSummary> = {}): PlayersSummary => ({
  status: 'none',
  run_id: null,
  clusters: 0,
  named: 0,
  clusters_us: 0,
  clusters_them: 0,
  numbers_read: 0,
  actions: 0,
  identified: 0,
  identified_share: null,
  suggested: 0,
  compared: 0,
  accuracy: null,
  ...patch,
})

const analysis = (patch: Partial<Analysis> = {}): Analysis => ({
  status: 'done',
  model_run: null,
  proposals: { unreviewed: 0, confirmed: 4, corrected: 1, rejected: 1 },
  metrics_vs_human: null,
  ...patch,
})

interface Setup {
  video?: Partial<Video>
  analysis?: Analysis | (() => Analysis)
  court?: CourtState
  scoreboard?: ScoreboardState | (() => ScoreboardState)
  scoresheet?: Scoresheet
  routes?: Record<string, { status?: number; body?: unknown }>
  rallies?: number
}

function show({
  video = {},
  analysis: a = analysis(),
  court = courtState({ calibration: savedCalibration() }),
  scoreboard = board({ region, reading, job: { status: 'done', progress: null, error: null } }),
  // The setup's other tests: a coach said there is no scoresheet (the step is done).
  scoresheet = sheet(),
  routes = {},
  rallies = 5,
}: Setup = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}/analysis`]: { body: a },
      [`/api/videos/${ID}/court`]: { body: court },
      [`/api/videos/${ID}/scoreboard`]: { body: scoreboard },
      [`/api/videos/${ID}/scoresheet`]: { body: scoresheet },
      ...routes,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const v = readyVideo({ names: names(), rallies_reviewed_at: '2026-10-16T19:00:00Z', no_scoresheet: true, ...video })
  const view = renderRoute(<VideoSetup video={v} element={null} rallies={rallies} actionTags={0} />)
  return { ...view, fetch }
}

const header = () => screen.getByRole('button', { name: /^Setup/ })
const steps = () => [...document.querySelectorAll('[data-step-state]')].map((li) => [li.querySelector('h3')?.firstChild?.textContent, li.getAttribute('data-step-state')])

afterEach(() => vi.unstubAllGlobals())

describe('the states of the steps', () => {
  it('reads the analysis: running, failed, to start, to review, to mark reviewed, done', () => {
    const video = { rallies_reviewed_at: null }
    expect(analysisState(undefined, video, 0)).toBe('waiting')
    expect(analysisState(analysis({ status: 'running' }), video, 0)).toBe('busy')
    expect(analysisState(analysis({ status: 'failed' }), video, 0)).toBe('attention')
    expect(analysisState(analysis({ status: 'none' }), video, 0)).toBe('todo')
    expect(analysisState(analysis({ proposals: { unreviewed: 3, confirmed: 0, corrected: 0, rejected: 0 } }), video, 0)).toBe('attention')
    expect(analysisState(analysis(), video, 5)).toBe('todo') // all answered: "rallies reviewed" is left
    expect(analysisState(analysis(), { rallies_reviewed_at: '2026-10-16T19:00:00Z' }, 5)).toBe('done')
    expect(analysisState(analysis({ proposals: { unreviewed: 0, confirmed: 0, corrected: 0, rejected: 0 } }), video, 0)).toBe('done') // nothing found
  })

  it('reads the ball path, the court, the scoreboard, the players and the names', () => {
    const track = courtState().ball_track
    expect(ballState(courtState())).toBe('done')
    expect(ballState(courtState({ ball_track: { ...track, available: false, job: 'running' } }))).toBe('busy')
    expect(ballState(courtState({ ball_track: { ...track, available: false, job: 'failed' } }))).toBe('attention')
    expect(ballState(courtState({ ball_track: { ...track, available: false, job: 'none' } }))).toBe('waiting')

    expect(courtStepState(courtState())).toBe('todo')
    expect(courtStepState(courtState({ court_job: 'running' }))).toBe('busy')
    expect(courtStepState(courtState({ calibration: savedCalibration({ method: 'auto-hall', checked: false }) }))).toBe('attention')
    expect(courtStepState(courtState({ calibration: savedCalibration() }))).toBe('done')
    // WiePlay sure of the court it set: nothing to do; a court it found but isn't sure of: check it.
    expect(courtStepState(courtState({ calibration: savedCalibration({ method: 'auto', checked: false, sure: true, confidence: 0.9 }) }))).toBe('done')
    const proposal = { source: 'auto' as const, points: CORNER_POINTS, confidence: 0.55, sure: false, video_id: ID, video_title: null, recorded_on: null, calibration_id: 'p1' }
    expect(courtStepState(courtState({ suggestions: [proposal] }))).toBe('attention')
    expect(courtStepState(courtState({ suggestions: [{ ...proposal, source: 'team' }] }))).toBe('todo')

    expect(scoreboardState(board(), false)).toBe('todo')
    expect(scoreboardState(board(), true)).toBe('done') // "no scoreboard in this video"
    expect(scoreboardState(board({ region, job: { status: 'running', progress: 0.4, error: null } }), false)).toBe('busy')
    expect(scoreboardState(board({ region, job: { status: 'failed', progress: null, error: 'x' } }), false)).toBe('attention')
    expect(scoreboardState(board({ region, reading: { ...reading, segments: [] } }), false)).toBe('attention')
    expect(scoreboardState(board({ region, reading }), false)).toBe('done')
    // The club scoreboard's log matching the official result: no box needed.
    expect(scoreboardState(board({ log: scoreLog() }), false)).toBe('done')
    expect(scoreboardState(board({ log: scoreLog({ trusted: false }) }), false)).toBe('todo')

    expect(playersState(players())).toBe('optional')
    expect(playersState(players({ status: 'running' }))).toBe('busy')
    expect(playersState(players({ status: 'failed' }))).toBe('attention')
    expect(playersState(players({ status: 'done', run_id: 'run1', clusters: 14 }))).toBe('done')

    expect(namesState({ names: names() })).toBe('done')
    expect(namesState({ names: names({ them: null, them_from: null }) })).toBe('todo')

    // The official scoresheet: a league game has one (to do), a friendly may (optional).
    const league = { game_id: 501, no_scoresheet: false }
    expect(sheetState(undefined, league)).toBe('waiting')
    expect(sheetState(sheet(), league)).toBe('todo')
    expect(sheetState(sheet(), { game_id: null, no_scoresheet: false })).toBe('optional')
    expect(sheetState(sheet(), { ...league, no_scoresheet: true })).toBe('done')
    expect(sheetState(sheet({ exists: true, trusted: true }), league)).toBe('done')
    expect(sheetState(sheet({ exists: true, checks: [setCheck(1, 'consistent'), setCheck(2, 'inconsistent')] }), league)).toBe('attention')
    // A scoresheet whose every set fits the official result: no scoreboard box needed.
    expect(scoreboardState(board(), false, true)).toBe('done')
  })
})

describe('VideoSetup', () => {
  it('puts the open steps first and groups the completed ones, open while something needs the coach', async () => {
    show({
      court: courtState({ calibration: savedCalibration({ method: 'auto-hall', checked: false }) }),
      analysis: analysis({ players: players() }),
    })
    const court = await screen.findByRole('button', { name: 'Check the court' })
    expect(header()).toHaveAttribute('aria-expanded', 'true')
    expect(header()).toHaveTextContent('5 of 7 done · 1 needs you')
    expect(steps()).toEqual([
      ['Court', 'attention'],
      ['Players', 'optional'],
      ['Rallies and score', 'done'],
      ['Ball path', 'done'],
      ['Scoreboard', 'done'],
      ['Scoresheet', 'done'],
      ['Team names', 'done'],
    ])
    const completed = screen.getByRole('region', { name: 'Completed (5)' })
    expect(within(completed).getByText('Scoreboard read: 2 scores')).toBeInTheDocument()
    expect(within(completed).getByText('KSCW H3 – Volley Näfels')).toBeInTheDocument()
    expect(within(completed).getByText('From the game in Wiedisync.')).toBeInTheDocument()
    expect(court).toHaveClass('bg-accent') // the coach's turn: the primary button

    // The coach folds it away.
    fireEvent.click(header())
    expect(header()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Check the court' })).not.toBeInTheDocument()
  })

  it('stays folded when nothing needs the coach, and opens on request', async () => {
    show()
    await waitFor(() => expect(header()).toHaveTextContent('6 of 6 done'))
    expect(header()).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(header())
    expect(screen.getByRole('button', { name: 'Edit the court' })).toBeInTheDocument()
    expect(screen.getByText('Found. “Trajectories” under the video draws it.')).toBeInTheDocument()
  })

  it('asks for the court: set up, or check what WiePlay set', async () => {
    show({ court: courtState() })
    expect(await screen.findByRole('button', { name: 'Set up the court' })).toBeInTheDocument()
    expect(screen.getByText('Not set up. With the court, the ball’s landings count in the tendencies.')).toBeInTheDocument()
  })

  it('shows the analysis with its review link and the switch that teaches WiePlay', async () => {
    const toCheck = { rallies: 2, breaks: 1, sets: 0, actions: 2, players: 0, total: 5 }
    show({ analysis: analysis({ proposals: { auto: 0, check: 2, confirmed: 3, corrected: 0, rejected: 1 }, to_check: toCheck }), video: { rallies_reviewed_at: null } })
    expect(await screen.findByRole('link', { name: 'To check (5)' })).toHaveAttribute('href', `/videos/${ID}/tag?mode=review`)
    expect(screen.getByText('WiePlay found 6 rallies.')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Rallies reviewed' })).toBeInTheDocument()
    expect(steps()[0]).toEqual(['Rallies and score', 'attention'])
  })

  it('says there is no scoreboard in this video', async () => {
    const patched: unknown[] = []
    const { queryClient } = show({
      scoreboard: board(),
      routes: {
        [`PATCH /api/videos/${ID}/setup`]: {
          body: ({ body }: { body: unknown }) => {
            patched.push(body)
            return readyVideo({ names: names(), no_scoreboard: true })
          },
        },
      },
    })
    expect(await screen.findByText('Not marked. If the scoreboard is in the picture, WiePlay reads it for the score line.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'No scoreboard in this video' }))
    await waitFor(() => expect(patched).toEqual([{ no_scoreboard: true }]))
    expect(queryClient.getQueryData(['videos', ID])).toMatchObject({ no_scoreboard: true })
  })

  it('shows a video without a scoreboard as done', async () => {
    show({ scoreboard: board(), video: { no_scoreboard: true } })
    fireEvent.click(await screen.findByRole('button', { name: /^Setup/ }))
    const completed = await screen.findByRole('region', { name: 'Completed (6)' })
    expect(within(completed).getByText('No scoreboard in this video.')).toBeInTheDocument()
    expect(within(completed).getByRole('button', { name: 'Mark the scoreboard' })).toBeInTheDocument()
  })

  it("takes the score from the club scoreboard's log: done, no box asked for", async () => {
    show({ scoreboard: board({ log: scoreLog() }) })
    fireEvent.click(await screen.findByRole('button', { name: /^Setup/ }))
    const completed = await screen.findByRole('region', { name: 'Completed (6)' })
    expect(within(completed).getByText('Score from the club scoreboard’s log: 4 of 4 sets match the official result.')).toBeInTheDocument()
    expect(within(completed).queryByRole('button', { name: 'Mark the scoreboard' })).not.toBeInTheDocument()
  })

  it('shows the reading’s progress and lets a failed one be read again', async () => {
    show({ scoreboard: board({ region, job: { status: 'running', progress: 0.42, error: null } }) })
    fireEvent.click(await screen.findByRole('button', { name: /1 running/ }))
    expect(await screen.findByText('Reading the scoreboard… 42 %')).toBeInTheDocument()
  })

  it('reads a failed scoreboard again', async () => {
    const reads: string[] = []
    show({
      scoreboard: board({ region, job: { status: 'failed', progress: null, error: 'x' } }),
      routes: {
        [`POST /api/videos/${ID}/scoreboard/read`]: {
          body: ({ path }: { path: string }) => {
            reads.push(path)
            return board({ region, job: { status: 'queued', progress: null, error: null } })
          },
        },
      },
    })
    expect(await screen.findByText('Couldn’t read the scoreboard.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }))
    await waitFor(() => expect(reads).toHaveLength(1))
  })

  it('draws a scoreboard box, takes the hall’s last one, saves', async () => {
    const saved: unknown[] = []
    const suggestion = { source: 'hall' as const, region: { ...region, id: 'old' }, video_title: 'H3 – Testclub A', recorded_on: '2026-10-01', hall_name: 'Halle Test' }
    show({
      scoreboard: () => (saved.length ? board({ region }) : board({ suggestion })),
      routes: {
        [`PUT /api/videos/${ID}/scoreboard`]: {
          body: ({ body }: { body: unknown }) => {
            saved.push(body)
            return board({ region })
          },
        },
      },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Mark the scoreboard' }))
    const dialog = await screen.findByRole('dialog', { name: 'Mark the scoreboard' })
    expect(within(dialog).getByText(/this hall’s last video: H3 – Testclub A/)).toBeInTheDocument()
    expect(within(dialog).getByTestId('box-score')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save and read' }))
    await waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0]).toMatchObject({ rect: BOX, kind: 'flip', copied_from: 'old' })

    // Drawing a box of its own: no longer the copy.
    fireEvent.click(await screen.findByRole('button', { name: 'Edit the box' }))
    const again = await screen.findByRole('dialog', { name: 'Mark the scoreboard' })
    const area = within(again).getByRole('application')
    area.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 562, right: 1000, bottom: 562, x: 0, y: 0, toJSON: () => ({}) })
    fireEvent.pointerDown(area, { clientX: 100, clientY: 400, pointerId: 1 })
    fireEvent.pointerMove(area, { clientX: 200, clientY: 450, pointerId: 1 })
    fireEvent.pointerUp(area, { pointerId: 1 })
    fireEvent.click(within(again).getByRole('button', { name: 'Lit digits' }))
    fireEvent.click(within(again).getByRole('button', { name: 'Save and read' }))
    await waitFor(() => expect(saved).toHaveLength(2))
    const second = saved[1] as { rect: { x: number; w: number }; kind: string; copied_from: string | null }
    expect(second.kind).toBe('led')
    expect(second.copied_from).toBeNull()
    expect(second.rect.x).toBeCloseTo(0.1)
    expect(second.rect.w).toBeCloseTo(0.1)
  })

  it('finds the players on demand (beta)', async () => {
    const found: string[] = []
    show({
      analysis: analysis({ players: players() }),
      court: courtState(),
      routes: {
        [`POST /api/videos/${ID}/players/find`]: {
          status: 202,
          body: ({ path }: { path: string }) => {
            found.push(path)
            return {}
          },
        },
      },
    })
    const button = await screen.findByRole('button', { name: 'Find players (beta, takes about 2 hours)' })
    expect(screen.getByText('Not looked for yet. Finding the players is a beta: it takes about 2 hours per match.')).toBeInTheDocument()
    fireEvent.click(button)
    await waitFor(() => expect(found).toEqual([`/api/videos/${ID}/players/find`]))
  })

  it('asks to name an unknown opponent, and sets both names for the video', async () => {
    const patched: unknown[] = []
    show({
      video: { names: names({ them: null, them_full: null, them_from: null }), title: 'Friendly' },
      routes: {
        [`PATCH /api/videos/${ID}/setup`]: {
          body: ({ body }: { body: unknown }) => {
            patched.push(body)
            return readyVideo({ names: names({ them: 'Volero', them_full: 'Volero', them_from: 'set', them_set: 'Volero' }) })
          },
        },
      },
    })
    expect(await screen.findByText('KSCW H3 – Opponent')).toBeInTheDocument()
    expect(screen.getByText('The opponent isn’t known, so the app says “Opponent”. Name it here.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Edit the names' }))
    const dialog = await screen.findByRole('dialog', { name: 'Team names' })
    const us = within(dialog).getByRole('textbox', { name: 'Our team' })
    expect(us).toHaveAttribute('placeholder', 'KSCW H3')
    expect(within(dialog).getByText('Empty: KSCW H3 · Official name: KSC Wiedikon H3')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Opponent' }), { target: { value: '  Volero ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save names' }))
    await waitFor(() => expect(patched).toEqual([{ name_us: null, name_them: 'Volero' }]))
    expect(await screen.findByText('Names saved.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('asks for the scoresheet of a league game: type it in, or say there is none', async () => {
    const patched: unknown[] = []
    show({
      video: { no_scoresheet: false },
      routes: {
        [`PATCH /api/videos/${ID}/setup`]: {
          body: ({ body }: { body: unknown }) => {
            patched.push(body)
            return readyVideo({ names: names(), no_scoresheet: true })
          },
        },
      },
    })
    expect(await screen.findByText('Not entered. With the official scoresheet, every point of the match is known.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Type it in' })).toHaveAttribute('href', `/videos/${ID}/scoresheet`)
    fireEvent.click(screen.getByRole('button', { name: 'No scoresheet' }))
    await waitFor(() => expect(patched).toEqual([{ no_scoresheet: true }]))
  })

  it('takes the score from a scoresheet whose sets all fit: the scoreboard step is done too', async () => {
    show({
      scoreboard: board(),
      video: { no_scoresheet: false },
      scoresheet: sheet({ exists: true, version: 2, trusted: true, checks: [1, 2, 3].map((no) => setCheck(no, 'consistent')) }),
    })
    fireEvent.click(await screen.findByRole('button', { name: /^Setup/ }))
    const completed = await screen.findByRole('region', { name: 'Completed (6)' })
    expect(within(completed).getByText('Score from the official scoresheet: every set fits the official result.')).toBeInTheDocument()
    expect(within(completed).getByText('Entered; every set fits the official result.')).toBeInTheDocument()
    expect(within(completed).queryByRole('button', { name: 'Mark the scoreboard' })).not.toBeInTheDocument()
    expect(within(completed).getByRole('link', { name: 'Open the scoresheet' })).toBeInTheDocument()
  })

  it('needs the coach when a set of the scoresheet does not fit', async () => {
    show({ video: { no_scoresheet: false }, scoresheet: sheet({ exists: true, version: 1, checks: [setCheck(1, 'consistent'), setCheck(2, 'inconsistent')] }) })
    expect(await screen.findByText('Entered; 1 set to check.')).toBeInTheDocument()
    expect(steps()[0]).toEqual(['Scoresheet', 'attention'])
  })
})

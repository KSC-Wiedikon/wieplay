import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CorrectionOut, Scoresheet, SetCheckOut, SheetSetDoc } from '../lib/scoresheet'
import { PLAYERS_VIDEO_ID as ID, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { ScoresheetPage } from './ScoresheetPage'

// The scoring found a scorer's slip (docs/PLAN.md §10.3a): at 1:1 the scorer missed our side-out
// and wrote their next three points under the server before it. The board log and the video say
// their boxes II/1 and III/1 are 1 and 4, not 4 and 5. The page names the boxes and applies the
// correction as a new version.

const NAMES = { us: 'KSCW H3', them: 'Volley Näfels', us_full: 'KSC Wiedikon H3', them_full: 'Volley Näfels', them_from: 'game' as const, us_set: null, them_set: null }

const grid = (cols: (number | null)[][]) => cols.map((c) => [...c, ...Array<null>(8 - c.length).fill(null)])
const US = grid([
  [1, 9, 15],
  [2, 10, 16],
  [5, 11, 17],
  [6, 12, 18],
  [7, 13, 19],
  [8, 14, 25],
])
const PAPER = grid([
  [null, 9, 15],
  [4, 10, 16],
  [5, 11, 17],
  [6, 12, 18],
  [7, 13, 19],
  [8, 14, 20],
])
const FIXED = grid([
  [null, 9, 15],
  [1, 10, 16],
  [4, 11, 17],
  [6, 12, 18],
  [7, 13, 19],
  [8, 14, 20],
])

const setDoc = (boxesThem: (number | null)[][], patch: Partial<SheetSetDoc> = {}): SheetSetDoc => ({
  no: 1,
  deciding: null,
  first_serve: 'us',
  lineup_us: [7, 3, 11, 5, 9, 1],
  lineup_them: [4, 8, 12, 14, 2, 6],
  boxes_us: US,
  boxes_them: boxesThem,
  circled_us: [6, 3],
  circled_them: [6, 3],
  timeouts: [],
  subs: [],
  sanctions: [],
  cancelled: [],
  start: null,
  end: null,
  use: true,
  ...patch,
})

const CORRECTION: CorrectionOut = {
  boxes: [
    { kind: 'change', box: { side: 'them', position: 2, box: 1 }, old: 4, new: 1, cost: 1.5, final: [25, 20] },
    { kind: 'change', box: { side: 'them', position: 3, box: 1 }, old: 5, new: 4, cost: 1, final: [25, 20] },
  ],
  from: [1, 1],
  to: [5, 5],
  sources: ['log', 'video'],
}

const check = (correction: CorrectionOut | null, scoring: Record<string, number> = {}): SetCheckOut => ({
  no: 1,
  deciding: false,
  status: 'consistent',
  use: 'path',
  wanted: true,
  final: [25, 20],
  official: [25, 20],
  last: null,
  issues: [],
  repair: null,
  repairs: [],
  points: [],
  timeouts: [],
  subs: [],
  stats: null,
  scoring: { aligned: 37, points: 45, missed: 0, penalties: 0, extra: 0, ...scoring },
  correction,
})

const sheetOf = (patch: Partial<Scoresheet> = {}): Scoresheet => ({
  video_id: ID,
  exists: true,
  can_edit: true,
  version: 1,
  use_in_scoring: true,
  source: 'typed',
  sets: [setDoc(PAPER)],
  entered_by: 'A Coach',
  entered_at: '2026-10-19T20:00:00Z',
  updated_by: 'A Coach',
  updated_at: '2026-10-19T20:00:00Z',
  conflicts: [],
  official_sets: [[25, 20]],
  roster: [{ number: 3, setter: true, libero: false }],
  previous_lineup: null,
  no_scoresheet: false,
  images: null,
  can_add_images: false,
  status: 'entered',
  read: null,
  can_read: false,
  checks: [check(CORRECTION, { departs: 8, disputed: 8 })],
  trusted: true,
  match: null,
  match_by_setter: false,
  ...patch,
})

function show(sheet: Scoresheet, routes: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: readyVideo({ names: NAMES, game_id: 501 }) },
      [`/api/videos/${ID}/scoresheet`]: { body: sheet },
      [`POST /api/videos/${ID}/scoresheet/check`]: { body: { checks: [], trusted: false, match: null, match_by_setter: false } },
      ...routes,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<ScoresheetPage />, { path: '/videos/:id/scoresheet', url: `/videos/${ID}/scoresheet` })
  return fetch
}

const theirBox = (position: string) => screen.getByRole('textbox', { name: new RegExp(`^Volley Näfels box ${position}/1`) })

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

describe('ScoresheetPage: a correction from the board log and the video', () => {
  it('names the boxes, marks them and applies the correction as a new version', async () => {
    const asked: unknown[] = []
    let current = sheetOf()
    show(current, {
      // The page reloads the sheet after the correction: the server has the new version then.
      [`/api/videos/${ID}/scoresheet`]: { body: () => current },
      [`POST /api/videos/${ID}/scoresheet/correct`]: {
        body: ({ body }: { body: unknown }) => {
          asked.push(body)
          const corrections = [
            { side: 'them', position: 2, box: 1, paper: 4, value: 1, by: ['log', 'video'] },
            { side: 'them', position: 3, box: 1, paper: 5, value: 4, by: ['log', 'video'] },
          ]
          current = sheetOf({ version: 2, sets: [setDoc(FIXED, { corrections } as Partial<SheetSetDoc>)], checks: [check(null)] })
          return current
        },
      },
    })
    const card = await screen.findByRole('region', { name: 'The paper and the other records disagree' })
    expect(within(card).getByText('Between 1:1 and 5:5, the board log and the video say Volley Näfels’s box II/1 is 1, not 4, and III/1 is 4, not 5.')).toBeInTheDocument()
    // The boxes the board log and the video say otherwise are marked, with what they say.
    expect(theirBox('II')).toHaveValue('4')
    expect(theirBox('II')).toHaveAttribute('aria-invalid', 'true')
    expect(theirBox('II')).toHaveAccessibleName(/the board log and the video: 1$/)
    expect(theirBox('III')).toHaveAccessibleName(/the board log and the video: 4$/)
    expect(theirBox('IV')).not.toHaveAttribute('aria-invalid')
    // How the score line used the set.
    expect(screen.getByText('The score line leaves the sheet on 8 rallies, where the other records outvote it.')).toBeInTheDocument()

    fireEvent.click(within(card).getByRole('button', { name: 'Apply the correction' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Correct set 1?' })
    expect(within(dialog).getByText(/the board log and the video say; the paper’s version stays/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply the correction' }))

    await waitFor(() => expect(asked).toEqual([{ set: 1, base_version: 1 }]))
    expect(await screen.findByText('Set 1 corrected.')).toBeInTheDocument()
    // The form shows the corrected sheet and says what changed, on whose word.
    await waitFor(() => expect(theirBox('II')).toHaveValue('1'))
    expect(theirBox('III')).toHaveValue('4')
    expect(theirBox('II')).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('region', { name: 'The paper and the other records disagree' })).not.toBeInTheDocument()
    expect(
      screen.getByText('Corrected from the paper on the word of the board log and the video: Volley Näfels II/1 4 → 1, III/1 5 → 4. The paper’s version is in the sheet’s history.'),
    ).toBeInTheDocument()
  })

  it('shows the correction without the button to those who may not change the sheet', async () => {
    show(sheetOf({ can_edit: false }))
    const card = await screen.findByRole('region', { name: 'The paper and the other records disagree' })
    expect(within(card).getByText(/the board log and the video say Volley Näfels’s box II\/1 is 1, not 4/)).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Apply the correction' })).not.toBeInTheDocument()
  })

  it('waits for unsaved changes to be saved or discarded', async () => {
    show(sheetOf())
    const card = await screen.findByRole('region', { name: 'The paper and the other records disagree' })
    fireEvent.change(theirBox('IV'), { target: { value: '7' } })
    expect(within(card).getByRole('button', { name: 'Apply the correction' })).toBeDisabled()
    expect(within(card).getByText('Save or discard your changes first.')).toBeInTheDocument()
    // The marks are for the saved set: not while the form differs from it.
    expect(theirBox('II')).not.toHaveAttribute('aria-invalid')
  })

  it('says when the sheet was changed meanwhile', async () => {
    show(sheetOf(), { [`POST /api/videos/${ID}/scoresheet/correct`]: { status: 409, body: { detail: 'The scoresheet was changed meanwhile: reload it' } } })
    const card = await screen.findByRole('region', { name: 'The paper and the other records disagree' })
    fireEvent.click(within(card).getByRole('button', { name: 'Apply the correction' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Correct set 1?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply the correction' }))
    expect(await screen.findByRole('button', { name: /Reload/ })).toBeInTheDocument()
  })
})

import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { emptySet, type Scoresheet, type SheetIn, type SheetSetDoc } from '../lib/scoresheet'
import type { ReadCell, SheetReading } from '../lib/sheetRead'
import { PLAYERS_VIDEO_ID as ID, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { ScoresheetPage } from './ScoresheetPage'

// Reading the photo (SS4): the page says how many cells to check, marks them, accepts the sure
// ones, takes "it is right" or a correction for each doubtful one, and only then confirms the set.

const NAMES = { us: 'KSCW H3', them: 'Volley Näfels', us_full: 'KSC Wiedikon H3', them_full: 'Volley Näfels', them_from: 'game' as const, us_set: null, them_set: null }

/** Set 1 as the reading proposed it: them first, 3 boxes each so far (synthetic numbers). */
function draftSet(): SheetSetDoc {
  const set = emptySet(1, [7, 3, 11, 5, 9, 1])
  set.first_serve = 'them'
  set.lineup_them = [4, 8, 10, 12, 14, 16]
  set.boxes_them[0] = [2, 13, null, null, null, null, null, null]
  set.boxes_us[1] = [1, 14, null, null, null, null, null, null]
  return set
}

const sure = (v: unknown): ReadCell => ({ v, alts: [[v, 0.99]], check: false, img: 'photo-1', quad: [0.1, 0.1, 0.12, 0.1, 0.12, 0.12, 0.1, 0.12] })
const doubt = (v: unknown, other: unknown): ReadCell => ({ v, alts: [[v, 0.55], [other, 0.4]], check: true, why: 'unsure', img: 'photo-1', quad: [0.2, 0.2, 0.22, 0.2, 0.22, 0.22, 0.2, 0.22] })

function reading(): SheetReading {
  return {
    job: { status: 'succeeded', error: null, created_at: '2026-10-19T12:00:00Z' },
    at: '2026-10-19T12:00:05+02:00',
    worker: 'lenovo-dev',
    model: 'wieplay-sheet-cells v1',
    us_letter: 'B',
    error: null,
    to_check: 2,
    drafts: [1],
    sets: {
      '1': {
        state: 'draft',
        us_half: 'R',
        decoded: true,
        to_check: 2,
        cells: {
          first_serve: { v: 'them', alts: [], check: false },
          'lineup.us.1': sure(7),
          'box.them.1.1': sure(2),
          'box.them.1.2': doubt(13, 18),
          'box.us.2.1': sure(1),
          'box.us.2.2': doubt(14, 11),
        },
      },
    },
  }
}

const sheet = (patch: Partial<Scoresheet> = {}): Scoresheet => ({
  video_id: ID,
  exists: true,
  can_edit: true,
  version: 3,
  use_in_scoring: true,
  source: 'read',
  sets: [draftSet()],
  entered_by: 'Jo Trainer',
  entered_at: '2026-10-19T10:00:00Z',
  updated_by: null,
  updated_at: null,
  conflicts: [],
  official_sets: [[25, 21]],
  roster: [{ number: 3, setter: true, libero: false }],
  previous_lineup: null,
  no_scoresheet: false,
  images: null,
  can_add_images: false,
  status: 'draft',
  read: reading(),
  can_read: true,
  checks: [],
  trusted: false,
  match: null,
  match_by_setter: false,
  ...patch,
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

function show(routes: Record<string, { status?: number; body?: unknown }> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        [`/api/videos/${ID}`]: { body: readyVideo({ names: NAMES, game_id: 501 }) },
        [`/api/videos/${ID}/scoresheet`]: { body: sheet() },
        [`POST /api/videos/${ID}/scoresheet/check`]: { body: { checks: [], trusted: false, match: null, match_by_setter: false } },
        ...routes,
      }),
    ),
  )
  return renderRoute(<ScoresheetPage />, { path: '/videos/:id/scoresheet', url: `/videos/${ID}/scoresheet` })
}

const box = (team: string, position: string, n: number) => screen.getByRole('textbox', { name: new RegExp(`^${team} box ${position}/${n}`) })

describe('a sheet read from the photo', () => {
  it('checks the doubtful cells, accepts the sure ones and confirms the set', async () => {
    const confirmed: (SheetIn & { confirm: number[] })[] = []
    show({
      [`POST /api/videos/${ID}/scoresheet/confirm`]: {
        body: ({ body }: { body: unknown }) => {
          confirmed.push(body as SheetIn & { confirm: number[] })
          return sheet({ version: 4, status: 'entered', read: { ...reading(), drafts: [], sets: { '1': { state: 'confirmed' } } } })
        },
      },
    })
    expect(await screen.findByText('Read from the photo, already used by the scoring; optional: check 2 cells')).toBeInTheDocument()
    expect(screen.getByText('Set 1 was read from the photo: 2 cells to check.')).toBeInTheDocument()
    expect(screen.getByText('On the sheet we are team B.')).toBeInTheDocument()
    // The doubtful cells stand out; the sure ones are marked lightly; a typed one is plain.
    expect(box('Volley Näfels', 'I', 2)).toHaveAttribute('data-read', 'check')
    expect(box('Volley Näfels', 'I', 2)).toHaveAttribute('title', '13 (18?)')
    expect(box('KSCW H3', 'II', 2)).toHaveAttribute('data-read', 'check')
    expect(box('Volley Näfels', 'I', 1)).toHaveAttribute('data-read', 'read')
    expect(box('KSCW H3', 'III', 1)).not.toHaveAttribute('data-read')
    const confirm = screen.getByRole('button', { name: 'Confirm set 1' })
    expect(confirm).toBeDisabled()

    // "Next cell to check" goes to the first doubtful one (ours first); "It is right" accepts it.
    fireEvent.click(screen.getByRole('button', { name: 'Next cell to check' }))
    await waitFor(() => expect(box('KSCW H3', 'II', 2)).toHaveFocus())
    expect(screen.getByText(/Read: 14 \(11\?\) · not sure/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'It is right' }))
    expect(await screen.findByText('Read from the photo, already used by the scoring; optional: check 1 cell')).toBeInTheDocument()
    expect(box('KSCW H3', 'II', 2)).not.toHaveAttribute('data-read')

    // A correction typed in counts as checked.
    fireEvent.change(box('Volley Näfels', 'I', 2), { target: { value: '18' } })
    expect(await screen.findByText('Read from the photo: every doubtful cell is checked. Confirm each set.')).toBeInTheDocument()
    expect(confirm).toBeDisabled() // the sure cells are not looked at yet

    fireEvent.click(screen.getByRole('button', { name: 'Accept the sure cells (3)' }))
    expect(screen.getByText('Set 1: every cell is looked at. Confirm it.')).toBeInTheDocument()
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(confirmed).toHaveLength(1))
    expect(confirmed[0]?.confirm).toEqual([1])
    expect(confirmed[0]?.base_version).toBe(3)
    expect(confirmed[0]?.sets[0]?.boxes_them[0]?.slice(0, 2)).toEqual([2, 18])
    expect(await screen.findByText('Set 1 confirmed.')).toBeInTheDocument()
  }, 20_000)

  it('offers to read the photo, and shows the reading in progress', async () => {
    let posted = 0
    const before = sheet({ sets: [], exists: false, status: 'entered', read: null })
    const queued = sheet({
      sets: [],
      exists: false,
      status: 'entered',
      read: { ...reading(), job: { status: 'queued', error: null, created_at: null }, drafts: [], sets: {}, at: null },
    })
    show({
      [`/api/videos/${ID}/scoresheet`]: { body: () => (posted ? queued : before) },
      [`POST /api/videos/${ID}/scoresheet/read`]: {
        body: () => {
          posted += 1
          return queued
        },
      },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Read the photo' }))
    await waitFor(() => expect(posted).toBe(1))
    expect(await screen.findByText('The photo is waiting to be read on the club’s server…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Read the photo' })).not.toBeInTheDocument()
  })
})

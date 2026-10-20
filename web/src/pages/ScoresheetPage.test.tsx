import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Scoresheet, SheetIn } from '../lib/scoresheet'
import type { SheetPhoto } from '../lib/sheetPhotos'
import { PLAYERS_VIDEO_ID as ID, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { ScoresheetPage } from './ScoresheetPage'

// The canvas is not in jsdom: the redaction's pixels are tested in src/lib/redact.test.ts.
const redactToJpeg = vi.hoisted(() => vi.fn(async () => new Blob(['jpeg'], { type: 'image/jpeg' })))
vi.mock('../lib/redact', async (original) => ({ ...(await original<typeof import('../lib/redact')>()), redactToJpeg }))
// PDF.js draws a scan's pages on the device; here, two pages as pictures.
const pdfPages = vi.hoisted(() =>
  vi.fn(async () => [new File(['p1'], 'scan-p1.jpg', { type: 'image/jpeg' }), new File(['p2'], 'scan-p2.jpg', { type: 'image/jpeg' })]),
)
vi.mock('../lib/pdfPages', async (original) => ({ ...(await original<typeof import('../lib/pdfPages')>()), pdfPages }))

const NAMES = { us: 'KSCW H3', them: 'Volley Näfels', us_full: 'KSC Wiedikon H3', them_full: 'Volley Näfels', them_from: 'game' as const, us_set: null, them_set: null }

const empty = (patch: Partial<Scoresheet> = {}): Scoresheet => ({
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
    [25, 21],
    [25, 20],
  ],
  roster: [{ number: 3, setter: true, libero: false }],
  previous_lineup: [7, 3, 11, 5, 9, 1],
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

// The research note's worked example (docs/research/scoresheets-2026-10.md §3.3): them served
// first, 25:21 for us, won on reception. Rows of the sheet: box 1 of positions I-VI, then box 2.
const THEM = [
  [2, 3, 5, 8, 10, 12],
  [13, 14, 16, 17, 20, 21],
]
const US = [
  [null, 1, 3, 5, 6, 7],
  [11, 14, 15, 17, 19, 24],
  [25, null, null, null, null, null],
]
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI']

function show(sheet: Scoresheet = empty(), routes: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: readyVideo({ names: NAMES, game_id: 501 }) },
      [`/api/videos/${ID}/scoresheet`]: { body: sheet },
      [`POST /api/videos/${ID}/scoresheet/check`]: { body: { checks: [], trusted: false, match: null, match_by_setter: false } },
      ...routes,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<ScoresheetPage />, { path: '/videos/:id/scoresheet', url: `/videos/${ID}/scoresheet` })
  return { ...view, fetch }
}

const box = (team: string, position: number, n: number) => screen.getByRole('textbox', { name: new RegExp(`^${team} box ${ROMAN[position - 1]}/${n}`) })

/** Types rows of a team's boxes; `from`: the box number of the first row. */
function type(team: string, rows: (number | null)[][], from = 1) {
  rows.forEach((row, b) =>
    row.forEach((value, p) => {
      if (value !== null) fireEvent.change(box(team, p + 1, b + from), { target: { value: String(value) } })
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

describe('ScoresheetPage', () => {
  it('decodes the boxes while they are typed and saves the sheet', async () => {
    const saved: SheetIn[] = []
    show(empty(), {
      [`PUT /api/videos/${ID}/scoresheet`]: {
        body: ({ body }: { body: unknown }) => {
          saved.push(body as SheetIn)
          return empty({ exists: true, version: 1, sets: (body as SheetIn).sets, trusted: true })
        },
      },
    })
    // One tab per official set; our six from the last match are filled in.
    expect(await screen.findByRole('tab', { name: /Set 1/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Set 2/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'KSCW H3, position II' })).toHaveValue('3')
    expect(screen.getByText('Choose who served first, then type the boxes.')).toBeInTheDocument()

    const first = screen.getByRole('group', { name: 'First serve' })
    fireEvent.click(within(first).getByRole('button', { name: 'Volley Näfels' }))
    // We received first: our position I box 1 holds the X.
    expect(screen.getByRole('img', { name: 'KSCW H3 I/1: X, received first' })).toBeInTheDocument()

    type('Volley Näfels', THEM.slice(0, 1))
    type('KSCW H3', US.slice(0, 1))
    expect(screen.getByText(/^7:12 after box Volley Näfels VI\/1$/)).toBeInTheDocument()
    type('Volley Näfels', THEM.slice(1), 2)
    type('KSCW H3', US.slice(1), 2)
    expect(await screen.findByText('Every box fits: 25:21, the official result.')).toBeInTheDocument()
    // The points it decodes to, and set 2's first server follows the rule (it alternates).
    expect(screen.getAllByRole('listitem', { name: /^Point \d+:/ })).toHaveLength(46)

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saved).toHaveLength(1))
    const body = saved[0]
    expect(body?.base_version).toBe(0)
    expect(body?.sets[0]).toMatchObject({ no: 1, first_serve: 'them', lineup_us: [7, 3, 11, 5, 9, 1] })
    expect(body?.sets[0]?.boxes_them[3]?.slice(0, 2)).toEqual([8, 17])
    expect(body?.sets[0]?.boxes_us[0]?.slice(0, 3)).toEqual([null, 11, 25])
    expect(body?.sets[1]?.first_serve).toBe('us')
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
  }, 30_000) // ~70 boxes typed into the form: slow in jsdom when the suite runs in parallel

  it('a substitution is OUT, the starting player by shirt number, and IN', async () => {
    show()
    expect(await screen.findByRole('tab', { name: /Set 1/ })).toHaveAttribute('aria-selected', 'true')
    // Our six from the last match are filled in, so OUT names them by number.
    fireEvent.click(screen.getAllByRole('button', { name: 'Add a substitution' })[0] as HTMLElement)
    const out = screen.getByRole('combobox', { name: 'OUT' })
    expect(within(out).getAllByRole('option').map((o) => o.textContent)).toEqual(['#7 (I)', '#3 (II)', '#11 (III)', '#5 (IV)', '#9 (V)', '#1 (VI)'])
    expect(screen.getByRole('textbox', { name: 'IN' })).toHaveValue('')
  })

  it('names the box that does not fit and applies the server’s repair', async () => {
    show(empty(), {
      [`POST /api/videos/${ID}/scoresheet/check`]: {
        body: {
          checks: [
            {
              no: 1, deciding: false, status: 'inconsistent', use: 'hint', wanted: true, final: [17, 17], official: [25, 21], last: null, issues: [],
              repair: { kind: 'change', box: { side: 'them', position: 4, box: 2 }, old: 15, new: 17, cost: 1, final: [25, 21] },
              repairs: [], points: [], timeouts: [], subs: [], stats: null, scoring: null,
            },
          ],
          trusted: false,
          match: null,
          match_by_setter: false,
        },
      },
    }) // prettier-ignore
    fireEvent.click(within(await screen.findByRole('group', { name: 'First serve' })).getByRole('button', { name: 'Volley Näfels' }))
    type('Volley Näfels', [THEM[0] ?? [], [13, 14, 16, 15, 20, 21]])
    type('KSCW H3', US)
    expect(
      screen.getByText('Box Volley Näfels IV/2 reads 15, but Volley Näfels already had 17 points when it won the serve back.'),
    ).toBeInTheDocument()
    expect(box('Volley Näfels', 4, 2)).toHaveAttribute('aria-invalid', 'true')
    // The server's suggestion comes a moment later.
    const apply = await screen.findByRole('button', { name: 'Apply' }, { timeout: 3000 })
    expect(screen.getByText('Suggestion: box Volley Näfels IV/2 reads 15; 17 makes the set fit.')).toBeInTheDocument()
    fireEvent.click(apply)
    expect(box('Volley Näfels', 4, 2)).toHaveValue('17')
    expect(await screen.findByText('Every box fits: 25:21, the official result.')).toBeInTheDocument()
  }, 30_000)

  it('moves on in the sheet’s order: two digits, Space, Enter to the other team, * circles', async () => {
    show()
    fireEvent.click(within(await screen.findByRole('group', { name: 'First serve' })).getByRole('button', { name: 'Volley Näfels' }))
    const i1 = box('Volley Näfels', 1, 1)
    i1.focus()
    fireEvent.change(i1, { target: { value: '12' } }) // two digits: on to the next box
    expect(box('Volley Näfels', 2, 1)).toHaveFocus()
    fireEvent.change(box('Volley Näfels', 2, 1), { target: { value: '3' } })
    fireEvent.keyDown(box('Volley Näfels', 2, 1), { key: ' ' })
    expect(box('Volley Näfels', 3, 1)).toHaveFocus()
    fireEvent.keyDown(box('Volley Näfels', 3, 1), { key: 'Backspace' }) // empty: back
    expect(box('Volley Näfels', 2, 1)).toHaveFocus()
    // Position VI box 1 is followed by position I box 2.
    box('Volley Näfels', 6, 1).focus()
    fireEvent.change(box('Volley Näfels', 6, 1), { target: { value: '9' } })
    fireEvent.keyDown(box('Volley Näfels', 6, 1), { key: 'Enter' })
    expect(box('Volley Näfels', 1, 2)).toHaveFocus()
    // Enter on an empty box: this team is done, the other team's first box (we received: II/1).
    fireEvent.keyDown(box('Volley Näfels', 1, 2), { key: 'Enter' })
    await waitFor(() => expect(box('KSCW H3', 2, 1)).toHaveFocus())
    fireEvent.keyDown(box('KSCW H3', 2, 1), { key: '*' })
    expect(box('KSCW H3', 2, 1)).toHaveAccessibleName('KSCW H3 box II/1, circled')
  })

  it('shows a saved sheet read-only to someone who may not change it, with its numbers', async () => {
    const line = { points: 25, lost: 21, served: 24, break_won: 13, received: 22, side_out_won: 12, side_out_pct: 0.5455, break_point_pct: 0.5417 }
    const theirs = { points: 21, lost: 25, served: 22, break_won: 10, received: 24, side_out_won: 11, side_out_pct: 0.4583, break_point_pct: 0.4545 }
    const stats = {
      us: { team: line, rotations: [{ rotation: 1, server: 7, setter_zone: 2, line }], longest_run: 5 },
      them: { team: theirs, rotations: [{ rotation: 1, server: 4, setter_zone: null, line: theirs }], longest_run: 3 },
    }
    show(
      empty({
        exists: true,
        version: 3,
        can_edit: false,
        entered_by: 'Jo Trainer',
        entered_at: '2026-10-18T20:00:00Z',
        updated_by: 'Jo Trainer',
        updated_at: '2026-10-18T20:00:00Z',
        sets: [{ no: 1, first_serve: 'them' } as Scoresheet['sets'][number]],
        match: stats,
        checks: [
          {
            no: 1, deciding: false, status: 'incomplete', use: null, wanted: true, final: [0, 0], official: [25, 21], last: null, issues: [], repair: null,
            repairs: [], points: [], timeouts: [], subs: [], stats, scoring: null,
          },
        ],
      }),
    ) // prettier-ignore
    expect(await screen.findByText('You can see this scoresheet. Those who tag this match can change it.')).toBeInTheDocument()
    expect(screen.getByText(/Entered by Jo Trainer/)).toBeInTheDocument()
    expect(box('Volley Näfels', 1, 1)).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    const numbers = screen.getByRole('region', { name: 'From the scoresheet' })
    expect(within(numbers).getAllByTitle('12/22')).toHaveLength(2) // the team, its rotation: 55% (12/22)
    expect(within(numbers).getByText('P2')).toBeInTheDocument()
  })

  it('says when someone else saved meanwhile', async () => {
    show(empty(), { [`PUT /api/videos/${ID}/scoresheet`]: { status: 409, body: { detail: 'changed meanwhile' } } })
    fireEvent.click(within(await screen.findByRole('group', { name: 'First serve' })).getByRole('button', { name: 'KSCW H3' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Someone else saved this scoresheet meanwhile.')
  })

  describe('photos of the paper sheet', () => {
    const photo = (patch: Partial<SheetPhoto> = {}): SheetPhoto => ({
      id: '00000000-0000-4000-8000-0000000000f1',
      page: 1,
      set_no: null,
      width: 3200,
      height: 2260,
      bytes: 812_000,
      uploaded_by: 'Jo Trainer',
      uploaded_at: '2026-10-19T08:00:00Z',
      url: `/api/videos/${ID}/scoresheet/images/00000000-0000-4000-8000-0000000000f1`,
      ...patch,
    })

    it('shows the photo of the set being typed beside the form, and removes one', async () => {
      const second = photo({ id: '00000000-0000-4000-8000-0000000000f2', page: 2, set_no: 2, url: `/api/videos/${ID}/scoresheet/images/f2` })
      const { fetch } = show(empty({ images: [photo(), second], can_add_images: true }), {
        [`DELETE /api/videos/${ID}/scoresheet/images/f2`]: { status: 204 },
      })
      const panel = await screen.findByRole('region', { name: 'Photo of the sheet' })
      expect(within(panel).getByRole('img', { name: /^Photo of the scoresheet: Page 1\./ })).toBeInTheDocument()
      expect(within(panel).getByRole('button', { name: 'Page 1' })).toHaveAttribute('aria-pressed', 'true')
      // Set 2 has its own photo: typing set 2 shows it.
      fireEvent.click(screen.getByRole('tab', { name: /Set 2/ }))
      expect(within(panel).getByRole('img', { name: /^Photo of the scoresheet: Set 2\./ })).toBeInTheDocument()
      expect(within(panel).getByText(/Added by Jo Trainer/)).toBeInTheDocument()

      fireEvent.click(within(panel).getByRole('button', { name: 'Remove this photo' }))
      const dialog = await screen.findByRole('alertdialog', { name: 'Remove this photo?' })
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove this photo' }))
      await waitFor(() => expect(fetch).toHaveBeenCalledWith(`/api/videos/${ID}/scoresheet/images/f2`, expect.objectContaining({ method: 'DELETE' })))
      expect(await screen.findByText('Photo removed.')).toBeInTheDocument()
    })

    it('shows no photo to players and helpers', async () => {
      show(empty({ images: null, can_add_images: false }))
      expect(await screen.findByRole('tab', { name: /Set 1/ })).toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'Photo of the sheet' })).not.toBeInTheDocument()
      expect(screen.queryByText('Add a photo')).not.toBeInTheDocument()
    })

    it('uploads only after the dates of birth and licence numbers are blacked out', async () => {
      const posted = `POST /api/videos/${ID}/scoresheet/images?redacted=true&boxes=2&rotation=0&cropped=false`
      let answer: { status: number; body: unknown } = { status: 422, body: { detail: { message: 'meta', code: 'metadata' } } }
      const { fetch } = show(empty({ images: [], can_add_images: true }), { [posted]: { body: () => answer.body } })
      fetch.mockImplementation(
        (async (input: RequestInfo | URL, init?: RequestInit) => {
          const path = String(input)
          if (`${init?.method ?? 'GET'} ${path}` === posted) {
            return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { 'Content-Type': 'application/json' } })
          }
          return stubFetch({
            [`/api/videos/${ID}`]: { body: readyVideo({ names: NAMES, game_id: 501 }) },
            [`/api/videos/${ID}/scoresheet`]: { body: empty({ images: [], can_add_images: true }) },
          })(input, init)
        }) as typeof globalThis.fetch,
      )
      vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:sheet', revokeObjectURL: () => undefined }))

      const prompt = await screen.findByRole('region', { name: 'Photo of the sheet' })
      expect(within(prompt).getByText(/Only the team’s coaches, team managers and admins see the photo/)).toBeInTheDocument()
      const input = prompt.querySelector('input[type="file"]')
      if (!(input instanceof HTMLInputElement)) throw new Error('no file input')
      fireEvent.change(input, { target: { files: [new File(['raw'], 'IMG_0001.jpg', { type: 'image/jpeg' })] } })

      const editor = await screen.findByRole('dialog', { name: 'Black out the dates of birth and licence numbers' })
      const img = within(editor).getByRole('img', { name: 'The photo to black out' })
      Object.defineProperty(img, 'naturalWidth', { value: 4000 })
      Object.defineProperty(img, 'naturalHeight', { value: 2800 })
      fireEvent.load(img)
      // Two boxes are there already, over the team lists and the officials' block (their
      // licence numbers are often dates of birth); the upload waits for the confirmation.
      expect(within(editor).getByRole('button', { name: /^Black box 1/ })).toBeInTheDocument()
      expect(within(editor).getByRole('button', { name: /^Black box 2/ })).toBeInTheDocument()
      const upload = within(editor).getByRole('button', { name: 'Upload' })
      expect(upload).toBeDisabled()
      fireEvent.click(within(editor).getByRole('checkbox', { name: 'The dates of birth and licence numbers are blacked out' }))
      expect(upload).toBeEnabled()

      // The server finds camera data: the editor stays open and says so.
      fireEvent.click(upload)
      expect(await within(editor).findByRole('alert')).toHaveTextContent('The photo still carries the camera’s data')
      expect(redactToJpeg).toHaveBeenCalledWith(
        img,
        expect.objectContaining({ rotation: 0, boxes: [expect.objectContaining({ x: 0.68 }), expect.objectContaining({ x: 0.22 })] }),
      )
      const call = fetch.mock.calls.find(([path]) => String(path).includes('/scoresheet/images?'))
      expect(call?.[1]?.body).toBeInstanceOf(Blob)
      expect(call?.[1]?.headers).toMatchObject({ 'Content-Type': 'image/jpeg' })

      // Turned once, then accepted: the editor closes.
      answer = { status: 201, body: photo() }
      fireEvent.click(within(editor).getByRole('button', { name: 'Turn right' }))
      fireEvent.click(within(editor).getByRole('button', { name: 'Turn left' }))
      fireEvent.click(within(editor).getByRole('button', { name: 'Upload' }))
      expect(await screen.findByText('Photo added.')).toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: 'Black out the dates of birth and licence numbers' })).not.toBeInTheDocument()
    })
    it('takes a PDF scan page by page through the redaction; only the pictures are uploaded', async () => {
      const posted = `POST /api/videos/${ID}/scoresheet/images?redacted=true&boxes=2&rotation=0&cropped=false`
      const uploads: unknown[] = []
      const { fetch } = show(empty({ images: [], can_add_images: true }), {
        [posted]: {
          status: 201,
          body: ({ body }: { body: unknown }) => {
            uploads.push(body)
            return photo({ id: `p${uploads.length}` })
          },
        },
      })
      fetch.mockImplementation(
        (async (input: RequestInfo | URL, init?: RequestInit) => {
          if (`${init?.method ?? 'GET'} ${String(input)}` === posted) {
            uploads.push(init?.body)
            return new Response(JSON.stringify(photo({ id: `p${uploads.length}` })), { status: 201, headers: { 'Content-Type': 'application/json' } })
          }
          return stubFetch({
            [`/api/videos/${ID}`]: { body: readyVideo({ names: NAMES, game_id: 501 }) },
            [`/api/videos/${ID}/scoresheet`]: { body: empty({ images: [], can_add_images: true }) },
          })(input, init)
        }) as typeof globalThis.fetch,
      )
      vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:sheet', revokeObjectURL: () => undefined }))

      const prompt = await screen.findByRole('region', { name: 'Photo of the sheet' })
      const input = prompt.querySelector('input[type="file"]')
      if (!(input instanceof HTMLInputElement)) throw new Error('no file input')
      expect(input.accept).toContain('application/pdf')
      const scan = new File(['%PDF-1.7'], 'scan.pdf', { type: 'application/pdf' })
      fireEvent.change(input, { target: { files: [scan] } })
      expect(pdfPages).toHaveBeenCalledWith(scan)

      for (const page of [1, 2]) {
        const editor = await screen.findByRole('dialog', { name: new RegExp(`^Black out the dates of birth and licence numbers\\W+page ${page} of 2$`) })
        const img = within(editor).getByRole('img', { name: 'The photo to black out' })
        Object.defineProperty(img, 'naturalWidth', { value: 3200 })
        Object.defineProperty(img, 'naturalHeight', { value: 2262 })
        fireEvent.load(img)
        fireEvent.click(within(editor).getByRole('checkbox', { name: 'The dates of birth and licence numbers are blacked out' }))
        fireEvent.click(within(editor).getByRole('button', { name: 'Upload' }))
        await waitFor(() => expect(uploads).toHaveLength(page))
      }
      // Two pictures went up, never the PDF.
      expect(uploads.every((b) => b instanceof Blob && b.type === 'image/jpeg')).toBe(true)
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    })
  })
})

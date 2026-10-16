import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Clip } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { ClipsPage } from './ClipsPage'

const clip = (patch: Partial<Clip>): Clip => ({
  id: '00000000-0000-4000-8000-00000000c101',
  video_id: '00000000-0000-4000-8000-0000000000b1',
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  start_ms: 85_000,
  end_ms: 95_000,
  title: 'My attack',
  note: null,
  event_id: null,
  owner: 107,
  owner_name: 'Luca Brunner',
  mine: true,
  visibility: 'private',
  created_at: '2026-10-13T19:00:00Z',
  ...patch,
})

describe('ClipsPage', () => {
  it('lists my clips newest first; each opens the video at the clip', async () => {
    const older = clip({ id: '00000000-0000-4000-8000-00000000c102', title: 'Serve', created_at: '2026-10-01T19:00:00Z', video_title: 'KSCW H3 – VBC Züri', recorded_on: '2026-10-04', visibility: 'team' })
    let mine = [older, clip({})]
    const fetch = vi.fn(
      stubFetch({
        '/api/clips/mine': { body: () => mine },
        [`DELETE /api/clips/${older.id}`]: {
          status: 204,
          body: () => {
            mine = mine.filter((c) => c.id !== older.id)
          },
        },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(<ClipsPage />, { path: '/clips', url: '/clips' })

    const table = await screen.findByRole('table', { name: 'My clips' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('link')[0]?.textContent)).toEqual(['My attack', 'Serve'])
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'My attack' })).toHaveAttribute(
      'href',
      '/videos/00000000-0000-4000-8000-0000000000b1?clip=00000000-0000-4000-8000-00000000c101',
    )
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'Watch My attack' })).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('Team')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('KSCW H3 – VBC Züri · 04.10.2026 · 0:10')).toBeInTheDocument()

    fireEvent.click(within(rows[1] as HTMLElement).getByRole('button', { name: 'Delete Serve' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete clip' }))
    await waitFor(() => expect(within(table).queryByText('Serve')).not.toBeInTheDocument())
  })

  it('says how to make a clip when there is none', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/clips/mine': { body: [] } })))
    renderRoute(<ClipsPage />)
    expect(await screen.findByText(/On a video, mark a moment/)).toBeInTheDocument()
  })
})

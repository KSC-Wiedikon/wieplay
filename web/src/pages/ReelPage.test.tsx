import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Piece, Reel } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { ReelPage } from './ReelPage'
import { ReelsPage } from './ReelsPage'

const reel = (patch: Partial<Reel>): Reel => ({
  id: '00000000-0000-4000-8000-00000000e001',
  title: 'H3 kills',
  kind: 'smart',
  query: { codes: ['A'], grades: ['#'] },
  visibility: 'team',
  lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
  owner: 7,
  mine: false,
  created_at: '2026-10-12T20:00:00Z',
  updated_at: '2026-10-12T20:00:00Z',
  ...patch,
})

const piece = (patch: Partial<Piece>): Piece => ({
  video_id: '00000000-0000-4000-8000-0000000000b1',
  start_ms: 15_000,
  end_ms: 20_000,
  event_id: null,
  clip_id: null,
  caption: null,
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  code: 'A',
  grade: '#',
  side: 'us',
  number: 7,
  member_id: 107,
  player_name: 'Luca Brunner',
  score_us: 1,
  score_them: 0,
  ...patch,
})

beforeEach(() => {
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('ReelsPage', () => {
  it('lists my reels and the team’s; only mine can be edited or deleted', async () => {
    let reels = [reel({ id: '00000000-0000-4000-8000-00000000e002', title: 'Approach work', kind: 'manual', visibility: 'private', mine: true, owner: 107 }), reel({})]
    const fetch = vi.fn(
      stubFetch({
        '/api/reels': { body: () => reels },
        'DELETE /api/reels/00000000-0000-4000-8000-00000000e002': {
          status: 204,
          body: () => {
            reels = reels.slice(1)
          },
        },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(<ReelsPage />)
    const mine = await screen.findByRole('table', { name: 'My reels' })
    const team = screen.getByRole('table', { name: 'Team reels' })
    expect(within(mine).getByRole('link', { name: 'Approach work' })).toHaveAttribute('href', '/reels/00000000-0000-4000-8000-00000000e002')
    expect(within(mine).getByRole('link', { name: 'Edit Approach work' })).toHaveAttribute('href', '/reels/00000000-0000-4000-8000-00000000e002/edit')
    expect(within(team).getByRole('link', { name: 'H3 kills' })).toBeInTheDocument()
    expect(within(team).queryByRole('button', { name: 'Delete H3 kills' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New highlight reel' })).toHaveAttribute('href', '/reels/new')
    expect(screen.getByRole('link', { name: 'New reel from clips' })).toHaveAttribute('href', '/reels/new?kind=manual')

    fireEvent.click(within(mine).getByRole('button', { name: 'Delete Approach work' }))
    fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Delete this reel?' })).getByRole('button', { name: 'Delete reel' }))
    expect(await screen.findByText('No reels yet.')).toBeInTheDocument()
  })
})

describe('ReelPage', () => {
  function show(r: Reel, items: Piece[], exportStatus = 403) {
    const fetch = vi.fn(
      stubFetch({
        [`/api/reels/${r.id}`]: { body: { reel: r, item_count: items.length, total_ms: items.reduce((sum, p) => sum + p.end_ms - p.start_ms, 0) } },
        [`/api/reels/${r.id}/items`]: { body: items },
        'POST /api/exports': { status: exportStatus, body: { detail: 'Only coaches and sport admins download videos' } },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(<ReelPage />, { path: '/reels/:id', url: `/reels/${r.id}` })
    return fetch
  }

  it('plays the reel’s moments and says when a download is not allowed', async () => {
    window.localStorage.setItem('nomal-export-rules', '1')
    const fetch = show(reel({}), [
      piece({}),
      piece({ video_id: '00000000-0000-4000-8000-0000000000b3', start_ms: 1_000, end_ms: 4_000, caption: 'Luca from zone 4', video_title: 'KSCW H3 – VBC Züri', recorded_on: '2026-10-04', score_us: 24, score_them: 22 }),
    ])
    expect(await screen.findByRole('heading', { name: 'H3 kills' })).toBeInTheDocument()
    expect(await screen.findByText('2 moments · 0:08')).toBeInTheDocument()
    const moments = screen.getByRole('table', { name: 'Moments' })
    const rows = within(moments).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getByRole('button').textContent)).toEqual(['#7 Luca Brunner · A · #', 'Luca from zone 4'])
    // Under each: the player when the label doesn't name them, and the score after the rally.
    expect(within(rows[0] as HTMLElement).getByText('1:0')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText(/^Luca Brunner · 24:22/)).toBeInTheDocument()
    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    // Not mine: no edit, no delete.
    expect(screen.queryByRole('link', { name: 'Edit' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download as MP4' }))
    expect(await screen.findByText('Only coaches and sport admins download videos.')).toBeInTheDocument()
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/exports', expect.objectContaining({ method: 'POST', body: JSON.stringify({ kind: 'reel', reel_id: '00000000-0000-4000-8000-00000000e001' }) })),
    )
  })

  it('says when there is nothing to play, and when the reel is gone', async () => {
    show(reel({ mine: true }), [])
    expect(await screen.findByText(/Nothing to play/)).toBeInTheDocument()
    expect(screen.getByText('0 moments · 0:00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Edit' })).toHaveAttribute('href', '/reels/00000000-0000-4000-8000-00000000e001/edit')
    expect(screen.queryByRole('button', { name: 'Download as MP4' })).not.toBeInTheDocument()
  })

  it('says a reel that is gone or not mine to see is not there', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({})))
    renderRoute(<ReelPage />, { path: '/reels/:id', url: '/reels/00000000-0000-4000-8000-00000000e009' })
    expect(await screen.findByText("This reel doesn't exist or you don't have access to it.")).toBeInTheDocument()
  })
})

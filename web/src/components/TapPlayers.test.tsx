import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../i18n'
import '../i18n/pages'
import { ToastProvider } from './ToastProvider'
import { stubFetch } from '../test/render'
import { TapLayer } from './TapPlayers'

const ID = '00000000-0000-4000-8000-0000000000ab'
const roster = [{ side: 'us' as const, number: 7, member_id: 101, position: null, is_libero: false, name: 'Alex Muster' }]
const box = (track: number, team: 'us' | 'them', number: number | null) => ({
  track,
  box: [0.1, 0.4, 0.15, 0.8] as [number, number, number, number],
  card: `c${track}`,
  key: track,
  team,
  number,
  ocr_number: null,
  not_player: false,
  sure: number !== null,
})

afterEach(() => vi.unstubAllGlobals())

describe('tapping a player in the paused video', () => {
  it('outlines the people of that moment and names the one tapped', async () => {
    const fetch = vi.fn(
      stubFetch({
        [`/api/videos/${ID}/players/frame?t_ms=12000`]: { body: { t_ms: 12_000, boxes: [box(2, 'us', null), box(3, 'them', 4)] } },
        [`POST /api/videos/${ID}/players/tap`]: { body: { players: {}, queue: { items: [], named: 1, sure_share: 0.5, teams: null }, undo: [{ id: 'c2', number: null }], relinked: [] } },
        [`POST /api/videos/${ID}/players/restore`]: { body: { players: {}, queue: { items: [], named: 0, sure_share: 0.4, teams: null }, undo: [], relinked: [] } },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    const video = document.createElement('video')
    video.currentTime = 12
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <TapLayer videoId={ID} video={video} roster={roster} />
        </ToastProvider>
      </QueryClientProvider>,
    )
    const layer = screen.getByTestId('tap-layer')
    const people = await within(layer).findAllByRole('button')
    expect(people).toHaveLength(2)
    expect(within(layer).getByText('#4')).toBeInTheDocument()
    fireEvent.click(people[0] as HTMLElement)
    const dialog = screen.getByRole('dialog', { name: 'Who is this?' })
    fireEvent.click(within(dialog).getByRole('button', { name: '#7' }))
    await waitFor(() => {
      const call = fetch.mock.calls.find(([url, init]) => url === `/api/videos/${ID}/players/tap` && (init as RequestInit).method === 'POST')
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ t_ms: 12_000, track: 2, number: 7, not_player: false })
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === `/api/videos/${ID}/players/restore`)).toBe(true))
  })
})

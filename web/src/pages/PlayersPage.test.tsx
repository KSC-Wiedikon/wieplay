import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Players } from '../lib/players'
import { cluster, PLAYERS_VIDEO_ID as ID, playersState, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { PlayersPage } from './PlayersPage'

function show(state: Players, extra: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: readyVideo() },
      [`/api/videos/${ID}/players`]: { body: state },
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<PlayersPage />, { path: '/videos/:id/players', url: `/videos/${ID}/players` })
  return fetch
}

const calls = (fetch: ReturnType<typeof vi.fn>, method: string, path: string) =>
  fetch.mock.calls
    .filter(([url, init]) => url === path && ((init as RequestInit | undefined)?.method ?? 'GET') === method)
    .map(([, init]) => JSON.parse(((init as RequestInit).body as string | undefined) ?? 'null') as unknown)

describe('PlayersPage', () => {
  it('shows the cards by team with their pictures, readings and how far the actions are identified', async () => {
    show(playersState())
    expect(await screen.findByRole('heading', { name: /Players in this video/ })).toHaveTextContent('beta')
    expect(screen.getByText('25% of actions identified')).toBeInTheDocument()
    expect(screen.getByText('3 cards: 2 ours, 1 theirs')).toBeInTheDocument()
    expect(screen.getByText(/0 of 3 cards named/)).toBeInTheDocument()
    expect(screen.getByText(/WiePlay agreed 75%/)).toBeInTheDocument()
    const ours = screen.getByRole('region', { name: 'Our team' })
    expect(within(ours).getAllByRole('article')).toHaveLength(2)
    const first = within(ours).getByRole('article', { name: 'Player 1' })
    expect(within(first).getByText('Reads #7')).toBeInTheDocument()
    const pictures = within(first).getByRole('img', { name: 'Pictures of Player 1' })
    const picture = pictures.firstElementChild as HTMLElement
    expect(picture.style.backgroundImage).toContain(`/api/videos/${ID}/players/sheets/0?v=run1`)
    expect(picture.style.backgroundPosition).toBe('-60px 0px')
    expect(within(screen.getByRole('region', { name: 'Opponent' })).getAllByRole('article')).toHaveLength(1)
    expect(screen.getByText('Not players (1)')).toBeInTheDocument()
  })

  it('names a card from the keyboard and moves on to the next one', async () => {
    const named = playersState({ clusters: [cluster({ key: 1, number: 7, name: 'Alex Muster', member_id: 101, seconds: 300 }), cluster({ key: 2, seconds: 200 })] })
    const fetch = show(playersState({ clusters: [cluster({ key: 1, seconds: 300 }), cluster({ key: 2, seconds: 200 })] }), {
      [`PATCH /api/videos/${ID}/players/clusters/c1`]: { body: named },
    })
    const field = await screen.findByLabelText('Shirt number of Player 1')
    fireEvent.change(field, { target: { value: '42' } })
    fireEvent.submit(field)
    expect(await screen.findByText(/#42 is not in this video’s roster/)).toBeInTheDocument()
    expect(field).toHaveAttribute('aria-invalid', 'true')
    fireEvent.change(field, { target: { value: '7' } })
    fireEvent.submit(field)
    expect(await screen.findByRole('heading', { name: '#7 Alex Muster' })).toBeInTheDocument()
    expect(calls(fetch, 'PATCH', `/api/videos/${ID}/players/clusters/c1`)).toEqual([{ number: 7, not_player: false }])
    await waitFor(() => expect(screen.getByLabelText('Shirt number of Player 2')).toHaveFocus())
  })

  it('takes the number read on the shirt, and marks a card as not a player', async () => {
    const fetch = show(playersState(), {
      [`PATCH /api/videos/${ID}/players/clusters/c1`]: { body: playersState() },
      [`PATCH /api/videos/${ID}/players/clusters/c3`]: { body: playersState() },
    })
    const card = await screen.findByRole('article', { name: 'Player 1' })
    fireEvent.click(within(card).getByRole('button', { name: 'Use #7' }))
    await waitFor(() => expect(calls(fetch, 'PATCH', `/api/videos/${ID}/players/clusters/c1`)).toEqual([{ number: 7, not_player: false }]))
    const theirs = screen.getByRole('article', { name: 'Player 3' })
    fireEvent.click(within(theirs).getByRole('button', { name: 'Not a player' }))
    await waitFor(() => expect(calls(fetch, 'PATCH', `/api/videos/${ID}/players/clusters/c3`)).toEqual([{ number: null, not_player: true }]))
  })

  it('merges selected cards of one team into the first one', async () => {
    const fetch = show(playersState(), { [`POST /api/videos/${ID}/players/merge`]: { body: playersState() } })
    fireEvent.click(await screen.findByLabelText('Select Player 1'))
    expect(screen.getByText('1 card selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Merge' })).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Select Player 3'))
    expect(screen.getByText('Only cards of one team can be merged.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Merge' })).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Select Player 3'))
    fireEvent.click(screen.getByLabelText('Select Player 2'))
    fireEvent.click(screen.getByRole('button', { name: 'Merge' }))
    await waitFor(() => expect(calls(fetch, 'POST', `/api/videos/${ID}/players/merge`)).toEqual([{ into: 'c1', sources: ['c2'] }]))
    await waitFor(() => expect(screen.queryByText(/cards? selected/)).not.toBeInTheDocument())
  })

  it('splits the stretches that show someone else into a new card', async () => {
    const fetch = show(playersState(), { [`POST /api/videos/${ID}/players/clusters/c2/split`]: { body: playersState() } })
    const card = await screen.findByRole('article', { name: 'Player 2' })
    fireEvent.click(within(card).getByRole('button', { name: 'Split' }))
    const dialog = await screen.findByRole('dialog', { name: 'Split Player 2' })
    const confirm = within(dialog).getByRole('button', { name: /to a new card/ })
    expect(confirm).toBeDisabled()
    const stretch = within(dialog).getByRole('button', { name: '1:10–1:40, 300 detections' })
    fireEvent.click(stretch)
    expect(stretch).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move 1 picture to a new card' }))
    await waitFor(() => expect(calls(fetch, 'POST', `/api/videos/${ID}/players/clusters/c2/split`)).toEqual([{ tracks: [21] }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('swaps the teams after asking, and applies WiePlay’s players to the coach’s tags', async () => {
    const fetch = show(playersState(), {
      [`POST /api/videos/${ID}/players/swap-teams`]: { body: playersState() },
      [`POST /api/videos/${ID}/players/apply`]: { body: { applied: 2, players: playersState({ open_suggestions: 0 }) } },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Swap teams' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Swap the teams?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Swap teams' }))
    await waitFor(() => expect(calls(fetch, 'POST', `/api/videos/${ID}/players/swap-teams`)).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Use WiePlay’s players for 2 of your tags' }))
    expect(await screen.findByText('2 tags got their player')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: /Use WiePlay’s players/ })).not.toBeInTheDocument())
  })

  it('is read-only for those who may not tag', async () => {
    show(playersState({ can_edit: false }))
    await screen.findByRole('article', { name: 'Player 1' })
    expect(screen.queryByLabelText(/Shirt number of/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Swap teams' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use #7' })).not.toBeInTheDocument()
  })

  it('shows the search while it runs and offers it when there is none', async () => {
    show(
      playersState({
        status: 'running',
        run: null,
        sheets: null,
        clusters: [],
        attribution: null,
        job: { id: 'j', status: 'running', progress: 0.4, message: 'finding the players', error: null },
      }),
    )
    expect(await screen.findByText('Finding the players…')).toBeInTheDocument()
    expect(screen.getByText('finding the players')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Find players/ })).not.toBeInTheDocument()
  })

  it('finds the players on demand when nobody has (beta, about 2 h)', async () => {
    const none = playersState({ status: 'none', run: null, sheets: null, clusters: [], attribution: null, open_suggestions: 0 })
    const fetch = show(none, {
      [`POST /api/videos/${ID}/players/find`]: { status: 202, body: { ...none, status: 'queued' } },
    })
    const button = await screen.findByRole('button', { name: 'Find players (beta, takes ~2 h)' })
    expect(screen.getByText(/about 2 hours of the club server’s time/)).toBeInTheDocument()
    fireEvent.click(button)
    await waitFor(() => expect(calls(fetch, 'POST', `/api/videos/${ID}/players/find`)).toHaveLength(1))
    expect(await screen.findByText('Waiting to find the players…')).toBeInTheDocument()
  })
})

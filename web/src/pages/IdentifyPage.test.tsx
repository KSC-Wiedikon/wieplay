import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Queue } from '../lib/identify'
import type { Players } from '../lib/players'
import { cluster, PLAYERS_VIDEO_ID as ID, playersState, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { IdentifyPage } from './IdentifyPage'

const players = (): Players =>
  playersState({
    clusters: [
      cluster({ key: 1, seconds: 300, confidence: 0.3, uncertain: true }),
      cluster({ key: 2, seconds: 200, confidence: 0.3, uncertain: true, ocr_number: 7, ocr_confidence: 0.8 }),
      cluster({ key: 3, team: 'them', seconds: 100, confidence: 0.9 }),
    ],
  })

const queue = (items = ['c1', 'c2'], teams: Queue['teams'] = null): Queue => ({
  items: items.map((card) => ({ card, on_court: [11], same_as: card === 'c1' ? ['c2'] : [] })),
  named: 0,
  sure_share: 0.4,
  teams,
})

function show(extra: Record<string, { status?: number; body?: unknown }> = {}, initial = queue()) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: readyVideo() },
      [`/api/videos/${ID}/players`]: { body: players() },
      [`/api/videos/${ID}/players/queue`]: { body: initial },
      [`/api/videos/${ID}/players/clusters/c1/moments`]: { body: [] },
      [`/api/videos/${ID}/players/clusters/c2/moments`]: { body: [] },
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<IdentifyPage />, { path: '/videos/:id/identify', url: `/videos/${ID}/identify` })
  return fetch
}

const posted = (fetch: ReturnType<typeof vi.fn>, path: string) =>
  fetch.mock.calls
    .filter(([url, init]) => url === path && (init as RequestInit | undefined)?.method === 'POST')
    .map(([, init]) => JSON.parse((init as RequestInit).body as string) as unknown)

const answered = (items: string[]) => ({ players: players(), queue: queue(items), undo: [{ id: 'c1', number: null }], relinked: [] })

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

afterEach(() => vi.unstubAllGlobals())

describe('IdentifyPage', () => {
  it('asks about the biggest unsure card first, the numbers on court first', async () => {
    show()
    expect(await screen.findByRole('heading', { name: /Player 1/ })).toBeInTheDocument()
    expect(screen.getByText('0 of 2')).toBeInTheDocument()
    expect(screen.getByText('40% of the players’ footage is on sure cards')).toBeInTheDocument()
    const answers = screen.getByRole('region', { name: 'Answer' })
    const numbers = within(answers).getAllByRole('button', { name: /^#\d+/ })
    expect(numbers.map((b) => b.textContent)).toEqual(['#11', '#3Sam Beispiel', '#7Alex Muster'])
  })

  it('a tapped number is an answer; Undo takes it back', async () => {
    const fetch = show({
      [`POST /api/videos/${ID}/players/clusters/c1/answer`]: { body: answered(['c2']) },
      [`POST /api/videos/${ID}/players/restore`]: { body: answered(['c1', 'c2']) },
    })
    fireEvent.click(await screen.findByRole('button', { name: /^#7/ }))
    await waitFor(() => expect(posted(fetch, `/api/videos/${ID}/players/clusters/c1/answer`)).toEqual([{ kind: 'number', number: 7 }]))
    expect(await screen.findByText('1 of 2')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: /Player 2/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    await waitFor(() => expect(posted(fetch, `/api/videos/${ID}/players/restore`)).toEqual([{ cards: [{ id: 'c1', number: null }] }]))
  })

  it('answers from the keyboard: not a player, the other team, skip', async () => {
    const fetch = show({ [`POST /api/videos/${ID}/players/clusters/c1/answer`]: { body: answered(['c1', 'c2']) } })
    await screen.findByRole('heading', { name: /Player 1/ })
    fireEvent.keyDown(window, { key: 'n' })
    await waitFor(() => expect(posted(fetch, `/api/videos/${ID}/players/clusters/c1/answer`)).toEqual([{ kind: 'not_player' }]))
    fireEvent.keyDown(window, { key: 't' })
    await waitFor(() => expect(posted(fetch, `/api/videos/${ID}/players/clusters/c1/answer`)).toHaveLength(2))
    expect(posted(fetch, `/api/videos/${ID}/players/clusters/c1/answer`)[1]).toEqual({ kind: 'team', team: 'them' })
    fireEvent.keyDown(window, { key: 'k' })
    expect(await screen.findByRole('heading', { name: /Player 2/ })).toBeInTheDocument()
  })

  it('names some pieces of a card with several people', async () => {
    const fetch = show({ [`POST /api/videos/${ID}/players/clusters/c1/answer`]: { body: answered(['c2']) } })
    fireEvent.click(await screen.findByRole('button', { name: /Two people/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getAllByRole('button', { pressed: false })[0] as HTMLElement)
    fireEvent.click(within(dialog).getByRole('button', { name: '#11' }))
    await waitFor(() =>
      expect(posted(fetch, `/api/videos/${ID}/players/clusters/c1/answer`)).toEqual([{ kind: 'pieces', tracks: [10], number: 11, not_player: false }]),
    )
  })

  it('asks which team is ours when the teams are uncertain', async () => {
    const teams = { set: 1, near: [{ card: 'c1', crops: [[0, 1]] as [number, number][], team: 'us' as const }], far: [{ card: 'c3', crops: [[0, 3]] as [number, number][], team: 'them' as const }], near_team: 'us' as const, far_team: 'them' as const }
    const fetch = show({ [`POST /api/videos/${ID}/players/teams`]: { body: answered(['c1', 'c2']) } }, queue(['c1', 'c2'], teams))
    const card = await screen.findByRole('region', { name: 'Which team is ours?' })
    fireEvent.click(within(card).getAllByRole('button', { name: 'These are ours' })[1] as HTMLElement)
    await waitFor(() => expect(posted(fetch, `/api/videos/${ID}/players/teams`)).toEqual([{ near_is_ours: false }]))
  })
})

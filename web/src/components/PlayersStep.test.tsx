import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PlayersSummary } from '../lib/players'
import { PLAYERS_VIDEO_ID as ID, readyVideo } from '../test/players'
import { renderRoute, stubFetch } from '../test/render'
import { PlayersStep } from './PlayersStep'

const summary = (patch: Partial<PlayersSummary> = {}): PlayersSummary => ({
  status: 'done',
  run_id: 'run1',
  clusters: 14,
  named: 3,
  clusters_us: 8,
  clusters_them: 6,
  numbers_read: 5,
  actions: 120,
  identified: 30,
  identified_share: 0.25,
  suggested: 20,
  compared: 0,
  accuracy: null,
  ...patch,
})

const analysis = (players: PlayersSummary | null | undefined) => ({
  status: 'done',
  model_run: null,
  proposals: { unreviewed: 0, confirmed: 0, corrected: 0, rejected: 0, merged: 0 },
  metrics_vs_human: null,
  players,
})

function show(players: PlayersSummary | null | undefined, canEdit = true, extra: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(stubFetch({ [`/api/videos/${ID}/analysis`]: { body: analysis(players) }, ...extra }))
  vi.stubGlobal('fetch', fetch)
  renderRoute(<PlayersStep video={readyVideo()} canEdit={canEdit} />)
  return fetch
}

describe('PlayersStep', () => {
  it('says how many players were found and named, and leads to the gallery', async () => {
    show(summary())
    expect(await screen.findByRole('region', { name: 'Players in this video' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Players in this video/ })).toHaveTextContent('beta')
    expect(screen.getByText('14 cards (8 ours, 6 theirs) · 3 named')).toBeInTheDocument()
    expect(screen.getByText('25% of actions with a player')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Name the players' })).toHaveAttribute('href', `/videos/${ID}/players`)
  })

  it('shows the search while it runs', async () => {
    show(summary({ status: 'running', run_id: null }))
    expect(await screen.findByText('Finding the players… (about 2 hours)')).toBeInTheDocument() // older servers: no minutes, 2 h
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers to find the players when nobody has, only to those who may tag', async () => {
    const none = summary({ status: 'none', run_id: null, clusters: 0, named: 0, clusters_us: 0, clusters_them: 0, actions: 0, identified: 0, identified_share: null })
    const fetch = show({ ...none, find_minutes: 20 }, true, { [`POST /api/videos/${ID}/players/find`]: { status: 202, body: {} } })
    expect(await screen.findByText(/takes about 20 min per match/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Find players (beta, takes about 20 min)' }))
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url, init]) => url === `/api/videos/${ID}/players/find` && (init as RequestInit | undefined)?.method === 'POST')).toBe(true),
    )
  })

  it('shows no find button to those who may not tag', async () => {
    show(summary({ status: 'none', run_id: null, clusters: 0 }), false)
    expect(await screen.findByText(/takes about 2 hours per match/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('counts the cards WiePlay is unsure of', async () => {
    show(summary({ uncertain: 3 }))
    expect(await screen.findByText('3 cards to check')).toBeInTheDocument()
  })

  it('renders nothing without players in the analysis', async () => {
    show(undefined)
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('region', { name: 'Players in this video' })).not.toBeInTheDocument()
  })
})

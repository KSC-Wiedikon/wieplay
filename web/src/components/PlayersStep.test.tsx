import { screen } from '@testing-library/react'
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

function show(players: PlayersSummary | null | undefined, canEdit = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        [`/api/videos/${ID}/analysis`]: {
          body: { status: 'done', model_run: null, proposals: { unreviewed: 0, confirmed: 0, corrected: 0, rejected: 0, merged: 0 }, metrics_vs_human: null, players },
        },
      }),
    ),
  )
  return renderRoute(<PlayersStep video={readyVideo()} canEdit={canEdit} />)
}

describe('PlayersStep', () => {
  it('says how many players were found and named, and leads to the gallery', async () => {
    show(summary())
    expect(await screen.findByRole('region', { name: 'Players in this video' })).toBeInTheDocument()
    expect(screen.getByText('8 of ours, 6 of theirs found · 3 named')).toBeInTheDocument()
    expect(screen.getByText('25% of actions with a player')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Name the players' })).toHaveAttribute('href', `/videos/${ID}/players`)
  })

  it('shows the search while it runs', async () => {
    show(summary({ status: 'running', run_id: null }))
    expect(await screen.findByText('Finding the players…')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders nothing without players in the analysis', async () => {
    show(undefined)
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('region', { name: 'Players in this video' })).not.toBeInTheDocument()
  })
})

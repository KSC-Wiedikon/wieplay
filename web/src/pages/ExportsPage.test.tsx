import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { VideoExport } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { ExportsPage } from './ExportsPage'

const item = (patch: Partial<VideoExport>): VideoExport => ({
  id: '00000000-0000-4000-8000-00000000f001',
  kind: 'clip',
  title: 'Kill by Luca',
  duration_ms: 7_000,
  status: 'ready',
  bytes: 48_300_000,
  progress: null,
  error: null,
  created_at: '2026-10-15T18:00:00Z',
  expires_at: '2099-11-14T18:00:00Z',
  ...patch,
})

function show(list: VideoExport[]) {
  const fetch = vi.fn(stubFetch({ '/api/exports': { body: list }, [`DELETE /api/exports/${list[0]?.id}`]: { status: 204 } }))
  vi.stubGlobal('fetch', fetch)
  renderRoute(<ExportsPage />)
  return fetch
}

describe('ExportsPage', () => {
  it('explains the rules and shows each state', async () => {
    show([
      item({}),
      item({ id: 'q', title: 'H3 kills', kind: 'reel', status: 'queued', bytes: null, progress: 0.42, expires_at: null }),
      item({ id: 'f', title: 'KSCW H3 – Volley Näfels (condensed)', kind: 'condensed', duration_ms: 2_040_000, status: 'failed', bytes: null, error: 'ffmpeg exited with status 1', expires_at: null }),
      item({ id: 'e', title: 'Old reel', status: 'expired', expires_at: '2026-09-01T00:00:00Z' }),
      // Past its date, even if the sweep hasn't run yet.
      item({ id: 'late', title: 'Late one', expires_at: '2026-01-01T00:00:00Z' }),
    ])
    expect(screen.getByText(/never post it publicly/)).toBeInTheDocument()
    const rows = within(await screen.findByRole('table', { name: 'Downloads' })).getAllByRole('row').slice(1)

    const ready = rows[0] as HTMLElement
    expect(within(ready).getByText('Ready')).toBeInTheDocument()
    expect(within(ready).getByText('Clip · 0:07')).toBeInTheDocument()
    expect(within(ready).getAllByText(/48.3 MB/).length).toBeGreaterThan(0)
    expect(within(ready).getAllByText(/14\.11\.2099/).length).toBeGreaterThan(0)
    // A plain link: the API redirects to a short-lived signed download.
    expect(within(ready).getByRole('link', { name: 'Download Kill by Luca' })).toHaveAttribute('href', '/api/exports/00000000-0000-4000-8000-00000000f001/download')

    const queued = rows[1] as HTMLElement
    expect(within(queued).getByText('Preparing')).toBeInTheDocument()
    expect(within(queued).getByRole('progressbar', { name: 'Preparing' })).toHaveAttribute('aria-valuenow', '42')
    expect(within(queued).queryByRole('link')).not.toBeInTheDocument()

    const failed = rows[2] as HTMLElement
    expect(within(failed).getByText('Failed')).toBeInTheDocument()
    expect(within(failed).getByText('ffmpeg exited with status 1')).toBeInTheDocument()
    expect(within(failed).getByText('Rallies of a match · 34:00')).toBeInTheDocument()

    for (const row of rows.slice(3) as HTMLElement[]) {
      expect(within(row).getByText('Expired')).toBeInTheDocument()
      expect(within(row).queryByRole('link')).not.toBeInTheDocument()
    }
  })

  it('deletes a download after confirming', async () => {
    const fetch = show([item({})])
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Kill by Luca' }))
    fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Delete this download?' })).getByRole('button', { name: 'Delete download' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/exports/00000000-0000-4000-8000-00000000f001', expect.objectContaining({ method: 'DELETE' })))
    expect(await screen.findByText('Download deleted.')).toBeInTheDocument()
  })

  it('says how to get one when there is none', async () => {
    show([])
    expect(await screen.findByText(/No downloads yet/)).toBeInTheDocument()
  })
})

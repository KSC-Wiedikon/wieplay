import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EXPORT_QUEUED } from '../lib/library'
import { writeStorage } from '../lib/storage'
import { renderRoute, stubFetch } from '../test/render'
import { ExportButton } from './ExportButton'

afterEach(() => vi.unstubAllGlobals())

describe('ExportButton for a game recap', () => {
  it('asks for a recap in the viewer’s language and says it is being cut', async () => {
    writeStorage('nomal-export-rules', '1')
    const posted: unknown[] = []
    const queued = vi.fn()
    window.addEventListener(EXPORT_QUEUED, queued)
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          'POST /api/exports': {
            status: 201,
            body: ({ body }: { body: unknown }) => {
              posted.push(body)
              return { id: 'e1', kind: 'recap', title: 'x', duration_ms: 1, status: 'queued', bytes: null, progress: null, error: null, created_at: '', expires_at: null }
            },
          },
          '/api/exports': { body: [] },
        }),
      ),
    )
    renderRoute(<ExportButton request={{ kind: 'recap', video_id: 'v1', lang: 'de' }} text="Create game recap" queuedText="Cutting the game recap." />)
    fireEvent.click(screen.getByRole('button', { name: 'Create game recap' }))
    expect(await screen.findByText('Cutting the game recap.')).toBeInTheDocument()
    expect(posted).toEqual([{ kind: 'recap', video_id: 'v1', lang: 'de' }])
    await waitFor(() => expect(queued).toHaveBeenCalled())
    window.removeEventListener(EXPORT_QUEUED, queued)
  })
})

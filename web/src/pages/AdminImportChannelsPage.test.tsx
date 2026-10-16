import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ImportChannel, Me } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { AdminImportChannelsPage } from './AdminImportChannelsPage'

const me = (isAdmin = true): Me => ({
  member: { id: 1, uuid: '00000000-0000-4000-8000-000000000101', name: 'Luca' },
  roles: [],
  is_admin: isAdmin,
  sport_admin: [],
  teams: [],
})

const luca: ImportChannel = {
  id: '00000000-0000-4000-8000-0000000000c1',
  platform: 'youtube',
  ref: 'lucazbindenvolley2500',
  label: 'Luca Zbinden',
  added_at: '2026-10-16T09:30:00Z',
}

const club: ImportChannel = {
  id: '00000000-0000-4000-8000-0000000000c2',
  platform: 'twitch',
  ref: 'kscwiedikon',
  label: 'KSC Wiedikon',
  added_at: '2026-10-15T18:00:00Z',
}

/** The allow-list API over an in-memory list; POST answers `add` when given. */
function channelsApi(list: ImportChannel[], add?: { status: number; body: unknown }) {
  const fetch = vi.fn(
    stubFetch({
      '/api/admin/import-channels': { body: () => [...list].sort((a, b) => a.label.localeCompare(b.label)) },
      'POST /api/admin/import-channels': add ?? {
        status: 201,
        body: ({ body }: { body: unknown }) => {
          const { label } = body as { channel: string; label: string }
          const added: ImportChannel = {
            id: '00000000-0000-4000-8000-0000000000c3',
            platform: 'youtube',
            ref: 'kscwiedikon',
            label,
            added_at: '2026-10-16T10:00:00Z',
          }
          list.push(added)
          return added
        },
      },
      [`DELETE /api/admin/import-channels/${luca.id}`]: {
        status: 204,
        body: () => list.splice(list.findIndex((c) => c.id === luca.id), 1),
      },
    }),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function open(isAdmin = true) {
  return renderRoute(<AdminImportChannelsPage />, {
    path: '/admin/import-channels',
    url: '/admin/import-channels',
    me: me(isAdmin),
  })
}

describe('AdminImportChannelsPage', () => {
  it('lists the allowed channels with their platform, channel page and date', async () => {
    channelsApi([luca, club])
    open()
    expect(screen.getByRole('link', { name: 'Import channels' })).toHaveAttribute('href', '/admin/import-channels')
    const table = await screen.findByRole('table', { name: 'Import channels' })
    const [first, second] = within(table).getAllByRole('row').slice(1) as [HTMLElement, HTMLElement]
    expect(first).toHaveTextContent('KSC Wiedikon')
    expect(first).toHaveTextContent('Twitch')
    // The channel is a column from sm up and a line under the label on phones (both in jsdom, which has no CSS).
    for (const link of within(first).getAllByRole('link', { name: 'kscwiedikon' })) {
      expect(link).toHaveAttribute('href', 'https://www.twitch.tv/kscwiedikon')
    }
    expect(first).toHaveTextContent('15.10.2026')
    const [channel] = within(second).getAllByRole('link', { name: '@lucazbindenvolley2500' })
    expect(channel).toHaveAttribute('href', 'https://www.youtube.com/@lucazbindenvolley2500')
    expect(channel).toHaveAttribute('rel', 'noreferrer noopener')
    expect(second).toHaveTextContent('YouTube')
    expect(second).toHaveTextContent('16.10.2026')
  })

  it('adds a channel', async () => {
    const fetch = channelsApi([luca])
    open()
    expect(await screen.findByText('Luca Zbinden')).toBeInTheDocument()
    const add = screen.getByRole('button', { name: 'Add channel' })
    expect(add).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Channel'), { target: { value: ' @KSCWiedikon ' } })
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'KSC Wiedikon' } })
    fireEvent.click(add)

    expect(await screen.findByText('KSC Wiedikon added.')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([path, init]) => path === '/api/admin/import-channels' && init?.method === 'POST')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ channel: '@KSCWiedikon', label: 'KSC Wiedikon' })
    const table = screen.getByRole('table', { name: 'Import channels' })
    expect((await within(table).findAllByRole('link', { name: '@kscwiedikon' })).length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Channel')).toHaveValue('')
  })

  it.each([
    [409, 'Already on the list as Luca Zbinden'],
    [422, 'Give a YouTube channel (link, @handle or UC… id) or a Twitch channel link (twitch.tv/name)'],
  ])("shows the API's reason when it refuses a channel (%i)", async (status, detail) => {
    channelsApi([luca], { status, body: { detail } })
    open()
    fireEvent.change(await screen.findByLabelText('Channel'), { target: { value: 'https://www.youtube.com/watch?v=NXNsZ_UNM6w' } })
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Luca' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add channel' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(`Couldn't add the channel: ${detail}`)
  })

  it('removes a channel after confirming', async () => {
    const fetch = channelsApi([luca, club])
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Luca Zbinden' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Remove Luca Zbinden?' })
    expect(dialog).toHaveTextContent('Videos already imported stay.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('Luca Zbinden removed.')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(`/api/admin/import-channels/${luca.id}`, expect.objectContaining({ method: 'DELETE' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Luca Zbinden' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Remove KSC Wiedikon' })).toBeInTheDocument()
  })

  it('says when nothing can be imported yet', async () => {
    channelsApi([])
    open()
    expect(await screen.findByText('No channels yet. Until one is added, no video can be imported.')).toBeInTheDocument()
  })

  it('is for admins only', async () => {
    const fetch = channelsApi([luca])
    open(false)
    expect(await screen.findByText('This page is for admins only.')).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })
})

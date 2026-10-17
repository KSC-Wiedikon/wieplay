import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { GuestInvite } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { JoinPage } from './JoinPage'

const TOKEN = 'tok-123'
const URL = `/api/auth/guest/invite/${TOKEN}`

const invite = (patch: Partial<GuestInvite> = {}): GuestInvite => ({
  club: 'VBC Gast',
  team: 'Herren 2',
  team_id: 1000000001,
  season: '2026/27',
  sport: 'volleyball',
  name: 'Paul Gast',
  role: 'player',
  has_account: false,
  ...patch,
})

beforeEach(() => {
  localStorage.clear()
})

function render(routes: Parameters<typeof stubFetch>[0]) {
  const fetch = vi.fn(stubFetch(routes))
  vi.stubGlobal('fetch', fetch)
  renderRoute(<JoinPage />, {
    path: '/join/:token',
    url: `/join/${TOKEN}`,
    routes: [{ path: '/teams/:id', element: <p>The team page</p> }],
  })
  return fetch
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

describe('JoinPage', () => {
  it('shows what the link is for and sets up a new login, then opens the team', async () => {
    const fetch = render({
      [URL]: { body: invite() },
      [`POST ${URL}`]: { body: { next: '/teams/1000000001' } },
    })
    expect(await screen.findByRole('heading', { name: 'Join Herren 2' })).toBeInTheDocument()
    expect(screen.getByText('Paul Gast, player of Herren 2 (VBC Gast), 2026/27')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'privacy notice' })).toHaveAttribute('href', '/privacy')

    const submit = screen.getByRole('button', { name: 'Set up my login' })
    fill('Email', 'paul@example.org')
    fill('Password', 'short')
    expect(screen.getByText('The password needs at least 10 characters.')).toBeInTheDocument()
    fill('Password', 'correct horse battery')
    fill('Repeat the password', 'correct horse batter')
    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument()
    fill('Repeat the password', 'correct horse battery')
    expect(submit).toBeDisabled() // the privacy notice is not accepted yet
    fireEvent.click(screen.getByRole('checkbox'))
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    expect(await screen.findByText('The team page')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
    expect(JSON.parse((post?.[1] as RequestInit).body as string)).toEqual({
      email: 'paul@example.org',
      password: 'correct horse battery',
      accept_privacy: true,
    })
    expect(localStorage.getItem('wieplay-login')).toBe('guest')
  })

  it('sets a new password without asking for the email again', async () => {
    const fetch = render({
      [URL]: { body: invite({ has_account: true, role: 'coach', name: 'Gina Gast' }) },
      [`POST ${URL}`]: { body: { next: '/teams/1000000001' } },
    })
    expect(await screen.findByText('You have a login already: choose a new password.')).toBeInTheDocument()
    expect(screen.getByText('Gina Gast, coach of Herren 2 (VBC Gast), 2026/27')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    fill('Password', 'a brand new password')
    fill('Repeat the password', 'a brand new password')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Save the new password' }))
    expect(await screen.findByText('The team page')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
    expect(JSON.parse((post?.[1] as RequestInit).body as string)).toMatchObject({ email: null })
  })

  it('says when a link was used or has expired, or does not work', async () => {
    render({ [URL]: { status: 410, body: { detail: 'gone' } } })
    expect(await screen.findByRole('alert')).toHaveTextContent('used or has expired')
    expect(screen.getByRole('link', { name: 'To the login page' })).toHaveAttribute('href', '/')
  })

  it('says when a link is unknown', async () => {
    render({ [URL]: { status: 404, body: { detail: 'Unknown invite link' } } })
    expect(await screen.findByRole('alert')).toHaveTextContent("This invite link doesn't work.")
  })

  it('shows why saving failed', async () => {
    render({
      [URL]: { body: invite() },
      [`POST ${URL}`]: { status: 409, body: { detail: 'This email address belongs to another account' } },
    })
    await screen.findByRole('heading', { name: 'Join Herren 2' })
    fill('Email', 'gina@example.org')
    fill('Password', 'correct horse battery')
    fill('Repeat the password', 'correct horse battery')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Set up my login' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('belongs to another account')
  })
})

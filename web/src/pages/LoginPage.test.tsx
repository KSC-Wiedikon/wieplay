import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { browser, markLoggedOut } from '../lib/guests'
import { renderRoute, stubFetch } from '../test/render'
import { LoginPage } from './LoginPage'

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

function goSpy() {
  return vi.spyOn(browser, 'assign').mockImplementation(() => {})
}

function bodyOf(fetch: ReturnType<typeof vi.fn>, path: string): unknown {
  const call = fetch.mock.calls.find(([url]) => url === path)
  return call ? (JSON.parse((call[1] as RequestInit).body as string) as unknown) : undefined
}

describe('LoginPage', () => {
  it('offers Wiedisync first, for the page that was asked for, and remembers the choice', () => {
    const go = goSpy()
    renderRoute(<LoginPage />, { path: '/teams/:id', url: '/teams/92?tab=stats' })
    expect(screen.getByRole('heading', { name: 'Guest clubs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy')

    fireEvent.click(screen.getByRole('button', { name: 'KSCW members: log in with Wiedisync' }))
    expect(go).toHaveBeenCalledWith('/api/auth/login?next=%2Fteams%2F92%3Ftab%3Dstats')
    expect(localStorage.getItem('wieplay-login')).toBe('wiedisync')
  })

  it('takes KSCW members straight to Wiedisync when that was their choice', () => {
    localStorage.setItem('wieplay-login', 'wiedisync')
    const go = goSpy()
    renderRoute(<LoginPage />, { path: '/', url: '/' })
    expect(go).toHaveBeenCalledWith('/api/auth/login?next=%2F')
    expect(screen.queryByRole('button', { name: /Wiedisync/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Taking you to Wiedisync…')
  })

  it('stays on the page after logging out, and for guests', () => {
    localStorage.setItem('wieplay-login', 'wiedisync')
    markLoggedOut()
    const go = goSpy()
    const first = renderRoute(<LoginPage />, { path: '/', url: '/' })
    expect(screen.getByRole('button', { name: 'KSCW members: log in with Wiedisync' })).toBeInTheDocument()
    first.unmount()

    sessionStorage.clear()
    localStorage.setItem('wieplay-login', 'guest')
    renderRoute(<LoginPage />, { path: '/', url: '/' })
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(go).not.toHaveBeenCalled()
  })

  it('logs a guest in with email and password and remembers that', async () => {
    const fetch = vi.fn(stubFetch({ 'POST /api/auth/guest/login': { status: 204 } }))
    vi.stubGlobal('fetch', fetch)
    renderRoute(<LoginPage />, { path: '/', url: '/' })
    const submit = screen.getByRole('button', { name: 'Log in' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: ' gina@example.org ' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse battery' } })
    fireEvent.click(submit)
    await waitFor(() => expect(localStorage.getItem('wieplay-login')).toBe('guest'))
    expect(bodyOf(fetch, '/api/auth/guest/login')).toEqual({
      email: 'gina@example.org',
      password: 'correct horse battery',
    })
  })

  it('says the same for every wrong login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(stubFetch({ 'POST /api/auth/guest/login': { status: 401, body: { detail: 'Wrong email or password' } } })),
    )
    renderRoute(<LoginPage />, { path: '/', url: '/' })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'who@example.org' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'not the password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong email or password.')
    expect(localStorage.getItem('wieplay-login')).toBeNull()
  })
})

import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Me, Team, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { HomePage } from './HomePage'

const team = (
  id: number,
  name: string,
  sport: Team['sport'],
  rel: string[] = ['player'],
  more: Partial<Team> = {},
): Team => ({
  id,
  name,
  sport,
  season: '2026/27',
  league: '2L',
  rel,
  lineage_id: `lineage-${name}`,
  can_tag: false,
  can_share: false,
  can_export: false,
  ...more,
})

const me = (patch: Partial<Me> = {}): Me => ({
  member: { id: 107, uuid: 'u', name: 'Luca Brunner' },
  roles: [],
  is_admin: false,
  sport_admin: [],
  teams: [],
  ...patch,
})

let n = 0
const video = (teamRow: Team, recorded_on: string, patch: Partial<Video> = {}): Video => {
  n += 1
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    team_id: teamRow.id,
    team_name: teamRow.name,
    team_season: teamRow.season,
    sport: teamRow.sport,
    kind: 'match',
    game_id: null,
    title: `${teamRow.name} match ${recorded_on}`,
    recorded_on,
    source: 'file',
    imported_from: null,
    status: 'ready',
    duration_ms: 5_400_000,
    width: 1920,
    height: 1080,
    fps: 50,
    has_audio: true,
    meets_standard: true,
    standard_issues: [],
    renditions: [],
    has_thumbs: true,
    progress: null,
    error: null,
    created_at: `${recorded_on}T20:00:00Z`,
    created_by: 7,
    can_delete: false,
    can_upload: false,
    can_tag: false,
    can_share: false,
    can_export: false,
    files: [],
    ...patch,
  }
}

const config = { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20 }

/** /api/teams, the video config and each team's videos (teams without a list answer 404). */
function stub(teams: Team[], videos: Record<number, Video[]> = {}, extra: Parameters<typeof stubFetch>[0] = {}) {
  const lists = Object.fromEntries(Object.entries(videos).map(([id, list]) => [`/api/teams/${id}/videos`, { body: list }]))
  vi.stubGlobal(
    'fetch',
    vi.fn(stubFetch({ '/api/teams': { body: teams }, '/api/video/config': { body: config }, ...lists, ...extra })),
  )
}

/** The team cards, in order: the link that opens each team. */
async function cardLinks(): Promise<HTMLElement[]> {
  const section = await screen.findByRole('region', { name: 'Your teams' })
  return within(section)
    .getAllByRole('listitem')
    .filter((item) => item.parentElement?.parentElement === section)
    .map((item) => within(item).getAllByRole('link')[0] as HTMLElement)
}

describe('HomePage', () => {
  it('greets the member by first name and links to clips and reels', async () => {
    stub([team(92, 'H3', 'volleyball')], { 92: [] })
    renderRoute(<HomePage />, { me: me() })

    expect(await screen.findByRole('heading', { level: 1, name: 'Hi Luca' })).toBeInTheDocument()
    expect(screen.getByText('Your teams and their latest matches at a glance.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'My clips' })).toHaveAttribute('href', '/clips')
    expect(screen.getByRole('link', { name: 'Reels' })).toHaveAttribute('href', '/reels')
    // A player can't upload.
    await screen.findByRole('region', { name: 'Recent videos' })
    expect(screen.queryByRole('link', { name: /Upload a video/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Upload a video/ })).not.toBeInTheDocument()
  })

  it('welcomes a member without a name', async () => {
    stub([team(92, 'H3', 'volleyball')], { 92: [] })
    renderRoute(<HomePage />, { me: me({ member: { id: 1, uuid: 'u', name: ' ' } }) })
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  })

  it('shows a card per team, volleyball first, with league, season and roles', async () => {
    stub(
      [
        team(1, 'Herren 1', 'basketball', ['coach'], { league: '1. Liga regional' }),
        team(92, 'H3', 'volleyball', ['player', 'captain'], { league: '3. Liga' }),
        team(10, 'D10', 'volleyball'),
        team(2, 'D2', 'volleyball', []),
      ],
      { 1: [], 92: [], 10: [], 2: [] },
    )
    renderRoute(<HomePage />, { me: me() })

    // Natural sort within a sport: D2 before D10.
    const links = await cardLinks()
    expect(links.map((link) => link.textContent)).toEqual(['D2', 'D10', 'H3', 'Herren 1'])
    expect(links[2]).toHaveAttribute('href', '/teams/92')

    const h3 = links[2]?.closest('li') as HTMLElement
    expect(within(h3).getByText('VB')).toBeInTheDocument()
    expect(within(h3).getByText('3. Liga · 2026/27')).toBeInTheDocument()
    const roles = within(h3).getByRole('list', { name: 'Your role' })
    expect(within(roles).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Player', 'Captain'])

    const d2 = links[0]?.closest('li') as HTMLElement
    expect(within(d2).getByText('Admin access')).toBeInTheDocument()
    expect(within(links[3]?.closest('li') as HTMLElement).getByText('BB')).toBeInTheDocument()
  })

  it('shows the number of videos, the latest date and the latest ready poster', async () => {
    const h3 = team(92, 'H3', 'volleyball')
    const ready = video(h3, '2026-10-04')
    stub([h3], {
      92: [
        video(h3, '2026-09-27'),
        ready,
        // Newest, but not ready yet: it counts, its poster doesn't.
        video(h3, '2026-10-11', { status: 'processing', has_thumbs: false, progress: 0.5 }),
      ],
    })
    renderRoute(<HomePage />, { me: me() })

    const [link] = await cardLinks()
    const card = link?.closest('li') as HTMLElement
    expect(await within(card).findByText('3 videos')).toBeInTheDocument()
    expect(within(card).getByText('Latest 11.10.2026')).toBeInTheDocument()
    const poster = card.querySelector('img[src^="/api/media/"]')
    expect(poster).toHaveAttribute('src', `/api/media/${ready.id}/poster.jpg`)
  })

  it('says when a team has no videos yet', async () => {
    stub([team(1, 'Herren 1', 'basketball')], { 1: [] })
    renderRoute(<HomePage />, { me: me() })
    const [link] = await cardLinks()
    expect(await within(link?.closest('li') as HTMLElement).findByText('No videos yet')).toBeInTheDocument()
  })

  it('keeps earlier seasons behind a disclosure in the card', async () => {
    stub(
      [
        team(3, 'H3', 'volleyball', ['coach'], { season: '2025/26', league: '3L' }),
        team(92, 'H3', 'volleyball', ['team_responsible']),
        // Renamed between seasons: the lineage, not the name, makes it the same team.
        team(1393, 'D2', 'volleyball', ['coach'], { season: '2025/26', lineage_id: 'lineage-D1' }),
        team(94, 'D1', 'volleyball', ['coach']),
        team(10, 'D10', 'volleyball'),
      ],
      { 3: [], 92: [], 1393: [], 94: [], 10: [] },
    )
    renderRoute(<HomePage />, { me: me() })

    const links = await cardLinks()
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/teams/94', '/teams/10', '/teams/92'])
    const d10 = links[1]?.closest('li') as HTMLElement
    expect(within(d10).queryByRole('button', { name: /earlier seasons/ })).not.toBeInTheDocument()

    const h3 = links[2]?.closest('li') as HTMLElement
    const toggle = within(h3).getByRole('button', { name: 'Show earlier seasons of H3' })
    expect(toggle).toHaveTextContent('1 earlier season')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(within(h3).queryByRole('link', { name: /2025\/26/ })).not.toBeInTheDocument()

    fireEvent.click(toggle)
    const earlier = within(h3).getByRole('link', { name: /H3 2025\/26/ })
    expect(earlier).toHaveAttribute('href', '/teams/3')
    expect(earlier).toHaveTextContent('Coach')
    const close = within(h3).getByRole('button', { name: 'Hide earlier seasons of H3' })
    expect(close).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(close)
    expect(within(h3).queryByRole('link', { name: /2025\/26/ })).not.toBeInTheDocument()

    const d1 = links[0]?.closest('li') as HTMLElement
    fireEvent.click(within(d1).getByRole('button', { name: 'Show earlier seasons of D1' }))
    expect(within(d1).getByRole('link', { name: /D2 2025\/26/ })).toHaveAttribute('href', '/teams/1393')
  })

  it('lists the newest videos of all teams, with the status of those not ready', async () => {
    const h3 = team(92, 'H3', 'volleyball')
    const h3old = team(3, 'H3', 'volleyball', ['player'], { season: '2025/26' })
    const d1 = team(94, 'D1', 'volleyball')
    stub([h3, h3old, d1], {
      92: [
        video(h3, '2026-10-04', { title: 'KSCW H3 – Volley Näfels' }),
        video(h3, '2026-10-14', { title: 'Friendly against VBC Zürich', status: 'processing', has_thumbs: false }),
        video(h3, '2026-10-13', { title: 'Not allowed', status: 'refused' }),
      ],
      3: [video(h3old, '2026-03-14', { title: 'KSCW H3 – Volley Smash 05' })],
      94: [video(d1, '2026-10-12', { title: 'KSCW D1 – VC Kanti' })],
    })
    renderRoute(<HomePage />, { me: me() })

    const recent = await screen.findByRole('list', { name: 'Recent videos' })
    await within(recent).findByText('KSCW H3 – Volley Smash 05')
    const items = within(recent).getAllByRole('listitem')
    expect(items.map((item) => within(item).getByRole('link').textContent)).toEqual([
      'Friendly against VBC Zürich',
      'KSCW D1 – VC Kanti',
      'KSCW H3 – Volley Näfels',
      'KSCW H3 – Volley Smash 05',
    ])
    const [friendly, kanti] = items as [HTMLElement, HTMLElement]
    expect(within(friendly).getByText('Processing')).toBeInTheDocument()
    expect(within(friendly).getByText('14.10.2026')).toBeInTheDocument()
    expect(within(friendly).getByRole('link')).toHaveAttribute('href', expect.stringMatching(/^\/videos\//))
    expect(within(kanti).queryByText('Ready')).not.toBeInTheDocument()
    expect(within(kanti).getByText('1:30:00')).toBeInTheDocument()
  })

  it('shows an empty state when the teams have no videos', async () => {
    stub([team(92, 'H3', 'volleyball')], { 92: [] })
    renderRoute(<HomePage />, { me: me() })
    const recent = await screen.findByRole('region', { name: 'Recent videos' })
    expect(await within(recent).findByText('No videos yet')).toBeInTheDocument()
    expect(within(recent).getByText(/Your coaches upload the matches/)).toBeInTheDocument()
  })

  it('offers coaches the upload, straight for one team', async () => {
    stub([team(94, 'D1', 'volleyball', ['coach'])], { 94: [] })
    renderRoute(<HomePage />, { me: me() })
    const [upload] = await screen.findAllByRole('link', { name: 'Upload a video' })
    expect(upload).toHaveAttribute('href', '/teams/94/upload')
    const recent = await screen.findByRole('region', { name: 'Recent videos' })
    expect(await within(recent).findByText(/Upload the first match/)).toBeInTheDocument()
  })

  it('lets a sport admin pick the team to upload for', async () => {
    stub([team(92, 'H3', 'volleyball', []), team(94, 'D1', 'volleyball', []), team(1, 'Herren 1', 'basketball', [])], {
      92: [video(team(92, 'H3', 'volleyball'), '2026-10-11')],
      94: [],
      1: [],
    })
    renderRoute(<HomePage />, { me: me({ sport_admin: ['volleyball'] }) })

    const button = await screen.findByRole('button', { name: 'Upload a video' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const menu = document.getElementById(button.getAttribute('aria-controls') ?? '') as HTMLElement
    // Basketball is not this admin's sport.
    expect(within(menu).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/teams/94/upload',
      '/teams/92/upload',
    ])
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('offers no upload while video storage is off', async () => {
    stub([team(94, 'D1', 'volleyball', ['coach'])], { 94: [] }, { '/api/video/config': { body: { ...config, enabled: false } } })
    renderRoute(<HomePage />, { me: me() })
    await cardLinks()
    expect(screen.queryByRole('link', { name: 'Upload a video' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Recent videos' })).not.toBeInTheDocument()
  })

  it('shows the empty state without teams', async () => {
    stub([])
    renderRoute(<HomePage />, { me: me() })
    expect(await screen.findByText('None of your teams uses WiePlay yet.')).toBeInTheDocument()
    expect(screen.getByText(/When an admin switches WiePlay on/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Choose teams' })).not.toBeInTheDocument()
  })

  it('points admins without teams to the team admin', async () => {
    stub([])
    renderRoute(<HomePage />, { me: me({ is_admin: true }) })
    expect(await screen.findByRole('link', { name: 'Choose teams' })).toHaveAttribute('href', '/admin/teams')
  })
})

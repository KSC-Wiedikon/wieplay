// The video page's clips, comments, downloads and drills (M3).

import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Clip, Comment, Reel, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, rally } from '../test/tagging'
import { VideoPage } from './VideoPage'

const ID = '00000000-0000-4000-8000-0000000000c1'
const MINE = '00000000-0000-4000-8000-00000000c101'
const TEAMS = '00000000-0000-4000-8000-00000000c102'

const video = (patch: Partial<Video> = {}): Video => ({
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 501,
  training_id: null,
  title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  source: 'file',
  status: 'ready',
  duration_ms: 180_000,
  width: 1920,
  height: 1080,
  fps: 50,
  has_audio: true,
  meets_standard: true,
  standard_issues: [],
  renditions: [{ name: '1080p', width: 1920, height: 1080, fps: 50 }],
  has_thumbs: false,
  progress: null,
  error: null,
  created_at: '2026-10-11T22:00:00Z',
  created_by: 7,
  can_delete: false,
  can_upload: false,
  files: [],
  ...patch,
})

const clip = (patch: Partial<Clip>): Clip => ({
  id: MINE,
  video_id: ID,
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  start_ms: 15_000,
  end_ms: 22_000,
  title: 'My attack',
  note: null,
  event_id: null,
  owner: 107,
  mine: true,
  visibility: 'private',
  created_at: '2026-10-12T19:00:00Z',
  ...patch,
})

const comment = (patch: Partial<Comment>): Comment => ({
  id: '00000000-0000-4000-8000-00000000d001',
  video_id: ID,
  target_type: 'video',
  target_id: null,
  t_ms: null,
  author: 7,
  author_name: 'Coach Test',
  body: 'Solid first set.',
  mentions: [],
  audience: 'team',
  recipient: null,
  created_at: '2026-10-12T19:05:00Z',
  edited_at: null,
  mine: false,
  can_delete: false,
  ...patch,
})

const me = { member: { id: 107, uuid: 'u', name: 'Luca Brunner' }, roles: [], is_admin: false, sport_admin: [], teams: [] }

interface Options {
  video?: Video
  tagging?: Tagging
  clips?: Clip[]
  comments?: Comment[]
  reels?: Reel[]
  routes?: Record<string, { status?: number; body?: unknown }>
  url?: string
  /** PUT /api/clips/{any id}: answers with this status, or the clip as sent. */
  putClip?: number
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function show({ video: v = video(), tagging = bundle({ can_tag: false }), clips = [], comments = [], reels = [], routes = {}, url, putClip = 200 }: Options = {}) {
  const stub = stubFetch({
      '/api/me': { body: me },
      [`/api/videos/${ID}`]: { body: v },
      '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20, notice_version: 'x' } },
      [`/api/videos/${ID}/tagging`]: { body: tagging },
      [`/api/videos/${ID}/clips`]: { body: clips },
      [`/api/videos/${ID}/comments`]: { body: comments },
      '/api/reels': { body: reels },
      ...routes,
  })
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (init?.method === 'PUT' && path.startsWith('/api/clips/') && !(`PUT ${path}` in routes)) {
      const body = JSON.parse(String(init.body)) as Partial<Clip>
      return putClip === 200 ? json(200, clip({ ...body, id: path.slice('/api/clips/'.length) })) : json(putClip, { detail: 'Only coaches share clips with the team' })
    }
    return stub(input, init)
  })
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<VideoPage />, { path: '/videos/:id', url: url ?? `/videos/${ID}` })
  return { ...view, fetch }
}

function sent(fetch: ReturnType<typeof vi.fn>, method: string, path: string | RegExp): unknown[] {
  return fetch.mock.calls
    .filter(([p, init]) => (init as RequestInit | undefined)?.method === method && (typeof path === 'string' ? p === path : path.test(String(p))))
    .map(([, init]) => JSON.parse(String((init as RequestInit).body ?? 'null')) as unknown)
}

function player(): HTMLVideoElement {
  const element = document.querySelector('video')
  if (!element) throw new Error('no video')
  return element
}

/** Moves the video and lets the playhead read it. */
function at(seconds: number) {
  player().currentTime = seconds
  fireEvent(player(), new Event('timeupdate'))
}

/** The roster (who can be mentioned) comes with the video's tagging data. */
async function peopleLoaded() {
  await waitFor(() => expect(screen.queryByText(/No one to mention/)).not.toBeInTheDocument())
}

function playing(element: HTMLVideoElement) {
  Object.defineProperty(element, 'paused', { configurable: true, get: () => false })
  fireEvent(element, new Event('play'))
}

beforeEach(() => {
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('clips on the video page', () => {
  it('saves the marked range as a private clip; players get no team option', async () => {
    const { fetch } = show()
    await screen.findByRole('heading', { name: 'Clips' })
    expect(screen.getByRole('button', { name: 'Save clip' })).toBeDisabled()
    at(30)
    fireEvent.click(screen.getByRole('button', { name: 'Clip in' }))
    at(36.5)
    fireEvent.click(screen.getByRole('button', { name: 'Clip out' }))
    expect(screen.getByText('In 0:30.0 · Out 0:36.5')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save clip' }))

    const dialog = screen.getByRole('dialog', { name: 'Save clip' })
    expect(within(dialog).getByLabelText('Title')).toHaveValue('Clip at 0:30')
    expect(within(dialog).queryByLabelText('The whole team')).not.toBeInTheDocument()
    expect(within(dialog).getByText('Only you see your clips.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Block at the net' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save clip' }))

    await screen.findByText('Clip saved.')
    expect(sent(fetch, 'PUT', /^\/api\/clips\//)).toEqual([{
      video_id: ID,
      start_ms: 30_000,
      end_ms: 36_500,
      title: 'Block at the net',
      note: null,
      event_id: null,
      visibility: 'private',
    }])
    // The marks are cleared once the clip is saved.
    expect(screen.getByText('Mark the start and the end at the playhead.')).toBeInTheDocument()
  })

  it('offers coaches to share with the team, and explains a refusal', async () => {
    const { fetch } = show({ video: video({ can_upload: true, can_delete: true }), tagging: bundle({ can_tag: true }), putClip: 403 })
    await screen.findByRole('heading', { name: 'Clips' })
    at(12)
    fireEvent.click(screen.getByRole('button', { name: 'Clip in' }))
    at(4) // the playhead is before the mark: the clip gets 10 s
    fireEvent.click(screen.getByRole('button', { name: 'Save clip' }))
    const dialog = screen.getByRole('dialog', { name: 'Save clip' })
    expect(within(dialog).getByText('0:12.0')).toBeInTheDocument()
    expect(within(dialog).getByText('0:22.0')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByLabelText('The whole team'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save clip' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Only coaches share clips with the team.')
    const [body] = sent(fetch, 'PUT', /^\/api\/clips\//)
    expect(body).toMatchObject({ start_ms: 12_000, end_ms: 22_000, visibility: 'team' })
  })

  it('edits my clips, and deletes them after confirming; others’ team clips only coaches delete', async () => {
    const theirs = clip({ id: TEAMS, title: 'Kill by Mia', owner: 7, mine: false, visibility: 'team', start_ms: 40_000, end_ms: 45_000 })
    const { fetch } = show({
      clips: [clip({ note: 'Arm high' }), theirs],
      routes: {
        [`PUT /api/clips/${MINE}`]: { body: clip({ title: 'My best attack' }) },
        [`DELETE /api/clips/${MINE}`]: { status: 204 },
      },
    })
    const table = await screen.findByRole('table', { name: 'Clips' })
    expect(within(table).getByText('Arm high')).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Edit Kill by Mia' })).not.toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Delete Kill by Mia' })).not.toBeInTheDocument()

    fireEvent.click(within(table).getByRole('button', { name: 'Edit My attack' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit clip' })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'My best attack' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start 1 second later' }))
    fireEvent.change(within(dialog).getByLabelText('Note (optional)'), { target: { value: '' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save clip' }))
    await screen.findByText('Clip saved.')
    expect(sent(fetch, 'PUT', `/api/clips/${MINE}`)).toEqual([
      { video_id: ID, start_ms: 16_000, end_ms: 22_000, title: 'My best attack', note: null, event_id: null, visibility: 'private' },
    ])

    fireEvent.click(within(table).getByRole('button', { name: 'Delete My attack' }))
    fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Delete this clip?' })).getByRole('button', { name: 'Delete clip' }))
    await screen.findByText('Clip deleted.')
    expect(fetch).toHaveBeenCalledWith(`/api/clips/${MINE}`, expect.objectContaining({ method: 'DELETE' }))
  })

  it('lets coaches delete the team’s clips of others', async () => {
    show({
      video: video({ can_delete: true }),
      clips: [clip({ id: TEAMS, title: 'Kill by Mia', owner: 7, mine: false, visibility: 'team' }), clip({ id: MINE, title: 'Private', owner: 9, mine: false })],
    })
    const table = await screen.findByRole('table', { name: 'Clips' })
    expect(within(table).getByRole('button', { name: 'Delete Kill by Mia' })).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Edit Kill by Mia' })).not.toBeInTheDocument()
  })

  it('plays a clip from its start and stops at its end; "one after another" moves on', async () => {
    show({ clips: [clip({}), clip({ id: TEAMS, title: 'Second', start_ms: 40_000, end_ms: 45_000 })] })
    const table = await screen.findByRole('table', { name: 'Clips' })
    fireEvent.click(within(table).getByRole('button', { name: 'Play My attack' }))
    const element = player()
    expect(element.currentTime).toBe(15)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
    expect(screen.getByText('Playing My attack')).toBeInTheDocument()
    playing(element)
    at(21.5)
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled()
    at(22)
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByText('Playing My attack')).not.toBeInTheDocument())

    fireEvent.click(screen.getByRole('switch', { name: 'Play one after another' }))
    fireEvent.click(within(table).getByRole('button', { name: 'Play My attack' }))
    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    playing(element)
    at(22)
    expect(element.currentTime).toBe(40)
    expect(await screen.findByText('Playing Second')).toBeInTheDocument()
    // A seek far away leaves the clip.
    at(100)
    fireEvent(element, new Event('seeked'))
    await waitFor(() => expect(screen.queryByText('Playing Second')).not.toBeInTheDocument())
  })

  it('opens at a clip from "My clips" and plays just that range', async () => {
    show({ clips: [clip({ start_ms: 85_000, end_ms: 95_000 })], url: `/videos/${ID}?clip=${MINE}` })
    await screen.findByRole('table', { name: 'Clips' })
    // It waits until the video can seek.
    expect(screen.queryByText('Playing My attack')).not.toBeInTheDocument()
    const element = player()
    fireEvent(element, new Event('loadedmetadata'))
    expect(await screen.findByText('Playing My attack')).toBeInTheDocument()
    expect(element.currentTime).toBe(85)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
    playing(element)
    at(95)
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
  })

  it('adds a clip to a new reel, or to the end of one of mine', async () => {
    const reel: Reel = {
      id: '00000000-0000-4000-8000-00000000e001',
      title: 'Approach work',
      kind: 'manual',
      query: null,
      visibility: 'private',
      lineage_id: null,
      owner: 107,
      mine: true,
      created_at: '2026-10-12T10:00:00Z',
      updated_at: '2026-10-12T10:00:00Z',
    }
    const { fetch } = show({
      clips: [clip({})],
      reels: [reel, { ...reel, id: '00000000-0000-4000-8000-00000000e002', title: 'Smart one', kind: 'smart' }],
      routes: {
        [`/api/reels/${reel.id}/items`]: {
          body: [{ video_id: ID, start_ms: 1_000, end_ms: 4_000, event_id: null, clip_id: null, caption: 'First', video_title: 't', recorded_on: '2026-10-11', code: null, grade: null, side: null, number: null, member_id: null }],
        },
        [`PUT /api/reels/${reel.id}`]: { body: reel },
        'POST /api/reels': { status: 201, body: { ...reel, title: 'My attack' } },
      },
    })
    const table = await screen.findByRole('table', { name: 'Clips' })
    fireEvent.click(within(table).getByRole('button', { name: 'Add My attack to a reel' }))
    let dialog = screen.getByRole('dialog', { name: 'Add to reel' })
    // Only my reels from clips are offered.
    expect(await within(dialog).findByLabelText('Approach work')).toBeChecked()
    expect(within(dialog).queryByLabelText('Smart one')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }))
    await screen.findByText('Added to Approach work.')
    expect(sent(fetch, 'PUT', `/api/reels/${reel.id}`)).toEqual([
      {
        title: 'Approach work',
        kind: 'manual',
        visibility: 'private',
        lineage_id: null,
        items: [
          { video_id: ID, start_ms: 1_000, end_ms: 4_000, event_id: null, clip_id: null, caption: 'First' },
          { video_id: ID, start_ms: 15_000, end_ms: 22_000, clip_id: MINE, caption: 'My attack' },
        ],
      },
    ])

    fireEvent.click(within(table).getByRole('button', { name: 'Add My attack to a reel' }))
    dialog = screen.getByRole('dialog', { name: 'Add to reel' })
    fireEvent.click(await within(dialog).findByLabelText('New reel'))
    expect(within(dialog).getByLabelText('Title of the new reel')).toHaveValue('My attack')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(sent(fetch, 'POST', '/api/reels')).toHaveLength(1))
    expect(sent(fetch, 'POST', '/api/reels')[0]).toEqual({
      title: 'My attack',
      kind: 'manual',
      items: [{ video_id: ID, start_ms: 15_000, end_ms: 22_000, clip_id: MINE, caption: 'My attack' }],
      visibility: 'private',
    })
  })
})

describe('comments', () => {
  const comments = [
    comment({ id: '00000000-0000-4000-8000-00000000d002', t_ms: 46_000, body: 'Rotation 2 needs work.', audience: 'coaches', created_at: '2026-10-12T19:07:00Z' }),
    comment({ id: '00000000-0000-4000-8000-00000000d003', t_ms: 18_000, body: 'Great kill @Luca Brunner, arm high.', mentions: [107] }),
    comment({}),
    comment({ id: '00000000-0000-4000-8000-00000000d004', t_ms: 60_000, author: 107, author_name: 'Luca Brunner', body: 'Thanks', audience: 'person', recipient: 103, mine: true, can_delete: true }),
  ]

  it('lists them by moment; a click on the time goes there', async () => {
    show({ comments })
    const list = await screen.findByRole('list', { name: 'Comments' })
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => within(li).getByText(/Solid|Great|Rotation|Thanks/).textContent)).toEqual([
      'Solid first set.',
      expect.stringContaining('Great kill'),
      'Rotation 2 needs work.',
      'Thanks',
    ])
    expect(within(items[0] as HTMLElement).getByText('Whole video')).toBeInTheDocument()
    expect(within(items[1] as HTMLElement).getByText('@Luca Brunner')).toHaveProperty('tagName', 'STRONG')
    expect(within(items[2] as HTMLElement).getByText('Coaches only')).toBeInTheDocument()
    expect(within(items[3] as HTMLElement).getByText('To Mia Keller')).toBeInTheDocument()

    fireEvent.click(within(list).getByRole('button', { name: 'Go to 0:46' }))
    expect(player().currentTime).toBe(46)
    // Only my own comment can be edited and deleted here.
    expect(within(list).getAllByRole('button', { name: 'Edit comment' })).toHaveLength(1)
    expect(within(list).getAllByRole('button', { name: 'Delete comment' })).toHaveLength(1)
  })

  it('posts at the current time to one person, with a mention picked from the roster', async () => {
    const { fetch } = show({
      routes: { [`POST /api/videos/${ID}/comments`]: { status: 201, body: ({ body }: { body: unknown }) => comment({ ...(body as Partial<Comment>), mine: true }) } },
    })
    const box = await screen.findByLabelText('Comment')
    await peopleLoaded()
    at(50)
    expect(screen.getByLabelText('At 0:50')).toBeChecked()
    fireEvent.change(box, { target: { value: 'Look @mi' } })
    const options = screen.getByRole('listbox', { name: 'People to mention' })
    expect(within(options).getAllByRole('option').map((o) => o.textContent)).toEqual(['#3 Mia Keller'])
    fireEvent.mouseDown(within(options).getByRole('option', { name: '#3 Mia Keller' }))
    expect(box).toHaveValue('Look @Mia Keller ')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    fireEvent.change(box, { target: { value: 'Look @Mia Keller and the block.' } })

    const post = screen.getByRole('button', { name: 'Post comment' })
    fireEvent.change(screen.getByLabelText('Who sees it'), { target: { value: 'person' } })
    expect(post).toBeDisabled()
    const to = screen.getByLabelText('To')
    // I can't write to myself: Luca is the one logged in.
    expect(within(to).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose…', '#3 Mia Keller', '#11 Noah Meier'])
    fireEvent.change(to, { target: { value: '103' } })
    fireEvent.click(post)

    await screen.findByText('Comment posted.')
    const [body] = sent(fetch, 'POST', `/api/videos/${ID}/comments`)
    expect(body).toEqual({
      id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      target_type: 'video',
      t_ms: 50_000,
      body: 'Look @Mia Keller and the block.',
      mentions: [103],
      audience: 'person',
      recipient: 103,
    })
    expect(box).toHaveValue('')
    expect(screen.getByLabelText('Who sees it')).toHaveValue('team')
  })

  it('picks a mention with the keyboard; coaches-only comments have no time when unticked', async () => {
    const { fetch } = show({
      routes: { [`POST /api/videos/${ID}/comments`]: { status: 201, body: ({ body }: { body: unknown }) => comment({ ...(body as Partial<Comment>), mine: true }) } },
    })
    const box = await screen.findByLabelText('Comment')
    await peopleLoaded()
    fireEvent.change(box, { target: { value: '@' } })
    expect(screen.getByRole('option', { name: '#3 Mia Keller' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: '#11 Noah Meier' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box, { key: 'ArrowUp' })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(box).toHaveValue('@Mia Keller ')
    fireEvent.change(screen.getByLabelText('Who sees it'), { target: { value: 'coaches' } })
    fireEvent.click(screen.getByLabelText(/^At /))
    fireEvent.click(screen.getByRole('button', { name: 'Post comment' }))
    await screen.findByText('Comment posted.')
    expect(sent(fetch, 'POST', `/api/videos/${ID}/comments`)[0]).toMatchObject({ t_ms: null, audience: 'coaches', recipient: null, mentions: [103] })
  })

  it('shows why a comment was refused', async () => {
    show({ routes: { [`POST /api/videos/${ID}/comments`]: { status: 422, body: { detail: 'Only people of this team can be mentioned' } } } })
    const box = await screen.findByLabelText('Comment')
    fireEvent.change(box, { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Post comment' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't post the comment: Only people of this team can be mentioned")
  })

  it('edits my comment and lets coaches delete anyone’s', async () => {
    const { fetch } = show({
      comments: [comment({ can_delete: true }), comments[3] as Comment],
      routes: {
        [`PATCH /api/comments/${(comments[3] as Comment).id}`]: { body: { ...(comments[3] as Comment), body: 'Thanks a lot', edited_at: '2026-10-13T10:00:00Z' } },
        'DELETE /api/comments/00000000-0000-4000-8000-00000000d001': { status: 204 },
      },
    })
    const list = await screen.findByRole('list', { name: 'Comments' })
    fireEvent.click(within(list).getByRole('button', { name: 'Edit comment' }))
    fireEvent.change(within(list).getByDisplayValue('Thanks'), { target: { value: 'Thanks a lot' } })
    fireEvent.click(within(list).getByRole('button', { name: 'Save' }))
    expect(await within(list).findByText('Thanks a lot', { selector: 'span' })).toBeInTheDocument()
    expect(within(list).getByText(/edited/)).toBeInTheDocument()
    expect(sent(fetch, 'PATCH', `/api/comments/${(comments[3] as Comment).id}`)).toEqual([{ body: 'Thanks a lot' }])

    fireEvent.click(within(list).getAllByRole('button', { name: 'Delete comment' })[0] as HTMLElement)
    fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Delete this comment?' })).getByRole('button', { name: 'Delete comment' }))
    await waitFor(() => expect(within(list).queryByText('Solid first set.')).not.toBeInTheDocument())
  })
})

describe('downloads', () => {
  const coach = () => video({ can_upload: true, can_delete: true })
  const tagged = () => bundle({ can_tag: true, segments: [rally({ start_ms: 10_000, end_ms: 20_000 })] })

  it('lets coaches download a clip and the rallies, explaining the rules once', async () => {
    const { fetch } = show({
      video: coach(),
      tagging: tagged(),
      clips: [clip({})],
      routes: { 'POST /api/exports': { status: 201, body: { id: 'x', kind: 'clip', title: 'My attack', duration_ms: 7000, status: 'queued', bytes: null, progress: null, error: null, created_at: '', expires_at: null } } },
    })
    const table = await screen.findByRole('table', { name: 'Clips' })
    fireEvent.click(within(table).getByRole('button', { name: 'Download My attack as MP4' }))
    const rules = screen.getByRole('alertdialog', { name: 'Download as MP4' })
    expect(rules).toHaveTextContent('never post it publicly')
    fireEvent.click(within(rules).getByRole('button', { name: 'Prepare the MP4' }))
    await screen.findByText('Preparing the MP4. Find it under Downloads.')

    // The second time, straight away.
    fireEvent.click(await screen.findByRole('button', { name: 'Download as MP4' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    await waitFor(() => expect(sent(fetch, 'POST', '/api/exports')).toHaveLength(2))
    expect(sent(fetch, 'POST', '/api/exports')).toEqual([
      { kind: 'clip', clip_id: MINE },
      { kind: 'condensed', video_id: ID },
    ])
  })

  it('says why a download is refused', async () => {
    window.localStorage.setItem('nomal-export-rules', '1')
    show({ video: coach(), tagging: tagged(), routes: { 'POST /api/exports': { status: 409, body: { detail: 'The originals of KSCW H3 are not available' } } } })
    fireEvent.click(await screen.findByRole('button', { name: 'Download as MP4' }))
    expect(await screen.findByText("Can't prepare the MP4: The originals of KSCW H3 are not available")).toBeInTheDocument()
  })

  it('shows players no download buttons', async () => {
    show({ tagging: tagged(), clips: [clip({})] })
    await screen.findByRole('table', { name: 'Clips' })
    await screen.findByRole('switch', { name: 'Skip dead time' })
    expect(screen.queryByRole('button', { name: /Download/ })).not.toBeInTheDocument()
  })
})

describe('drills', () => {
  it('lists the drills of a training and plays one', async () => {
    const drill = { ...rally({ start_ms: 20_000, end_ms: 80_000 }), kind: 'drill' as const, label: 'Serve receive 3v3' }
    show({ video: video({ kind: 'training', game_id: null, training_id: 601 }), tagging: bundle({ can_tag: false, segments: [drill] }) })
    const table = await screen.findByRole('table', { name: 'Drills' })
    expect(within(table).getByText('0:20–1:20')).toBeInTheDocument()
    fireEvent.click(within(table).getByRole('button', { name: 'Play Serve receive 3v3' }))
    expect(player().currentTime).toBe(20)
    expect(screen.getByText('Playing Serve receive 3v3')).toBeInTheDocument()
  })
})

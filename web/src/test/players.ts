// Players fixtures (docs/PLAN.md §10.5): cards and a gallery as GET /api/videos/{id}/players
// returns them, and a ready video to show them for.

import type { PlayerCluster, Players } from '../lib/players'
import type { Video } from '../lib/types'

export const PLAYERS_VIDEO_ID = '00000000-0000-4000-8000-0000000000aa'

export function cluster(patch: Partial<PlayerCluster> = {}): PlayerCluster {
  const key = patch.key ?? 1
  return {
    id: `c${key}`,
    key,
    team: 'us',
    number: null,
    member_id: null,
    name: null,
    not_player: false,
    referee_guess: false,
    ocr_number: null,
    ocr_confidence: null,
    crops: [[0, key]],
    detections: 600,
    seconds: 60,
    actions: 3,
    source: 'model',
    tracks: [
      { id: key * 10, t0: 10_000, t1: 40_000, n: 300, side: 'near', number: null, crop: [0, 40 + key] },
      { id: key * 10 + 1, t0: 70_000, t1: 100_000, n: 300, side: 'near', number: null, crop: [0, 60 + key] },
    ],
    ...patch,
  }
}

export function playersState(patch: Partial<Players> = {}): Players {
  return {
    video_id: PLAYERS_VIDEO_ID,
    available: true,
    can_edit: true,
    status: 'done',
    job: null,
    run: { id: 'run1', created_at: '2026-10-17T00:00:00Z', stats: {}, timing: {} },
    sheets: { count: 1, cell_w: 80, cell_h: 160, cols: 10, rows: 8, version: 'run1' },
    clusters: [
      cluster({ key: 1, seconds: 300, ocr_number: 7, ocr_confidence: 0.8 }),
      cluster({ key: 2, seconds: 200 }),
      cluster({ key: 3, team: 'them', seconds: 250 }),
      cluster({ key: 4, team: 'them', not_player: true, referee_guess: true }),
    ],
    roster: [
      { side: 'us', number: 7, member_id: 101, position: 'outside', is_libero: false, name: 'Alex Muster' },
      { side: 'us', number: 3, member_id: 102, position: 'setter', is_libero: false, name: 'Sam Beispiel' },
    ],
    attribution: {
      actions: 40,
      identified: 10,
      identified_share: 0.25,
      by_coach: 4,
      suggested: 6,
      vs_coach: { compared: 4, correct: 3, accuracy: 0.75 },
    },
    open_suggestions: 2,
    ...patch,
  }
}

export function readyVideo(patch: Partial<Video> = {}): Video {
  return {
    id: PLAYERS_VIDEO_ID,
    team_id: 92,
    team_name: 'H3',
    team_season: '2026/27',
    sport: 'volleyball',
    kind: 'match',
    game_id: 501,
    title: 'KSCW H3 – Volley Näfels',
    recorded_on: '2026-10-11',
    source: 'file',
    imported_from: null,
    status: 'ready',
    duration_ms: 5_530_000,
    width: 1920,
    height: 1080,
    fps: 50,
    has_audio: true,
    meets_standard: true,
    standard_issues: [],
    renditions: [{ name: '1080p', width: 1920, height: 1080, fps: 50 }],
    has_thumbs: true,
    progress: null,
    error: null,
    created_at: '2026-10-11T22:00:00Z',
    created_by: 7,
    can_delete: false,
    can_upload: false,
    can_tag: true,
    can_share: false,
    can_export: false,
    files: [],
    ...patch,
  }
}

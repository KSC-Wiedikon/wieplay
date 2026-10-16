// Court fixtures: a calibration as the server computes it (backend/nomal/core/court.py) for a
// camera 6 m behind the near end line, 5 m up, and where court points show in its picture.

import type { Calibration, Camera, CourtState, Mat3 } from '../lib/court'

// A camera 6 m behind the near end line, 5 m up (computed by the server's court.solve).
export const calibration: { image_to_court: Mat3; camera: Camera } = {
  image_to_court: [
    [-0.593727820665, -0.286892541846, 0.341690869996],
    [0, 0.482138855047, -0.460223452545],
    [0, -0.063753898188, 0.009961546592],
  ],
  camera: {
    center: [4.5, -6.0, 5.0],
    focal: 1.1,
    rays: [
      [0.909090909, 0, 0],
      [0, -0.271159085, 0.954479978],
      [0, -0.867709071, -0.298274993],
    ],
    aspect: 1.777778,
  },
}
// Where court points show in that camera's picture.
export const FLOOR_2_12: [number, number] = [0.417155, 0.464856]
export const AIR_2_12: [number, number] = [0.416219, 0.424653] // 0.7 m above (2, 12)
export const FLOOR_6_4: [number, number] = [0.584098, 0.678378]


export const CORNER_POINTS = [
  { landmark: 'near_left' as const, u: 0.062, v: 0.857 },
  { landmark: 'near_right' as const, u: 0.938, v: 0.857 },
  { landmark: 'far_right' as const, u: 0.668, v: 0.413 },
  { landmark: 'far_left' as const, u: 0.332, v: 0.413 },
]

export const savedCalibration = (patch: Partial<Calibration> = {}): Calibration => ({
  ...calibration,
  court_to_image: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ],
  error_cm: 6.2,
  max_error_cm: 9.5,
  residuals_cm: {},
  lines: [
    [
      [0.062, 0.857],
      [0.938, 0.857],
    ],
  ],
  id: '00000000-0000-4000-8000-00000000ca11',
  video_id: '00000000-0000-4000-8000-000000000001',
  status: 'confirmed',
  method: 'manual',
  points: CORNER_POINTS,
  confidence: null,
  copied_from: null,
  updated_at: '2026-10-16T20:00:00Z',
  ...patch,
})

export const courtState = (patch: Partial<CourtState> = {}): CourtState => ({
  video_id: '00000000-0000-4000-8000-000000000001',
  can_edit: true,
  aspect: 16 / 9,
  calibration: null,
  suggestions: [],
  ball_track: { available: true, fps: 15, bytes: 97_712, contacts: 2357, bounces: 512, created_at: '2026-10-16T20:00:00Z', job: 'done' },
  ...patch,
})

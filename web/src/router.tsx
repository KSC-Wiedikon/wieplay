import { createBrowserRouter, type RouteObject } from 'react-router'
import { PageSpinner } from './components/Spinner'
import { AdminTeamsPage } from './pages/AdminTeamsPage'
import { AppLayout } from './pages/AppLayout'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RouteError } from './pages/RouteError'
import { TeamPage } from './pages/TeamPage'

// Pages most visits never open are loaded on demand (the player pulls in hls.js on its own).
const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    errorElement: <RouteError />,
    hydrateFallbackElement: <PageSpinner />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'teams/:id', element: <TeamPage /> },
      {
        path: 'teams/:id/upload',
        lazy: () => import('./pages/UploadPage').then((m) => ({ Component: m.UploadPage })),
      },
      {
        path: 'videos/:id',
        lazy: () => import('./pages/VideoPage').then((m) => ({ Component: m.VideoPage })),
      },
      {
        path: 'videos/:id/players',
        lazy: () => import('./pages/PlayersPage').then((m) => ({ Component: m.PlayersPage })),
      },
      {
        path: 'videos/:id/tag',
        lazy: () => import('./pages/TagPage').then((m) => ({ Component: m.TagPage })),
      },
      {
        path: 'teams/:id/highlights',
        lazy: () => import('./pages/HighlightsPage').then((m) => ({ Component: m.HighlightsPage })),
      },
      {
        path: 'clips',
        lazy: () => import('./pages/ClipsPage').then((m) => ({ Component: m.ClipsPage })),
      },
      {
        path: 'how-to-help',
        lazy: () => import('./pages/GuidePage').then((m) => ({ Component: m.GuidePage })),
      },
      {
        path: 'reels',
        lazy: () => import('./pages/ReelsPage').then((m) => ({ Component: m.ReelsPage })),
      },
      {
        path: 'reels/new',
        lazy: () => import('./pages/ReelBuilderPage').then((m) => ({ Component: m.ReelBuilderPage })),
      },
      {
        path: 'reels/:id',
        lazy: () => import('./pages/ReelPage').then((m) => ({ Component: m.ReelPage })),
      },
      {
        path: 'reels/:id/edit',
        lazy: () => import('./pages/ReelBuilderPage').then((m) => ({ Component: m.ReelBuilderPage })),
      },
      {
        path: 'downloads',
        lazy: () => import('./pages/ExportsPage').then((m) => ({ Component: m.ExportsPage })),
      },
      { path: 'admin/teams', element: <AdminTeamsPage /> },
      {
        path: 'admin/policies',
        lazy: () => import('./pages/AdminPoliciesPage').then((m) => ({ Component: m.AdminPoliciesPage })),
      },
      {
        path: 'admin/workers',
        lazy: () => import('./pages/AdminWorkersPage').then((m) => ({ Component: m.AdminWorkersPage })),
      },
      {
        path: 'admin/jobs',
        lazy: () => import('./pages/AdminJobsPage').then((m) => ({ Component: m.AdminJobsPage })),
      },
      {
        path: 'admin/import-channels',
        lazy: () =>
          import('./pages/AdminImportChannelsPage').then((m) => ({ Component: m.AdminImportChannelsPage })),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export function createRouter() {
  return createBrowserRouter(routes)
}

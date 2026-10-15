import { createBrowserRouter } from 'react-router'
import { AdminTeamsPage } from './pages/AdminTeamsPage'
import { AppLayout } from './pages/AppLayout'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RouteError } from './pages/RouteError'
import { TeamPage } from './pages/TeamPage'

const routes = [
  {
    element: <AppLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'teams/:id', element: <TeamPage /> },
      { path: 'admin/teams', element: <AdminTeamsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export function createRouter() {
  return createBrowserRouter(routes)
}

import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { ToastProvider } from '../components/ToastProvider'
import { createQueryClient } from '../lib/queryClient'

/** Renders a route element with the app's providers, at the given URL. `routes` adds other pages to navigate to. */
export function renderRoute(
  element: ReactElement,
  { path = '/', url = '/', routes = [] }: { path?: string; url?: string; routes?: RouteObject[] } = {},
) {
  const queryClient: QueryClient = createQueryClient()
  queryClient.setDefaultOptions({ queries: { retry: false } })
  const router = createMemoryRouter([{ path, element }, ...routes], { initialEntries: [url] })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  )
  return { ...view, queryClient, router }
}

type StubBody = unknown | ((request: { path: string; method: string; body: unknown }) => unknown)

/**
 * A fetch stub answering JSON by path (with its query string); unknown paths get 404.
 * Keys may start with the method ("POST /api/…"); those win over plain paths.
 * `body` may be a function of the request.
 */
export function stubFetch(routes: Record<string, { status?: number; body?: StubBody }>) {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname + input.search : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    const route = routes[`${method} ${path}`] ?? routes[path]
    if (!route) return jsonResponse(404, { detail: 'Not found' })
    const body =
      typeof route.body === 'function'
        ? (route.body as (request: { path: string; method: string; body: unknown }) => unknown)({
            path,
            method,
            body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
          })
        : route.body
    return jsonResponse(route.status ?? 200, body)
  }
}

export function jsonResponse(status: number, body?: unknown): Response {
  if (status === 204) return new Response(null, { status })
  return new Response(JSON.stringify(body ?? null), { status, headers: { 'Content-Type': 'application/json' } })
}

// Saved filters (docs/PLAN.md §9.7): a search kept by name, for oneself or shared with a team
// (backend/wieplay/api/filters.py). The query is the words as typed; src/lib/search.ts reads them.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'
import type { Visibility } from './types'

export interface SavedFilter {
  id: string
  title: string
  query: string
  visibility: Visibility
  lineage_id: string | null
  owner: number
  owner_name: string | null
  mine: boolean
  can_edit: boolean
  can_delete: boolean
  created_at: string
  updated_at: string
}

export interface SavedFilterIn {
  title: string
  query: string
  visibility: Visibility
  /** The team it is saved for (any season of it); a shared filter needs one. */
  team_id: number | null
}

export const filtersApi = {
  list: (teamId: number | null, signal?: AbortSignal) =>
    apiRequest<SavedFilter[]>(teamId === null ? '/api/filters' : `/api/filters?team_id=${teamId}`, { signal }),
  create: (body: SavedFilterIn) => apiRequest<SavedFilter>('/api/filters', { method: 'POST', body }),
  update: (id: string, body: SavedFilterIn) => apiRequest<SavedFilter>(`/api/filters/${id}`, { method: 'PUT', body }),
  remove: (id: string) => apiRequest<undefined>(`/api/filters/${id}`, { method: 'DELETE' }),
}

const FILTERS = ['filters'] as const

/** Mine (private ones from every team) and the team's shared ones. */
export function useSavedFilters(teamId: number | null, enabled = true) {
  return useQuery({
    queryKey: [...FILTERS, teamId],
    queryFn: ({ signal }) => filtersApi.list(teamId, signal),
    enabled,
  })
}

export function useSaveFilter() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: SavedFilterIn }) => (id ? filtersApi.update(id, body) : filtersApi.create(body)),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: FILTERS })
    },
  })
}

export function useDeleteFilter() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (filter: SavedFilter) => filtersApi.remove(filter.id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: FILTERS })
    },
  })
}

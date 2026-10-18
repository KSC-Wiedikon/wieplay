import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { keys } from './queryClient'
import type { GuestPersonIn, GuestTeam, GuestTeamCreate, GuestTeamUpdate, Me, NextSeasonIn, TeamDetail } from './types'

// Guest clubs (PLAN §4.5): other clubs' teams with their own logins, set up from invite links.

// --- the login page's remembered choice ---------------------------------------------------------

export type LoginChoice = 'wiedisync' | 'guest'

const CHOICE_KEY = 'wieplay-login'
const LOGGED_OUT_KEY = 'wieplay-logged-out'

/** How this browser logged in last time; null when never (or storage is blocked). */
export function rememberedLogin(): LoginChoice | null {
  try {
    const value = window.localStorage.getItem(CHOICE_KEY)
    return value === 'wiedisync' || value === 'guest' ? value : null
  } catch {
    return null
  }
}

export function rememberLogin(choice: LoginChoice): void {
  try {
    window.localStorage.setItem(CHOICE_KEY, choice)
    window.sessionStorage.removeItem(LOGGED_OUT_KEY)
  } catch {
    // Not remembered: the page asks again next time.
  }
}

/** After "Log out" this tab stays on the login page: going straight back to Wiedisync would log in again. */
export function markLoggedOut(): void {
  try {
    window.sessionStorage.setItem(LOGGED_OUT_KEY, '1')
  } catch {
    // Without storage the page shows the choice anyway (rememberedLogin is null then too).
  }
}

function loggedOutHere(): boolean {
  try {
    return window.sessionStorage.getItem(LOGGED_OUT_KEY) === '1'
  } catch {
    return true
  }
}

/** Leaving the app for the Wiedisync login (one place, so tests can watch it). */
export const browser = {
  assign: (url: string): void => window.location.assign(url),
}

/** KSCW members who chose Wiedisync last time go there without a click, unless they just logged out. */
export function goesStraightToWiedisync(): boolean {
  return rememberedLogin() === 'wiedisync' && !loggedOutHere()
}

/**
 * A guest club's member is logged in: the app hides what is KSCW's own (the helpers' guide).
 * Reads the session AppLayout loaded, without asking the API again.
 */
export function useIsGuest(): boolean {
  const client = useQueryClient()
  return client.getQueryData<Me | null>(keys.me)?.guest === true
}

// --- a guest team's people --------------------------------------------------------------------

export function useTeamPeople(teamId: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.teamPeople(teamId),
    queryFn: ({ signal }) => api.teamPeople(teamId, signal),
    enabled,
  })
}

function usePeopleRefresh(teamId: number) {
  const client = useQueryClient()
  return async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.teamPeople(teamId) }),
      client.invalidateQueries({ queryKey: keys.guestTeams }),
    ])
  }
}

export function useAddPerson(teamId: number) {
  const refresh = usePeopleRefresh(teamId)
  return useMutation({ mutationFn: (body: GuestPersonIn) => api.addPerson(teamId, body), onSuccess: refresh })
}

export function useEditPerson(teamId: number) {
  const refresh = usePeopleRefresh(teamId)
  return useMutation({
    mutationFn: ({ memberId, body }: { memberId: number; body: Partial<GuestPersonIn> }) =>
      api.editPerson(teamId, memberId, body),
    onSuccess: refresh,
  })
}

export function useRemovePerson(teamId: number) {
  const refresh = usePeopleRefresh(teamId)
  return useMutation({ mutationFn: (memberId: number) => api.removePerson(teamId, memberId), onSuccess: refresh })
}

/** A new invite link (shown once); the person's older links stop working. */
export function useInvitePerson(teamId: number) {
  const refresh = usePeopleRefresh(teamId)
  return useMutation({ mutationFn: (memberId: number) => api.invitePerson(teamId, memberId), onSuccess: refresh })
}

/** The coach's "everyone we film is 18 or older": uploads open once it is given. */
export function useDeclareAdults(teamId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.declareAdults(teamId),
    onSuccess: ({ adults_declared_at }) => {
      client.setQueryData<TeamDetail>(keys.team(teamId), (team) => (team ? { ...team, adults_declared_at } : team))
      void client.invalidateQueries({ queryKey: ['teams', teamId, 'filming'] })
    },
  })
}

// --- seasons: a guest team's coaches start its next season themselves ---------------------------

/** People of the team's earlier seasons who are not on this one: its coaches take them along. */
export function useEarlierPeople(teamId: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.earlierPeople(teamId),
    queryFn: ({ signal }) => api.earlierPeople(teamId, signal),
    enabled,
  })
}

export function useTakeAlong(teamId: number) {
  const refresh = usePeopleRefresh(teamId)
  return useMutation({ mutationFn: (memberIds: number[]) => api.takeAlong(teamId, memberIds), onSuccess: refresh })
}

/** A new team row for the next season, with the people picked; everything that lists teams changes. */
export function useStartNextSeason(teamId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: NextSeasonIn) => api.startNextSeason(teamId, body),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.me }),
        client.invalidateQueries({ queryKey: keys.teams }),
        client.invalidateQueries({ queryKey: keys.guestTeams }),
      ])
    },
  })
}

/** The season a day belongs to: the new one starts on 1 June ("2026/27"). */
export function seasonOf(iso: string): string {
  const year = Number(iso.slice(0, 4))
  const start = Number(iso.slice(5, 7)) >= 6 ? year : year - 1
  return `${start}/${String(start + 1).slice(2)}`
}

// --- admins: the guest clubs ------------------------------------------------------------------

export function useGuestTeams() {
  return useQuery({ queryKey: keys.guestTeams, queryFn: ({ signal }) => api.guestTeams(signal) })
}

export function useCreateGuestTeam() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: GuestTeamCreate) => api.createGuestTeam(body),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.guestTeams }),
        client.invalidateQueries({ queryKey: keys.teams }),
        client.invalidateQueries({ queryKey: keys.me }),
      ])
    },
  })
}

export function useUpdateGuestTeam() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ team, body }: { team: GuestTeam; body: GuestTeamUpdate }) => api.updateGuestTeam(team.id, body),
    onSuccess: (updated) => {
      client.setQueryData<GuestTeam[]>(keys.guestTeams, (list) => list?.map((t) => (t.id === updated.id ? updated : t)))
      void client.invalidateQueries({ queryKey: keys.teams })
      void client.invalidateQueries({ queryKey: keys.team(updated.id) })
      void client.invalidateQueries({ queryKey: keys.me })
    },
  })
}

export function useInviteCoach(teamId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; number: number | null }) => api.inviteCoach(teamId, body),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.guestTeams }),
        client.invalidateQueries({ queryKey: keys.teamPeople(teamId) }),
      ])
    },
  })
}

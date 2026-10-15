import { useOutletContext } from 'react-router'
import type { Me } from './types'

export interface Session {
  me: Me
}

/** The logged-in user; only valid inside the routes rendered by AppLayout. */
export function useSession(): Me {
  return useOutletContext<Session>().me
}

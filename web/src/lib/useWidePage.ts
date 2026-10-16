import { useMatches } from 'react-router'

/** Pages that use the full window width declare `handle: { wide: true }` on their route. */
export function useWidePage(): boolean {
  return useMatches().some((match) => (match.handle as { wide?: boolean } | undefined)?.wide === true)
}

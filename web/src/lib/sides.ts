// The teams' names where the app would say us and them: who served and who won a rally, the
// score line, the sets, the stats. The server works them out (backend nomal/core/names.py:
// "KSCW H3" from the club and the team, the opponent from the Wiedisync game or the video's
// title, or what a coach set in the video's setup). The keyboard and the data stay us/them
// (`a` starts an opponent's tag); only the words change.

import type { TFunction } from 'i18next'
import { createContext, useContext } from 'react'
import type { Side, Video } from './types'

export interface SideNames {
  /** "KSCW H3" */
  us: string
  /** "Volero"; "Opponent" when nobody knows. */
  them: string
  /** The official names (tooltips). */
  usFull: string
  themFull: string
}

/** A video's names; null from an older server without them (the app then says "Us" and "Them"). */
export function sideNamesOf(video: Pick<Video, 'names'>, t: TFunction): SideNames | null {
  const names = video.names
  if (!names) return null
  const them = names.them ?? t('sides.opponent')
  return { us: names.us, them, usFull: names.us_full || names.us, themFull: names.them_full ?? them }
}

/** The names of the video on screen (the video page, the tagging workspace); null elsewhere. */
export const SideNamesContext = createContext<SideNames | null>(null)

export function useSideNames(): SideNames | null {
  return useContext(SideNamesContext)
}

export function nameOf(names: SideNames, side: Side): string {
  return side === 'us' ? names.us : names.them
}

/** The official name behind a short one, for a tooltip; undefined when they are the same. */
export function fullNameOf(names: SideNames, side: Side): string | undefined {
  const full = side === 'us' ? names.usFull : names.themFull
  return full !== nameOf(names, side) ? full : undefined
}

/** "KSCW H3 – Volero": the order of every score shown (ours first). */
export function matchup(names: SideNames): string {
  return `${names.us} – ${names.them}`
}

/** "KSCW H3 12:10 Volero". */
export function namedScore(names: SideNames, us: number, them: number): string {
  return `${names.us} ${us}:${them} ${names.them}`
}

import { createContext, useContext, useSyncExternalStore } from 'react'
import type { Outbox, OutboxState } from '../../lib/outbox'
import type { Playhead } from '../../lib/playhead'
import type { TaggingStore, TaggingView } from '../../lib/taggingStore'
import type { Tagging, Taxonomy, Video } from '../../lib/types'
import type { TagController, UiState } from './controller'
import type { ReviewController, ReviewState } from './review'

export interface WorkspaceParts {
  video: Video
  bundle: Tagging
  taxonomy: Taxonomy
  store: TaggingStore
  outbox: Outbox
  playhead: Playhead
  controller: TagController
  review: ReviewController
  /** A narrow screen (tablet, phone): only the review of suggestions, by touch. */
  narrow: boolean
  /** Reloads the server's data under the queued changes (after the server changed rallies itself). */
  reload: () => void
}

export const WorkspaceContext = createContext<WorkspaceParts | null>(null)

export function useWorkspace(): WorkspaceParts {
  const parts = useContext(WorkspaceContext)
  if (!parts) throw new Error('useWorkspace must be used inside the tagging workspace')
  return parts
}

/** Tags, rallies, sets and roster as shown (server data with queued changes on top). */
export function useView(): TaggingView {
  const { store } = useWorkspace()
  return useSyncExternalStore(store.subscribe, store.getView)
}

export function useUi(): UiState {
  const { controller } = useWorkspace()
  return useSyncExternalStore(controller.subscribe, controller.getState)
}

export function useSync(): OutboxState {
  const { outbox } = useWorkspace()
  return useSyncExternalStore(outbox.subscribe, outbox.getState)
}

export function useReview(): ReviewState {
  const { review } = useWorkspace()
  return useSyncExternalStore(review.subscribe, review.getState)
}

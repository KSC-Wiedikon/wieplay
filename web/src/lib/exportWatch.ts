// Says when a download someone asked for is ready (docs/PLAN.md §7.7): once an export was queued
// in this tab (EXPORT_QUEUED), the list of exports is fetched every few seconds while one is
// being made (not at all before), and each that turns ready (or fails) gets a toast, on whatever
// page the coach is.

import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { EXPORT_QUEUED, libraryApi } from './library'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { VideoExport } from './types'

const POLL_MS = 5_000

/** What changed between two looks at the exports: those that were being made and now are not. */
export function finished(before: ReadonlyMap<string, string>, now: readonly VideoExport[]): VideoExport[] {
  return now.filter((e) => before.get(e.id) === 'queued' && (e.status === 'ready' || e.status === 'failed'))
}

export function useExportNotifications(): void {
  const { t } = useTranslation()
  const toast = useToast()
  const [watching, setWatching] = useState(false)
  useEffect(() => {
    const start = () => setWatching(true)
    window.addEventListener(EXPORT_QUEUED, start)
    return () => window.removeEventListener(EXPORT_QUEUED, start)
  }, [])
  const exports = useQuery({
    queryKey: keys.exports,
    queryFn: ({ signal }) => libraryApi.exports(signal),
    enabled: watching,
    refetchInterval: (query) => (query.state.data?.some((e) => e.status === 'queued') ? POLL_MS : false),
  })
  const seen = useRef(new Map<string, string>())
  useEffect(() => {
    const list = exports.data
    if (!list) return
    for (const item of finished(seen.current, list)) {
      if (item.status === 'failed') toast.error(t('notify.exportFailed', { title: item.title }))
      else toast.success(t(item.kind === 'recap' ? 'notify.recapReady' : 'notify.exportReady', { title: item.title }))
    }
    seen.current = new Map(list.map((e) => [e.id, e.status]))
  }, [exports.data, t, toast])
}

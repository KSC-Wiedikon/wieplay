import '../i18n/pages'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertIcon, CheckIcon, ChevronRightIcon, PlusIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { PageSpinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { TabPanel, Tabs } from '../components/Tabs'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { sideLabel } from '../lib/labels'
import { useMediaQuery } from '../lib/mediaQuery'
import { useVideo } from '../lib/queries'
import {
  checkScoresheet,
  decode,
  emptySet,
  normalised,
  other,
  scoresheetKey,
  useDeleteScoresheet,
  useSaveScoresheet,
  useScoresheet,
  type ChecksOut,
  type Scoresheet,
  type SheetSetDoc,
} from '../lib/scoresheet'
import { SideNamesContext, sideNamesOf, useSideNames } from '../lib/sides'
import { readStorage, writeStorage } from '../lib/storage'
import { useToast } from '../lib/toast'
import type { Video } from '../lib/types'
import { isUuid } from '../lib/video'
import { SetPanel } from './sheet/SetPanel'
import { PhotoPrompt, SheetPhotos } from './sheet/SheetPhotos'
import { SheetStats } from './sheet/SheetStats'

/**
 * /videos/:id/scoresheet — the match's official scoresheet, typed in (docs/PLAN.md §10.3a): per
 * set the first server, both lineups and every service box in the sheet's own order, with a live
 * check on every keystroke; timeouts, substitutions, sanctions; the sheet's own numbers. Saving
 * scores the match again: the sheet's points are lined up with the video's rallies.
 */
export function ScoresheetPage() {
  const { id } = useParams()
  const { t } = useTranslation()
  if (!isUuid(id)) return <Message>{t('video.notFound')}</Message>
  return <ScoresheetView videoId={id} />
}

function ScoresheetView({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  const video = useVideo(videoId)
  const volleyball = video.data?.sport === 'volleyball'
  const sheet = useScoresheet(videoId, volleyball)
  const [tab, setTab] = useState(1)
  if (video.isPending || (volleyball && sheet.isPending)) return <PageSpinner />
  if (video.isError || !video.data) {
    if (video.error instanceof ApiError && [403, 404, 422].includes(video.error.status)) return <Message>{t('video.notFound')}</Message>
    return <LoadError onRetry={() => void video.refetch()} />
  }
  const v = video.data
  if (!volleyball) return <Message>{t('sheet.notVolleyball')}</Message>
  if (sheet.isError || !sheet.data) {
    if (sheet.error instanceof ApiError && sheet.error.status === 404) return <Message>{t('video.notFound')}</Message>
    return <LoadError onRetry={() => void sheet.refetch()} />
  }
  const data = sheet.data
  return (
    <SideNamesContext value={sideNamesOf(v, t)}>
      <div className="flex flex-col gap-6 pb-28">
        <title>{`${t('sheet.title')} · ${v.title} · WiePlay`}</title>
        <PageHeader
          eyebrow={
            <>
              <Link to={`/videos/${v.id}`} className="font-semibold link">
                {v.title}
              </Link>
              <ChevronRightIcon className="size-4" />
            </>
          }
          title={t('sheet.title')}
          subtitle={
            <div className="flex max-w-3xl flex-col gap-1 text-sm">
              <p>{t('sheet.intro')}</p>
              {data.exists && data.entered_by && data.entered_at && (
                <p>
                  {t('sheet.enteredBy', { name: data.entered_by, date: formatDateTime(data.entered_at) })}
                  {data.updated_by && data.updated_at && data.updated_at !== data.entered_at
                    ? `; ${t('sheet.updatedBy', { name: data.updated_by, date: formatDateTime(data.updated_at) })}`
                    : ''}
                </p>
              )}
            </div>
          }
        />
        {v.status !== 'ready' ? (
          <Message>{t('sheet.notReady')}</Message>
        ) : (
          // Keyed by the saved version: a save, a reload or a removal starts from the server's sheet.
          // A photo added before anything is typed changes no typed sheet: the form stays as typed.
          <SheetEditor key={`${data.exists ? data.version : 'new'}-${data.exists}`} video={v} sheet={data} tab={tab} onTab={setTab} />
        )}
      </div>
    </SideNamesContext>
  )
}

interface Draft {
  sets: SheetSetDoc[]
  use_in_scoring: boolean
  base_version: number
}

function draftKey(videoId: string): string {
  return `wieplay-sheet-draft-${videoId}`
}

function readDraft(videoId: string): Draft | null {
  try {
    const raw = readStorage(draftKey(videoId))
    return raw ? (JSON.parse(raw) as Draft) : null
  } catch {
    return null
  }
}

function clearDraft(videoId: string) {
  try {
    window.localStorage.removeItem(draftKey(videoId))
  } catch {
    // nothing kept anyway
  }
}

/** The sets to start typing: the saved ones, else one per official set with our last six. */
function initialSets(sheet: Scoresheet): SheetSetDoc[] {
  if (sheet.exists && sheet.sets.length > 0) return sheet.sets.map((s) => normalised(s))
  const count = Math.max(1, Math.min(5, sheet.official_sets?.length ?? 3))
  return Array.from({ length: count }, (_, k) => emptySet(k + 1, sheet.previous_lineup))
}

function SheetEditor({ video, sheet, tab, onTab }: { video: Video; sheet: Scoresheet; tab: number; onTab: (no: number) => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const names = useSideNames()
  const client = useQueryClient()
  const save = useSaveScoresheet(video.id)
  const remove = useDeleteScoresheet(video.id)
  const start = useMemo(() => ({ sets: initialSets(sheet), use: sheet.use_in_scoring }), [sheet])
  const [sets, setSets] = useState<SheetSetDoc[]>(start.sets)
  const [useAll, setUseAll] = useState(start.use)
  const [stale, setStale] = useState(false)
  const [askRemove, setAskRemove] = useState(false)
  const [askRemoveSet, setAskRemoveSet] = useState<number | null>(null)
  const readOnly = !sheet.can_edit
  const body = JSON.stringify({ sets, useAll })
  const dirty = body !== JSON.stringify({ sets: start.sets, useAll: start.use }) || !sheet.exists

  // Unsaved changes stay in this browser (a reload, a lost connection, someone saving meanwhile).
  const [draft, setDraft] = useState<Draft | null>(() => {
    const found = readDraft(video.id)
    return found && JSON.stringify(found.sets) !== JSON.stringify(start.sets) ? found : null
  })
  const touched = useRef(false)
  useEffect(() => {
    if (!touched.current || readOnly) return
    writeStorage(draftKey(video.id), JSON.stringify({ sets, use_in_scoring: useAll, base_version: sheet.version } satisfies Draft))
  }, [body, sets, useAll, video.id, sheet.version, readOnly])

  // The server's checks of the sheet as typed (its repairs and numbers), a moment after typing.
  const [server, setServer] = useState<{ body: string; out: ChecksOut } | null>(null)
  useEffect(() => {
    if (!dirty || readOnly) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      checkScoresheet(video.id, { base_version: sheet.version, sets, use_in_scoring: useAll }, controller.signal)
        .then((out) => setServer({ body, out }))
        .catch(() => undefined) // the live check in the browser still runs
    }, 600)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [body, dirty, readOnly, sets, useAll, video.id, sheet.version])
  const checks: ChecksOut | null = !dirty ? sheet : server?.body === body ? server.out : null

  const change = (index: number, next: SheetSetDoc) => {
    touched.current = true
    setSets((all) => {
      const out = all.map((s, i) => (i === index ? next : s))
      // The first serve alternates in sets 1-4: fill in the sets nobody set yet.
      const first = out.find((s) => s.no === 1)?.first_serve
      if (next.no === 1 && first) {
        return out.map((s) => (s.no >= 2 && s.no <= 4 && s.first_serve === null ? { ...s, first_serve: s.no % 2 === 1 ? first : other(first) } : s))
      }
      return out
    })
  }
  const addSet = () => {
    touched.current = true
    const no = (sets[sets.length - 1]?.no ?? 0) + 1
    const last = sets[sets.length - 1]
    const fresh = emptySet(no, last?.lineup_us ?? sheet.previous_lineup)
    fresh.lineup_them = last?.lineup_them ? [...last.lineup_them] : fresh.lineup_them
    setSets([...sets, fresh])
    onTab(no)
  }
  const removeSet = (no: number) => {
    touched.current = true
    const rest = sets.filter((s) => s.no !== no)
    setSets(rest)
    onTab(rest[rest.length - 1]?.no ?? 1)
  }

  const onSave = () => {
    save.mutate(
      { base_version: sheet.version, sets, use_in_scoring: useAll },
      {
        onSuccess: () => {
          clearDraft(video.id)
          toast.success(t('sheet.saved'))
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) setStale(true)
          else toast.error(t('sheet.saveFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') }))
        },
      },
    )
  }

  const current = sets.find((s) => s.no === tab) ?? sets[0]
  const currentIndex = current ? sets.indexOf(current) : -1
  const official = (no: number): [number, number] | null => sheet.official_sets?.[no - 1] ?? null
  const status = (s: SheetSetDoc) => decode(s, official(s.no))
  const savedChecks = new Map(sheet.checks.map((c) => [c.no, c]))
  const serverChecks = new Map((checks?.checks ?? []).map((c) => [c.no, c]))

  // The photo of the paper sheet (SS3): beside the form on wide screens, a strip above the set
  // tabs on phones; for the team's coaches and admins only (`images` is null for everyone else).
  const photos = sheet.images
  const wide = useMediaQuery('(min-width: 80rem)', true)
  const side = wide && photos !== null && photos.length > 0
  const setNo = current?.no ?? 1
  const photoProps = { videoId: video.id, set: setNo, sets: Math.max(sets.length, sheet.official_sets?.length ?? 0) }

  return (
    <div className={cx(side && 'grid grid-cols-2 items-start gap-6')}>
      {side && photos && (
        <aside className="sticky top-4 h-[calc(100dvh-2rem)] min-h-96">
          <SheetPhotos {...photoProps} photos={photos} canAdd={sheet.can_add_images} variant="side" />
        </aside>
      )}
      <div className="@container flex min-w-0 flex-col gap-6">
        {readOnly && <Message>{t('sheet.readOnly')}</Message>}
        {draft && !readOnly && (
          <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-accent bg-accent-soft/40 p-3 text-sm">
            <span className="font-semibold">{t('sheet.draft.found')}</span>
            <Button
              variant="primary"
              onClick={() => {
                touched.current = true
                setSets(draft.sets.map((s) => normalised(s)))
                setUseAll(draft.use_in_scoring)
                setDraft(null)
              }}
            >
              {t('sheet.draft.restore')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                clearDraft(video.id)
                setDraft(null)
              }}
            >
              {t('sheet.draft.discard')}
            </Button>
          </div>
        )}
        {stale && (
          <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-danger bg-danger/5 p-3 text-sm">
            <AlertIcon className="size-5 shrink-0 text-danger" />
            <span>{t('sheet.stale')}</span>
            <Button variant="secondary" onClick={() => void client.invalidateQueries({ queryKey: scoresheetKey(video.id) })}>
              {t('sheet.reload')}
            </Button>
          </div>
        )}
        {sheet.conflicts.length > 0 && (
          <section className="flex flex-col gap-1 rounded-xl border border-hairline bg-sunken p-3 text-sm">
            <h2 className="font-semibold">{t('sheet.conflicts.title')}</h2>
            <ul className="list-disc pl-5">
              {sheet.conflicts.map((c, k) => (
                <li key={k}>
                  {t(`sheet.conflicts.${c.field as 'first_serve' | 'start_lineup' | 'start_lineup_them'}`, {
                    set: c.set,
                    coach: sideLabel(t, c.coach === 'us' || c.coach === 'them' ? c.coach : null, names),
                    sheet: sideLabel(t, c.sheet === 'us' || c.sheet === 'them' ? c.sheet : null, names),
                  })}
                </li>
              ))}
            </ul>
          </section>
        )}

        {photos !== null &&
          !side &&
          (photos.length > 0 ? (
            <SheetPhotos {...photoProps} photos={photos} canAdd={sheet.can_add_images} variant="strip" />
          ) : sheet.can_add_images ? (
            <PhotoPrompt {...photoProps} />
          ) : null)}

        <Card className="flex flex-col gap-5 p-4">
          <div className="flex flex-wrap items-end gap-2">
            <Tabs
              label={t('sheet.sets')}
              id="sheet-sets"
              value={String(current?.no ?? 1)}
              className="min-w-0 flex-1"
              options={sets.map((s) => {
                const d = status(s)
                const bad = d.issues.some((i) => i.severity === 'error')
                return {
                  value: String(s.no),
                  label: (
                    <span className="inline-flex items-center gap-1.5">
                      {t('sheet.set', { no: s.no })}
                      {d.consistent ? (
                        <CheckIcon className="size-4 text-on-accent-soft" aria-label={t('sheet.status.consistent')} />
                      ) : bad ? (
                        <AlertIcon className="size-4 text-danger" aria-label={t('sheet.status.inconsistent')} />
                      ) : null}
                    </span>
                  ),
                }
              })}
              onChange={(value) => onTab(Number(value))}
            />
            {!readOnly && sets.length < 5 && (
              <Button variant="ghost" onClick={addSet} aria-label={t('sheet.addSet', { no: (sets[sets.length - 1]?.no ?? 0) + 1 })}>
                <PlusIcon className="size-4" />
                <span className="hidden sm:inline">{t('sheet.addSet', { no: (sets[sets.length - 1]?.no ?? 0) + 1 })}</span>
              </Button>
            )}
          </div>
          {current && (
            <TabPanel id="sheet-sets" value={String(current.no)}>
              <SetPanel
                key={current.no}
                set={current}
                names={names}
                official={official(current.no)}
                server={serverChecks.get(current.no) ?? null}
                saved={!dirty ? (savedChecks.get(current.no) ?? null) : null}
                readOnly={readOnly}
                onChange={(next) => change(currentIndex, next)}
                onRemove={() => setAskRemoveSet(current.no)}
              />
            </TabPanel>
          )}
        </Card>

        <SheetStats match={checks?.match ?? null} checks={checks?.checks ?? []} bySetter={checks?.match_by_setter ?? false} names={names} />

        <details className="hidden text-sm sm:block">
          <summary className="cursor-pointer font-semibold text-muted">{t('sheet.keys.title')}</summary>
          <dl className="mt-2 grid max-w-xl grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-muted">
            {(
              [
                ['Tab · Space · Enter · →', t('sheet.keys.next')],
                ['⇧ Tab · ← · Backspace', t('sheet.keys.back')],
                ['Enter', t('sheet.keys.other')],
                ['↑ ↓', t('sheet.keys.upDown')],
                ['*', t('sheet.keys.circle')],
              ] as const
            ).map(([key, text]) => (
              <div key={key} className="contents">
                <dt>
                  <kbd className="rounded-md border border-hairline bg-sunken px-1.5 py-px font-mono text-[11px] font-semibold whitespace-nowrap text-fg">{key}</kbd>
                </dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
        </details>

        {!readOnly && (
          <Card className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 p-4">
            <span className="flex items-center gap-2">
              <Switch
                checked={useAll}
                label={t('sheet.useAll')}
                describedBy="sheet-use-all"
                onChange={(on) => {
                  touched.current = true
                  setUseAll(on)
                }}
              />
              <span className="flex flex-col">
                <span className="text-sm font-semibold">{t('sheet.useAll')}</span>
                <span id="sheet-use-all" className="text-xs text-muted">
                  {t('sheet.useAllHint')}
                </span>
              </span>
            </span>
            {sheet.exists && (
              <Button variant="ghostDanger" onClick={() => setAskRemove(true)}>
                <TrashIcon className="size-4" />
                {t('sheet.remove')}
              </Button>
            )}
          </Card>
        )}

        {!readOnly && (
          // Saving stays in reach while typing a long set.
          <div className="sticky bottom-0 z-10 -mx-4 flex items-center gap-4 border-t border-hairline bg-surface/95 px-4 py-2.5 shadow-raised backdrop-blur sm:mx-0 sm:rounded-card sm:border">
            <Button variant="primary" disabled={!dirty || save.isPending} onClick={onSave}>
              {save.isPending ? t('sheet.saving') : t('sheet.save')}
            </Button>
            <span className={cx('min-w-0 text-sm', dirty ? 'font-semibold' : 'text-muted')} role="status">
              {dirty ? t('sheet.unsaved') : t('sheet.upToDate')}
              {!dirty && sheet.trusted ? ` · ${t('sheet.trusted')}` : ''}
            </span>
          </div>
        )}

        <ConfirmDialog
          open={askRemove}
          title={t('sheet.removeTitle')}
          confirmLabel={t('sheet.remove')}
          onCancel={() => setAskRemove(false)}
          onConfirm={() =>
            remove.mutate(undefined, {
              onSuccess: () => {
                clearDraft(video.id)
                setAskRemove(false)
                toast.success(t('sheet.removed'))
              },
              onError: (error) => toast.error(t('sheet.saveFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
            })
          }
        >
          {t('sheet.removeBody')}
          {photos && photos.length > 0 ? ` ${t('sheet.removePhotos', { count: photos.length })}` : ''}
        </ConfirmDialog>
        <ConfirmDialog
          open={askRemoveSet !== null}
          title={t('sheet.removeSetTitle', { no: askRemoveSet ?? 0 })}
          confirmLabel={t('sheet.removeSet', { no: askRemoveSet ?? 0 })}
          onCancel={() => setAskRemoveSet(null)}
          onConfirm={() => {
            if (askRemoveSet !== null) removeSet(askRemoveSet)
            setAskRemoveSet(null)
          }}
        >
          {t('sheet.removeSetBody')}
        </ConfirmDialog>
      </div>
    </div>
  )
}

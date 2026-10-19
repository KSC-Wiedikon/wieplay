import '../i18n/pages'
import { useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertIcon, CheckIcon, ChevronRightIcon, MergeIcon, SparkleIcon, SplitIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { PageHeader, SectionHeader } from '../components/PageHeader'
import { ProgressBar } from '../components/ProgressBar'
import { PageSpinner } from '../components/Spinner'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { useIdentifyQueue } from '../lib/identify'
import { formatClock } from '../lib/labels'
import {
  clusterTitle,
  cropStyle,
  groupClusters,
  parseNumber,
  sheetUrl,
  useApplySuggestions,
  useAssignPlayer,
  useFindPlayers,
  useSearchTime,
  useMergePlayers,
  usePlayers,
  useSplitPlayer,
  useSwapTeams,
  type PlayerCluster,
  type Players,
  type Sheets,
} from '../lib/players'
import { useVideo } from '../lib/queries'
import { SideNamesContext, sideNamesOf, useSideNames } from '../lib/sides'
import { formatPercent } from '../lib/suggestions'
import { useToast } from '../lib/toast'
import { isUuid } from '../lib/video'

/**
 * /videos/:id/players — "Players in this video" (docs/PLAN.md §10.5): one card per person WiePlay
 * found, with pictures of their back, the actions attributed to them and the number a coach
 * gives them once. Merge, split, "not a player", swap teams. Keyboard: type a number, Enter
 * saves it and moves to the next card.
 */
export function PlayersPage() {
  const { id } = useParams()
  const { t } = useTranslation()
  if (!isUuid(id)) return <Message>{t('video.notFound')}</Message>
  return <PlayersView videoId={id} />
}

function PlayersView({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  const video = useVideo(videoId)
  const players = usePlayers(videoId, video.data?.status === 'ready')
  if (video.isPending || (video.data?.status === 'ready' && players.isPending)) return <PageSpinner />
  if (video.isError || !video.data) {
    if (video.error instanceof ApiError && [403, 404, 422].includes(video.error.status)) return <Message>{t('video.notFound')}</Message>
    return <LoadError onRetry={() => void video.refetch()} />
  }
  const v = video.data
  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('players.title')} · ${v.title} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <>
            <Link to={`/videos/${v.id}`} className="font-semibold link">
              {v.title}
            </Link>
            <ChevronRightIcon className="size-4" />
          </>
        }
        title={
          <span className="inline-flex flex-wrap items-center gap-3">
            {t('players.title')}
            <Badge>{t('players.beta')}</Badge>
          </span>
        }
        subtitle={<p className="max-w-3xl text-sm">{t('players.intro')}</p>}
      />
      {players.isError ? (
        <LoadError onRetry={() => void players.refetch()} />
      ) : players.data ? (
        <SideNamesContext value={sideNamesOf(v, t)}>
          <Gallery videoId={v.id} data={players.data} />
        </SideNamesContext>
      ) : (
        <Message>{t('players.status.none')}</Message>
      )}
    </div>
  )
}

function Gallery({ videoId, data }: { videoId: string; data: Players }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const toast = useToast()
  const find = useFindPlayers(videoId)
  const swap = useSwapTeams(videoId)
  const merge = useMergePlayers(videoId)
  const apply = useApplySuggestions(videoId)
  const [selected, setSelected] = useState<string[]>([])
  const [splitting, setSplitting] = useState<PlayerCluster | null>(null)
  const [askSwap, setAskSwap] = useState(false)
  const grouped = useMemo(() => groupClusters(data.clusters), [data.clusters])
  // The order of the number fields: Enter moves to the next card.
  const order = useMemo(() => [...grouped.us, ...grouped.them, ...grouped.check, ...grouped.brief, ...grouped.others].map((c) => c.id), [grouped])
  const byId = useMemo(() => new Map(data.clusters.map((c) => [c.id, c])), [data.clusters])
  const chosen = selected.map((id) => byId.get(id)).filter((c): c is PlayerCluster => c !== undefined)
  const oneTeam = chosen.every((c) => c.team === chosen[0]?.team)
  const busy = data.status === 'queued' || data.status === 'running'
  const people = data.clusters.filter((c) => !c.not_player)
  const named = people.filter((c) => c.number !== null).length
  const check = useIdentifyQueue(videoId, data.run !== null).data?.items.length ?? 0

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const doMerge = () => {
    const [into, ...sources] = selected
    if (!into || sources.length === 0) return
    merge.mutate({ into, sources }, { onSuccess: () => setSelected([]) })
  }

  return (
    <>
      {data.run ? (
        <Card className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 p-4" role="region" aria-label={t('players.setup.title')}>
          <div className="flex min-w-0 flex-[1_1_20rem] flex-col gap-1.5">
            <h2 className="font-semibold">{t('players.setup.title')}</h2>
            <p className="text-2xl font-bold tabular-nums">
              {data.attribution && data.attribution.actions > 0
                ? t('players.setup.identified', { value: formatPercent(data.attribution.identified_share) })
                : t('players.setup.identifiedNone')}
            </p>
            {data.attribution && data.attribution.actions > 0 && (
              <ProgressBar value={data.attribution.identified_share ?? 0} label={t('players.setup.title')} />
            )}
            <p className="text-sm font-semibold">
              {t('players.cards', {
                count: people.length,
                us: people.filter((c) => c.team === 'us').length,
                them: people.filter((c) => c.team === 'them').length,
              })}
            </p>
            <p className="text-sm text-muted">
              {t('players.setup.named', { count: named, total: people.length })} · {t('players.cardsHint')}
            </p>
            {data.attribution?.vs_coach.accuracy != null && (
              <p className="text-sm text-muted">
                {t('players.setup.accuracy', {
                  value: formatPercent(data.attribution.vs_coach.accuracy),
                  count: data.attribution.vs_coach.compared,
                })}
              </p>
            )}
          </div>
          {data.can_edit && (
            <div className="flex flex-[1_1_16rem] flex-col items-start gap-2">
              {data.open_suggestions > 0 && (
                <>
                  <Button
                    variant="primary"
                    disabled={apply.isPending}
                    onClick={() =>
                      apply.mutate(undefined, {
                        onSuccess: () => toast.success(t('players.setup.applied', { count: data.open_suggestions })),
                      })
                    }
                  >
                    <SparkleIcon className="size-4" />
                    {t('players.setup.apply', { count: data.open_suggestions })}
                  </Button>
                  <p className="text-sm text-muted">{t('players.setup.applyHint')}</p>
                </>
              )}
              <div className="flex flex-wrap gap-2">
                {check > 0 && (
                  <ButtonLink to={`/videos/${videoId}/identify`} variant="primary">
                    {t('identify.open', { count: check })}
                  </ButtonLink>
                )}
                <Button onClick={() => setAskSwap(true)} disabled={swap.isPending} title={t('players.swapHint')}>
                  {t('players.swap')}
                </Button>
                {(data.can_find ?? true) && (
                  <Button variant="ghost" onClick={() => find.mutate()} disabled={find.isPending || busy} title={t('players.findAgainHint')}>
                    {t('players.findAgain')}
                  </Button>
                )}
              </div>
            </div>
          )}
        </Card>
      ) : data.can_edit && data.open_suggestions > 0 ? (
        // No players found yet, but the scoresheet names the servers of tagged serves.
        <Card className="flex flex-col items-start gap-2 p-4" role="region" aria-label={t('players.setup.title')}>
          <Button
            variant="primary"
            disabled={apply.isPending}
            onClick={() => apply.mutate(undefined, { onSuccess: () => toast.success(t('players.setup.applied', { count: data.open_suggestions })) })}
          >
            <SparkleIcon className="size-4" />
            {t('players.setup.apply', { count: data.open_suggestions })}
          </Button>
          <p className="text-sm text-muted">{t('players.setup.applySheet')}</p>
        </Card>
      ) : null}

      <JobState data={data} onFind={() => find.mutate()} finding={find.isPending} />

      {data.can_edit && selected.length > 0 && (
        <div
          role="region"
          aria-label={t('players.merge')}
          className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-card border border-accent bg-accent-soft p-3 shadow-card"
        >
          <p className="font-semibold">{t('players.selected', { count: selected.length })}</p>
          <Button variant="primary" disabled={selected.length < 2 || !oneTeam || merge.isPending} onClick={doMerge}>
            <MergeIcon className="size-4" />
            {t('players.merge')}
          </Button>
          <Button variant="ghost" onClick={() => setSelected([])}>
            {t('players.clear')}
          </Button>
          <p className="basis-full text-sm">{oneTeam ? t('players.mergeHint') : t('players.mergeTeams')}</p>
        </div>
      )}

      {data.run && data.sheets && (
        <>
          <TeamSection title={names?.us ?? t('players.us')} clusters={grouped.us} {...{ data, order, selected, toggle, setSplitting, videoId }} sheets={data.sheets} />
          <TeamSection title={names?.them ?? t('players.them')} clusters={grouped.them} {...{ data, order, selected, toggle, setSplitting, videoId }} sheets={data.sheets} />
          {grouped.check.length > 0 && (
            <details className="group rounded-card border border-hairline bg-surface px-4 shadow-card">
              <summary className="flex min-h-11 cursor-pointer items-center font-semibold select-none">{t('players.toCheck', { count: grouped.check.length })}</summary>
              <p className="pb-2 text-sm text-muted">{t('players.toCheckHint')}</p>
              <CardGrid clusters={grouped.check} {...{ data, order, selected, toggle, setSplitting, videoId }} sheets={data.sheets} />
            </details>
          )}
          {grouped.brief.length > 0 && (
            <details className="group rounded-card border border-hairline bg-surface px-4 shadow-card">
              <summary className="flex min-h-11 cursor-pointer items-center font-semibold select-none">{t('players.brief', { count: grouped.brief.length })}</summary>
              <p className="pb-2 text-sm text-muted">{t('players.briefHint')}</p>
              <CardGrid clusters={grouped.brief} {...{ data, order, selected, toggle, setSplitting, videoId }} sheets={data.sheets} />
            </details>
          )}
          {grouped.others.length > 0 && (
            <details className="group rounded-card border border-hairline bg-surface px-4 shadow-card">
              <summary className="flex min-h-11 cursor-pointer items-center font-semibold select-none">
                {t('players.others')} ({grouped.others.length})
              </summary>
              <p className="pb-2 text-sm text-muted">{t('players.othersHint')}</p>
              <CardGrid clusters={grouped.others} {...{ data, order, selected, toggle, setSplitting, videoId }} sheets={data.sheets} />
            </details>
          )}
        </>
      )}

      <datalist id={`${videoId}-roster`}>
        {data.roster
          .filter((r) => r.side === 'us')
          .map((r) => (
            <option key={r.number} value={r.number}>
              {r.name ?? ''}
            </option>
          ))}
      </datalist>
      {splitting && data.sheets && (
        <SplitDialog videoId={videoId} cluster={splitting} sheets={data.sheets} onClose={() => setSplitting(null)} />
      )}
      <ConfirmDialog
        open={askSwap}
        title={t('players.swapConfirmTitle')}
        confirmLabel={t('players.swap')}
        busy={swap.isPending}
        onCancel={() => setAskSwap(false)}
        onConfirm={() => swap.mutate(undefined, { onSettled: () => setAskSwap(false) })}
      >
        {t('players.swapConfirmBody')}
      </ConfirmDialog>
    </>
  )
}

function JobState({ data, onFind, finding }: { data: Players; onFind: () => void; finding: boolean }) {
  const { t } = useTranslation()
  const time = useSearchTime(data.find_minutes)
  if (data.status === 'done' && data.run) return null
  const failed = data.status === 'failed'
  const busy = data.status === 'queued' || data.status === 'running'
  return (
    <Card className="flex flex-col gap-3 p-4">
      <p role="status" className={cx('flex items-center gap-2 font-semibold', failed && 'text-danger')}>
        {failed && <AlertIcon className="size-5" />}
        {t(`players.status.${data.status}`)}
      </p>
      {busy && data.job?.progress != null && <ProgressBar value={data.job.progress} label={t(`players.status.${data.status}`)} />}
      {busy && data.job?.message && <p className="text-sm text-muted">{data.job.message}</p>}
      {failed && data.job?.error && <p className="text-sm wrap-anywhere text-muted">{data.job.error}</p>}
      {(data.can_find ?? data.can_edit) && !busy && (
        <div>
          <Button variant="primary" onClick={onFind} disabled={finding}>
            {t('players.find', { time })}
          </Button>
          <p className="mt-2 text-sm text-muted">{t('players.findHint', { time })}</p>
        </div>
      )}
    </Card>
  )
}

interface CardsProps {
  videoId: string
  data: Players
  sheets: Sheets
  clusters: PlayerCluster[]
  order: string[]
  selected: string[]
  toggle: (id: string) => void
  setSplitting: (c: PlayerCluster) => void
}

function TeamSection({ title, ...props }: CardsProps & { title: string }) {
  const { t } = useTranslation()
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      <SectionHeader title={`${title} (${props.clusters.length})`} hint={props.data.can_edit ? t('players.numberHint') : undefined} />
      {props.clusters.length > 0 ? <CardGrid {...props} /> : <p className="text-sm text-muted">{t('players.empty')}</p>}
    </section>
  )
}

function CardGrid(props: CardsProps) {
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(17rem,1fr))] gap-3 pb-3">
      {props.clusters.map((c) => (
        // A new number from the server (a reading taken, a merge) resets the field.
        <PlayerCard key={`${c.id}-${c.number ?? ''}`} cluster={c} {...props} />
      ))}
    </ul>
  )
}

function Crop({ videoId, sheets, crop, label, scale = 0.75 }: { videoId: string; sheets: Sheets; crop: [number, number]; label?: string; scale?: number }) {
  return (
    <div
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className="shrink-0 rounded-md bg-sunken bg-no-repeat"
      style={{ backgroundImage: `url(${sheetUrl(videoId, sheets, crop[0])})`, ...cropStyle(sheets, crop, scale) }}
    />
  )
}

function PlayerCard({ cluster: c, videoId, data, sheets, order, selected, toggle, setSplitting }: CardsProps & { cluster: PlayerCluster }) {
  const { t } = useTranslation()
  const assign = useAssignPlayer(videoId)
  const [text, setText] = useState(c.number === null ? '' : String(c.number))
  const [error, setError] = useState<string | null>(null)
  const name = clusterTitle(c, (key) => t('players.unnamed', { key }))
  const inputId = `number-${c.id}`
  const ours = new Set(data.roster.filter((r) => r.side === 'us').map((r) => r.number))
  const isSelected = selected.includes(c.id)

  const focusNext = () => {
    const next = order[order.indexOf(c.id) + 1]
    if (next) document.getElementById(`number-${next}`)?.focus()
  }
  const save = (number: number | null, notPlayer = false) =>
    assign.mutate(
      { id: c.id, number, not_player: notPlayer },
      {
        onSuccess: () => {
          setError(null)
          if (!notPlayer) focusNext()
        },
      },
    )
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const number = parseNumber(text)
    if (number === undefined) return setError(t('players.invalidNumber'))
    if (number !== null && c.team === 'us' && !ours.has(number)) return setError(t('players.notInRoster', { number }))
    if (number === c.number) return focusNext()
    save(number)
  }

  return (
    <li>
      <article
        aria-labelledby={`${c.id}-title`}
        className={cx(
          'flex h-full flex-col gap-3 rounded-card border bg-surface p-3 shadow-card',
          isSelected ? 'border-accent ring-2 ring-accent' : 'border-hairline',
          c.not_player && 'opacity-80',
        )}
      >
        <div role="img" aria-label={t('players.crops', { name })} className="flex gap-1.5 overflow-hidden">
          {c.crops.slice(0, 4).map((crop, i) => (
            <Crop key={`${crop[0]}-${crop[1]}-${i}`} videoId={videoId} sheets={sheets} crop={crop} />
          ))}
        </div>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <h3 id={`${c.id}-title`} className="font-semibold wrap-break-word">
              {name}
            </h3>
            <p className="text-sm text-muted tabular-nums">
              {t('players.actions', { count: c.actions })} · {t('players.seen', { time: formatDuration(c.seconds * 1000) })}
            </p>
          </div>
          {c.number !== null && <CheckIcon className="size-5 shrink-0 text-on-accent-soft" aria-hidden="true" />}
        </div>
        {c.referee_guess && !c.not_player && <Badge>{t('players.refereeGuess')}</Badge>}
        {c.uncertain && (
          <p className="text-sm" title={t('players.uncertainHint')}>
            <Badge tone="danger">{t('players.uncertain')}</Badge> <span className="text-muted">{t('players.uncertainHint')}</span>
          </p>
        )}
        {c.ocr_number !== null && c.number === null && (
          <p className="flex flex-wrap items-center gap-2 text-sm" title={t('players.readsHint')}>
            <Badge tone="accent">{t('players.reads', { number: c.ocr_number })}</Badge>
            {data.can_edit && (c.team === 'them' || ours.has(c.ocr_number)) && (
              <Button variant="ghost" disabled={assign.isPending} onClick={() => save(c.ocr_number)}>
                {t('players.useReading', { number: c.ocr_number })}
              </Button>
            )}
          </p>
        )}
        {data.can_edit && (
          <>
            {!c.not_player && (
              <form onSubmit={onSubmit} className="flex flex-col gap-1" noValidate>
                <label htmlFor={inputId} className="sr-only">
                  {t('players.numberFor', { name })}
                </label>
                <div className="flex gap-2">
                  <input
                    id={inputId}
                    inputMode="numeric"
                    autoComplete="off"
                    placeholder="#"
                    value={text}
                    list={c.team === 'us' ? `${videoId}-roster` : undefined}
                    aria-describedby={`${c.id}-hint`}
                    aria-invalid={error ? true : undefined}
                    onChange={(e) => {
                      setText(e.target.value)
                      setError(null)
                    }}
                    className="min-h-11 w-20 rounded-control border border-hairline-strong bg-surface px-3 text-sm text-fg tabular-nums focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:min-h-9"
                  />
                  <Button type="submit" disabled={assign.isPending}>
                    {t('players.save')}
                  </Button>
                </div>
                <p id={`${c.id}-hint`} className={cx('text-xs', error ? 'font-semibold text-danger' : 'sr-only')}>
                  {error ?? t('players.numberHint')}
                </p>
              </form>
            )}
            <div className="mt-auto flex flex-wrap items-center gap-1 border-t border-hairline pt-2">
              {!c.not_player && (
                <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-control px-2 text-sm font-semibold hover:bg-hover sm:min-h-9">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggle(c.id)}
                    aria-label={t('players.select', { name })}
                    className="size-4 accent-current"
                  />
                  <span aria-hidden="true">{t('players.selectShort')}</span>
                </label>
              )}
              {c.tracks.length > 1 && (
                <Button variant="ghost" onClick={() => setSplitting(c)}>
                  <SplitIcon className="size-4" />
                  {t('players.split')}
                </Button>
              )}
              <Button variant="ghost" className="ml-auto" disabled={assign.isPending} onClick={() => save(null, !c.not_player)}>
                {c.not_player ? t('players.isPlayer') : t('players.notPlayer')}
              </Button>
            </div>
          </>
        )}
      </article>
    </li>
  )
}

function SplitDialog({ videoId, cluster, sheets, onClose }: { videoId: string; cluster: PlayerCluster; sheets: Sheets; onClose: () => void }) {
  const { t } = useTranslation()
  const split = useSplitPlayer(videoId)
  const [picked, setPicked] = useState<number[]>([])
  const name = clusterTitle(cluster, (key) => t('players.unnamed', { key }))
  const valid = picked.length > 0 && picked.length < cluster.tracks.length
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  return (
    <Modal
      open
      size="xl"
      title={t('players.splitTitle', { name })}
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-end gap-2">
          {!valid && <p className="mr-auto text-sm text-muted">{t('players.splitNone')}</p>}
          <Button variant="ghost" onClick={onClose}>
            {t('players.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={!valid || split.isPending}
            onClick={() => split.mutate({ id: cluster.id, tracks: picked }, { onSuccess: onClose })}
          >
            <SplitIcon className="size-4" />
            {t('players.splitConfirm', { count: picked.length })}
          </Button>
        </div>
      }
    >
      <p className="mb-3 text-sm">{t('players.splitBody')}</p>
      <ul className="flex flex-wrap gap-2">
        {cluster.tracks.map((track) => {
          const on = picked.includes(track.id)
          const label = t('players.splitPick', { time: `${formatClock(track.t0, { tenths: false })}–${formatClock(track.t1, { tenths: false })}`, count: track.n })
          return (
            <li key={track.id}>
              <button
                type="button"
                aria-pressed={on}
                aria-label={label}
                onClick={() => toggle(track.id)}
                className={cx(
                  'flex cursor-pointer flex-col items-center gap-1 rounded-card border p-1.5 text-xs tabular-nums focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
                  on ? 'border-accent bg-accent-soft ring-2 ring-accent' : 'border-hairline bg-surface hover:bg-hover',
                )}
              >
                {track.crop ? <Crop videoId={videoId} sheets={sheets} crop={track.crop} scale={0.6} /> : null}
                <span aria-hidden="true">{formatClock(track.t0, { tenths: false })}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </Modal>
  )
}

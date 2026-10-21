import '../i18n/pages'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { CheckIcon, ChevronRightIcon, UndoIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { PageHeader } from '../components/PageHeader'
import { ProgressBar } from '../components/ProgressBar'
import { Segmented } from '../components/Segmented'
import { PageSpinner } from '../components/Spinner'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { useHlsSource } from '../lib/hls'
import {
  boxAt,
  numbersToOffer,
  useAcceptRecognised,
  useAnswer,
  useIdentifyQueue,
  useMoments,
  useRestore,
  useTeamsAnswer,
  zoomOnto,
  type Answer,
  type CardState,
  type Moment,
  type QueueItem,
  type Recognised,
  type TeamsQuestion,
} from '../lib/identify'
import { clusterTitle, cropStyle, isPieces, parseNumber, sheetUrl, usePlayers, type PlayerCluster, type PlayerTrack, type Players, type Sheets } from '../lib/players'
import { useVideo } from '../lib/queries'
import { sideLabel } from '../lib/labels'
import { SideNamesContext, sideNamesOf, useSideNames } from '../lib/sides'
import { formatPercent } from '../lib/suggestions'
import type { Side, Video } from '../lib/types'
import { isUuid } from '../lib/video'

/**
 * /videos/:id/identify — identify the players WiePlay is not sure of (docs/PLAN.md §10.5), like
 * Hudl or Balltime: one card at a time, the biggest share of the footage first, the person large
 * (the video zoomed onto them at several moments). Tap their number (ours from the roster, the
 * numbers on court in their rallies first), or not a player, the other team, the same person as
 * another card, two people (split), skip. Each answer links the cards anew; Undo takes it back.
 * No faces: your eyes match the person, WiePlay links the answer to the tracks.
 */
export function IdentifyPage() {
  const { id } = useParams()
  const { t } = useTranslation()
  if (!isUuid(id)) return <Message>{t('video.notFound')}</Message>
  return <IdentifyView videoId={id} />
}

function IdentifyView({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  const video = useVideo(videoId)
  const ready = video.data?.status === 'ready'
  const players = usePlayers(videoId, ready)
  const queue = useIdentifyQueue(videoId, ready)
  if (video.isPending || (ready && (players.isPending || queue.isPending))) return <PageSpinner />
  if (video.isError || !video.data) {
    if (video.error instanceof ApiError && [403, 404, 422].includes(video.error.status)) return <Message>{t('video.notFound')}</Message>
    return <LoadError onRetry={() => void video.refetch()} />
  }
  const v = video.data
  return (
    <div className="flex flex-col gap-4">
      <title>{`${t('identify.title')} · ${v.title} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <>
            <Link to={`/videos/${v.id}`} className="font-semibold link">
              {v.title}
            </Link>
            <ChevronRightIcon className="size-4" />
            <Link to={`/videos/${v.id}/players`} className="font-semibold link">
              {t('players.title')}
            </Link>
            <ChevronRightIcon className="size-4" />
          </>
        }
        title={
          <span className="inline-flex flex-wrap items-center gap-3">
            {t('identify.title')}
            <Badge>{t('players.beta')}</Badge>
          </span>
        }
        subtitle={<p className="max-w-3xl text-sm">{t('identify.intro')}</p>}
      />
      {players.isError || queue.isError ? (
        <LoadError onRetry={() => void Promise.all([players.refetch(), queue.refetch()])} />
      ) : players.data?.run && players.data.sheets && queue.data ? (
        <SideNamesContext value={sideNamesOf(v, t)}>
          <Queue
            video={v}
            players={players.data}
            sheets={players.data.sheets}
            items={queue.data.items}
            sureShare={queue.data.sure_share}
            teams={queue.data.teams}
            recognised={queue.data.recognised ?? []}
          />
        </SideNamesContext>
      ) : (
        <Message>{t('identify.noPlayers')}</Message>
      )}
    </div>
  )
}

interface QueueProps {
  video: Video
  players: Players
  sheets: Sheets
  items: QueueItem[]
  sureShare: number
  teams: TeamsQuestion | null
  recognised: Recognised[]
}

function Queue({ video, players, sheets, items, sureShare, teams, recognised }: QueueProps) {
  const { t } = useTranslation()
  const answer = useAnswer(video.id)
  const restore = useRestore(video.id)
  const accept = useAcceptRecognised(video.id)
  const [skipped, setSkipped] = useState<string[]>([])
  const [undo, setUndo] = useState<CardState[][]>([])
  const [start] = useState(() => items.length)
  const [done, setDone] = useState(0)
  const byId = useMemo(() => new Map(players.clusters.map((c) => [c.id, c])), [players.clusters])
  const open = items.filter((i) => !skipped.includes(i.card) && byId.has(i.card))
  const current = open[0] ?? null
  const card = current ? (byId.get(current.card) ?? null) : null
  const total = Math.max(start, done + items.length)
  const busy = answer.isPending || restore.isPending || accept.isPending
  const acceptAll = () => {
    if (busy || recognised.length === 0) return
    accept.mutate(
      recognised.map((r) => r.card),
      {
        onSuccess: (data) => {
          setUndo((u) => [...u, data.undo])
          setDone((n) => n + 1)
        },
      },
    )
  }

  const send = (a: Answer) => {
    if (!current || busy) return
    answer.mutate(
      { card: current.card, answer: a },
      {
        onSuccess: (data) => {
          setUndo((u) => [...u, data.undo])
          setDone((n) => n + 1)
        },
      },
    )
  }
  const takeBack = () => {
    const last = undo.at(-1)
    if (!last || busy) return
    restore.mutate(last, {
      onSuccess: () => {
        setUndo((u) => u.slice(0, -1))
        setDone((n) => Math.max(0, n - 1))
      },
    })
  }
  const skip = () => current && setSkipped((s) => [...s, current.card])

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-2 p-4" role="region" aria-label={t('identify.progressTitle')}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-lg font-bold tabular-nums" aria-live="polite">
            {t('identify.progress', { done, total })}
          </p>
          <p className="text-sm text-muted">{t('identify.sureShare', { value: formatPercent(sureShare) })}</p>
        </div>
        <ProgressBar value={sureShare} label={t('identify.sureShare', { value: formatPercent(sureShare) })} />
      </Card>
      {teams && players.can_edit && <TeamsCard videoId={video.id} sheets={sheets} question={teams} />}
      {recognised.length > 0 && players.can_edit && (
        <RecognisedCard videoId={video.id} sheets={sheets} recognised={recognised} busy={busy} onAccept={acceptAll} />
      )}
      {!players.can_edit ? (
        <Message>{t('identify.readOnly')}</Message>
      ) : card && current && isPieces(card) ? (
        <PiecesQuestion
          key={`${card.id}-${card.tracks.length}`}
          players={players}
          sheets={sheets}
          card={card}
          item={current}
          busy={busy}
          canUndo={undo.length > 0}
          onAnswer={send}
          onSkip={skip}
          onUndo={takeBack}
        />
      ) : card && current ? (
        <CardQuestion
          key={card.id}
          video={video}
          players={players}
          sheets={sheets}
          card={card}
          item={current}
          byId={byId}
          busy={busy}
          canUndo={undo.length > 0}
          onAnswer={send}
          onSkip={skip}
          onUndo={takeBack}
        />
      ) : (
        <Card className="flex flex-col items-start gap-3 p-4">
          <p className="flex items-center gap-2 font-semibold">
            <CheckIcon className="size-5" />
            {skipped.length > 0 ? t('identify.skippedLeft', { count: skipped.length }) : t('identify.allDone')}
          </p>
          <div className="flex flex-wrap gap-2">
            {skipped.length > 0 && <Button onClick={() => setSkipped([])}>{t('identify.again')}</Button>}
            {undo.length > 0 && (
              <Button variant="ghost" onClick={takeBack} disabled={busy}>
                <UndoIcon className="size-4" />
                {t('identify.undo')}
              </Button>
            )}
            <ButtonLink to={`/videos/${video.id}/players`} variant="secondary">
              {t('identify.toGallery')}
            </ButtonLink>
          </div>
        </Card>
      )}
    </div>
  )
}

/**
 * The cards of ours the team's earlier games recognise (docs/PLAN.md §10.5b): the number read
 * on the shirt is one the team plays. One check of the pictures, then "Accept all".
 */
function RecognisedCard({
  videoId,
  sheets,
  recognised,
  busy,
  onAccept,
}: {
  videoId: string
  sheets: Sheets
  recognised: Recognised[]
  busy: boolean
  onAccept: () => void
}) {
  const { t } = useTranslation()
  return (
    <Card className="flex flex-col gap-3 p-4" role="region" aria-label={t('identify.recognised.title')}>
      <h2 className="font-semibold">{t('identify.recognised.title')}</h2>
      <p className="text-sm text-muted">{t('identify.recognised.body', { count: recognised.length })}</p>
      <ul className="flex flex-wrap gap-3">
        {recognised.map((r) => (
          <li key={r.card} className="flex flex-col items-start gap-1">
            <span className="flex gap-1">
              {r.crops.slice(0, 2).map((c) => (
                <Crop key={`${c[0]}-${c[1]}`} videoId={videoId} sheets={sheets} crop={c} scale={0.75} />
              ))}
            </span>
            <span className="text-sm font-semibold">{r.name ? `#${r.number} ${r.name}` : `#${r.number}`}</span>
            <span className="text-xs text-muted">{t('identify.recognised.games', { count: r.games })}</span>
          </li>
        ))}
      </ul>
      <div>
        <Button variant="primary" disabled={busy} onClick={onAccept}>
          <CheckIcon className="size-4" />
          {t('identify.recognised.accept', { count: recognised.length })}
        </Button>
      </div>
    </Card>
  )
}

/** "Which team is ours?": the biggest cards on each side of the net in the first set. */
function TeamsCard({ videoId, sheets, question }: { videoId: string; sheets: Sheets; question: TeamsQuestion }) {
  const { t } = useTranslation()
  const teams = useTeamsAnswer(videoId)
  return (
    <Card className="flex flex-col gap-3 p-4" role="region" aria-label={t('identify.teams.title')}>
      <h2 className="font-semibold">{t('identify.teams.title')}</h2>
      <p className="text-sm text-muted">{t('identify.teams.body', { set: question.set })}</p>
      {(['near', 'far'] as const).map((side) => (
        <div key={side} className="flex flex-col gap-2">
          <p className="text-sm font-semibold">{t(`identify.teams.${side}`)}</p>
          <div className="flex flex-wrap items-end gap-2">
            {question[side].map((c) =>
              c.crops[0] ? <Crop key={c.card} videoId={videoId} sheets={sheets} crop={c.crops[0]} scale={0.75} /> : null,
            )}
            <Button variant="primary" disabled={teams.isPending} onClick={() => teams.mutate(side === 'near')}>
              {t('identify.teams.ours')}
            </Button>
          </div>
        </div>
      ))}
    </Card>
  )
}

function Crop({ videoId, sheets, crop, scale = 1 }: { videoId: string; sheets: Sheets; crop: [number, number]; scale?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-md bg-navy bg-no-repeat"
      style={{ ...cropStyle(sheets, crop, scale), backgroundImage: `url(${sheetUrl(videoId, sheets, crop[0])})` }}
    />
  )
}

interface CardQuestionProps {
  video: Video
  players: Players
  sheets: Sheets
  card: PlayerCluster
  item: QueueItem
  byId: Map<string, PlayerCluster>
  busy: boolean
  canUndo: boolean
  onAnswer: (a: Answer) => void
  onSkip: () => void
  onUndo: () => void
}

function CardQuestion({ video, players, sheets, card, item, byId, busy, canUndo, onAnswer, onSkip, onUndo }: CardQuestionProps) {
  const { t } = useTranslation()
  const names = useSideNames()
  const [typed, setTyped] = useState('')
  const [splitting, setSplitting] = useState(false)
  const [sameAs, setSameAs] = useState(false)
  const offer = numbersToOffer(card.team, item.on_court, players.roster)
  const candidates = item.same_as.map((id) => byId.get(id)).filter((c): c is PlayerCluster => c !== undefined)
  const other: Side = card.team === 'us' ? 'them' : 'us'
  const team = sideLabel(t, card.team, names)

  // Keys: digits and Enter (a number), N not a player, T the other team, S same as, M mixed,
  // K or → skip, U undo.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      const target = event.target
      if (target instanceof Element && target.closest('input, textarea, select, [role="dialog"]')) return
      const key = event.key
      if (/^[0-9]$/.test(key)) setTyped((s) => (s + key).slice(-2))
      else if (key === 'Backspace') setTyped((s) => s.slice(0, -1))
      else if (key === 'Enter' && typed !== '') onAnswer({ kind: 'number', number: Number(typed) })
      else if (key === 'n' || key === 'N') onAnswer({ kind: 'not_player' })
      else if (key === 't' || key === 'T') onAnswer({ kind: 'team', team: other })
      else if (key === 's' || key === 'S') setSameAs(true)
      else if (key === 'm' || key === 'M') setSplitting(true)
      else if (key === 'k' || key === 'K' || key === 'ArrowRight') onSkip()
      else if (key === 'u' || key === 'U') onUndo()
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [typed, onAnswer, onSkip, onUndo, other])

  const submitTyped = (event: FormEvent) => {
    event.preventDefault()
    const n = parseNumber(typed)
    if (n !== null && n !== undefined) onAnswer({ kind: 'number', number: n })
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,26rem)]">
      <Card className="flex flex-col gap-3 p-3 sm:p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-semibold">
            {clusterTitle(card, (key) => t('players.unnamed', { key }))}
            <span className="ml-2 font-normal text-muted">
              {team} · {t('identify.seconds', { n: Math.round(card.seconds) })}
            </span>
          </h2>
          {card.ocr_number !== null && <p className="text-sm text-muted">{t('identify.read', { number: card.ocr_number })}</p>}
        </div>
        <Pictures video={video} card={card} />
        <div className="flex gap-1.5 overflow-x-auto pb-1" aria-label={t('identify.crops')}>
          {card.crops.map((crop, i) => (
            <Crop key={i} videoId={video.id} sheets={sheets} crop={crop} scale={0.8} />
          ))}
        </div>
      </Card>

      <Card className="flex flex-col gap-3 p-3 sm:p-4" role="region" aria-label={t('identify.answers')}>
        <p className="text-sm font-semibold">{t('identify.whoIsIt')}</p>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {offer.map((o) => (
            <Button
              key={o.number}
              size="touch"
              variant={o.onCourt ? 'primary' : 'secondary'}
              disabled={busy}
              title={o.onCourt ? t('identify.onCourt') : undefined}
              onClick={() => onAnswer({ kind: 'number', number: o.number })}
              className="flex-col gap-0 leading-tight"
            >
              <span className="text-base font-bold tabular-nums">#{o.number}</span>
              {o.name && <span className="max-w-full truncate text-xs font-normal">{o.name}</span>}
            </Button>
          ))}
        </div>
        <form onSubmit={submitTyped} className="flex items-center gap-2">
          <label className="text-sm font-semibold" htmlFor={`typed-${card.id}`}>
            {t('identify.typeNumber')}
          </label>
          <input
            id={`typed-${card.id}`}
            inputMode="numeric"
            className="h-11 w-20 rounded-control border border-hairline-strong bg-surface px-2 text-center font-mono tabular-nums sm:h-9"
            value={typed}
            onChange={(e) => setTyped(e.target.value.replace(/\D/g, '').slice(0, 2))}
          />
          <Button type="submit" disabled={busy || parseNumber(typed) == null}>
            {t('identify.save')}
          </Button>
        </form>
        <div className="grid grid-cols-2 gap-2">
          <Button size="touch" disabled={busy} onClick={() => onAnswer({ kind: 'not_player' })}>
            {t('identify.notPlayer')}
            <Kbd>N</Kbd>
          </Button>
          <Button size="touch" disabled={busy} onClick={() => onAnswer({ kind: 'team', team: other })}>
            {t('identify.otherTeam', { team: sideLabel(t, other, names) })}
            <Kbd>T</Kbd>
          </Button>
          <Button size="touch" disabled={busy || candidates.length === 0} onClick={() => setSameAs(true)}>
            {t('identify.sameAs')}
            <Kbd>S</Kbd>
          </Button>
          <Button size="touch" disabled={busy || card.tracks.length < 2} onClick={() => setSplitting(true)}>
            {t('identify.mixed')}
            <Kbd>M</Kbd>
          </Button>
          <Button size="touch" variant="ghost" disabled={busy} onClick={onSkip}>
            {t('identify.skip')}
            <Kbd>K</Kbd>
          </Button>
          <Button size="touch" variant="ghost" disabled={busy || !canUndo} onClick={onUndo}>
            <UndoIcon className="size-4" />
            {t('identify.undo')}
            <Kbd>U</Kbd>
          </Button>
        </div>
        <p className="text-xs text-muted">{t('identify.keys')}</p>
      </Card>

      <Modal open={sameAs} onClose={() => setSameAs(false)} title={t('identify.sameAsTitle')}>
        <ul className="flex flex-col gap-2">
          {candidates.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="flex w-full cursor-pointer items-center gap-3 rounded-control border border-hairline p-2 text-left hover:bg-sunken focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => {
                  setSameAs(false)
                  onAnswer({ kind: 'same_as', into: c.id })
                }}
              >
                {c.crops[0] && <Crop videoId={video.id} sheets={sheets} crop={c.crops[0]} scale={0.6} />}
                <span className="flex flex-col">
                  <span className="font-semibold">{clusterTitle(c, (key) => t('players.unnamed', { key }))}</span>
                  <span className="text-sm text-muted">
                    {c.number === null && c.ocr_number !== null ? t('identify.read', { number: c.ocr_number }) : ''}{' '}
                    {t('identify.seconds', { n: Math.round(c.seconds) })}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Modal>
      {splitting && (
        <PiecesDialog
          videoId={video.id}
          sheets={sheets}
          card={card}
          offer={offer}
          onClose={() => setSplitting(false)}
          onAnswer={(a) => {
            setSplitting(false)
            onAnswer(a)
          }}
        />
      )}
    </div>
  )
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd aria-hidden="true" className="ml-1 hidden rounded-md border border-current/25 px-1 font-mono text-[11px] leading-4 opacity-70 sm:inline">
      {children}
    </kbd>
  )
}

type Offer = { number: number; name: string | null; onCourt: boolean }[]

/** The pieces of a card as toggles: a picture each, its time and the number read on it. */
function PieceTiles({ videoId, sheets, tracks, picked, onToggle }: { videoId: string; sheets: Sheets; tracks: PlayerTrack[]; picked: number[]; onToggle: (id: number) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {tracks.map((tr) => {
        const on = picked.includes(tr.id)
        return (
          <button
            key={tr.id}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(tr.id)}
            className={cx('flex cursor-pointer flex-col items-center gap-1 rounded-control border-2 p-1 text-xs', on ? 'border-accent bg-accent-soft' : 'border-hairline')}
          >
            {tr.crop ? <Crop videoId={videoId} sheets={sheets} crop={tr.crop} scale={0.7} /> : <span className="h-28 w-14 bg-sunken" />}
            <span className="tabular-nums">
              {Math.floor(tr.t0 / 60000)}:{String(Math.floor(tr.t0 / 1000) % 60).padStart(2, '0')}
              {tr.number !== null && ` · #${tr.number}`}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** "These N pieces are…": the numbers to offer, a typed one, and not a player. */
function NamePieces({ count, offer, onName }: { count: number; offer: Offer; onName: (number: number | null, notPlayer?: boolean) => void }) {
  const { t } = useTranslation()
  const [typed, setTyped] = useState('')
  const some = count > 0
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-semibold">{t('identify.piecesAre', { count })}</p>
      <div className="flex flex-wrap gap-1.5">
        {offer.map((o) => (
          <Button key={o.number} variant={o.onCourt ? 'primary' : 'secondary'} disabled={!some} onClick={() => onName(o.number)} className="flex-col gap-0 leading-tight">
            <span className="font-bold tabular-nums">#{o.number}</span>
            {o.name && <span className="max-w-32 truncate text-xs font-normal">{o.name}</span>}
          </Button>
        ))}
        <input
          inputMode="numeric"
          aria-label={t('identify.typeNumber')}
          className="h-11 w-16 rounded-control border border-hairline-strong bg-surface px-2 text-center font-mono sm:h-9"
          value={typed}
          onChange={(e) => setTyped(e.target.value.replace(/\D/g, '').slice(0, 2))}
        />
        <Button disabled={!some || parseNumber(typed) == null} onClick={() => onName(Number(typed))}>
          {t('identify.save')}
        </Button>
        <Button disabled={!some} onClick={() => onName(null, true)}>
          {t('identify.notPlayer')}
        </Button>
      </div>
    </div>
  )
}

/**
 * Several people on one card (WiePlay joined them by their colours): tick the pieces that are
 * one person and name them (they join that number's card), or just move them to a card of
 * their own.
 */
function PiecesDialog({
  videoId,
  sheets,
  card,
  offer,
  onClose,
  onAnswer,
}: {
  videoId: string
  sheets: Sheets
  card: PlayerCluster
  offer: Offer
  onClose: () => void
  onAnswer: (a: Answer) => void
}) {
  const { t } = useTranslation()
  const [picked, setPicked] = useState<number[]>([])
  const tracks = [...card.tracks].sort((a, b) => a.t0 - b.t0)
  const some = picked.length > 0
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  return (
    <Modal open onClose={onClose} title={t('identify.splitTitle')} size="lg">
      <p className="text-sm text-muted">{t('identify.splitBody')}</p>
      <div className="mt-3">
        <PieceTiles videoId={videoId} sheets={sheets} tracks={tracks} picked={picked} onToggle={toggle} />
      </div>
      <div className="mt-3 flex flex-col gap-2 border-t border-hairline pt-3">
        <NamePieces count={picked.length} offer={offer} onName={(number, notPlayer = false) => onAnswer({ kind: 'pieces', tracks: picked, number, not_player: notPlayer })} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!some || picked.length === tracks.length} onClick={() => onAnswer({ kind: 'split', tracks: picked })}>
            {t('identify.splitConfirm', { count: picked.length })}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

/** A pieces card shows its biggest pieces first, this many at once (core/identify.py). */
const PIECES_SHOWN = 24

interface PiecesQuestionProps {
  players: Players
  sheets: Sheets
  card: PlayerCluster
  item: QueueItem
  busy: boolean
  canUndo: boolean
  onAnswer: (a: Answer) => void
  onSkip: () => void
  onUndo: () => void
}

/**
 * A pieces card (players v3): pieces without a number that look alike, most often of several
 * teammates. Tick the pieces of one person and tap their number: they join that number's card,
 * the rest comes back for the next answer.
 */
function PiecesQuestion({ players, sheets, card, item, busy, canUndo, onAnswer, onSkip, onUndo }: PiecesQuestionProps) {
  const { t } = useTranslation()
  const names = useSideNames()
  const [picked, setPicked] = useState<number[]>([])
  const [all, setAll] = useState(false)
  // Whose kit the ticked pieces wear: the card's team, or the other (WiePlay mixed the kits up).
  const [team, setTeam] = useState<Side>(card.team)
  const offer = numbersToOffer(team, team === card.team ? item.on_court : [], players.roster)
  const tracks = [...card.tracks].sort((a, b) => b.n - a.n)
  const shown = all ? tracks : tracks.slice(0, PIECES_SHOWN)
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  const name = (number: number | null, notPlayer = false) => {
    if (busy || picked.length === 0) return
    onAnswer({ kind: 'pieces', tracks: picked, number, not_player: notPlayer, ...(team !== card.team && { team }) })
  }

  // Keys: K or → skip, U undo (numbers are tapped: the pieces are ticked first).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      const target = event.target
      if (target instanceof Element && target.closest('input, textarea, select, [role="dialog"]')) return
      if (event.key === 'k' || event.key === 'K' || event.key === 'ArrowRight') onSkip()
      else if (event.key === 'u' || event.key === 'U') onUndo()
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSkip, onUndo])

  return (
    <Card className="flex flex-col gap-3 p-3 sm:p-4" role="region" aria-label={t('identify.answers')}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">
          {clusterTitle(
            card,
            (key) => t('players.unnamed', { key }),
            (key) => t('players.piecesCard', { key }),
          )}
          <span className="ml-2 font-normal text-muted">
            {sideLabel(t, card.team, names)} · {t('players.pieces', { count: card.tracks.length })}
          </span>
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-muted">{t('identify.piecesBody')}</p>
      <PieceTiles videoId={players.video_id} sheets={sheets} tracks={shown} picked={picked} onToggle={toggle} />
      {tracks.length > shown.length && (
        <Button variant="ghost" className="self-start" onClick={() => setAll(true)}>
          {t('identify.showAll', { count: tracks.length })}
        </Button>
      )}
      {/* Kept in view while ticking down a long list of pieces (a phone). */}
      <div className="sticky bottom-0 z-10 -mx-3 -mb-3 flex flex-col gap-2 rounded-b-card border-t border-hairline bg-surface p-3 sm:-mx-4 sm:-mb-4 sm:p-4">
        <Segmented
          label={t('identify.piecesTeam')}
          value={team}
          options={(['us', 'them'] as const).map((side) => ({ value: side, label: t('identify.otherTeam', { team: sideLabel(t, side, names) }) }))}
          onChange={(side) => setTeam(side)}
          className="self-start"
        />
        <NamePieces key={team} count={picked.length} offer={offer} onName={name} />
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" disabled={busy} onClick={onSkip}>
            {t('identify.skip')}
            <Kbd>K</Kbd>
          </Button>
          <Button variant="ghost" disabled={busy || !canUndo} onClick={onUndo}>
            <UndoIcon className="size-4" />
            {t('identify.undo')}
            <Kbd>U</Kbd>
          </Button>
        </div>
      </div>
    </Card>
  )
}

const VIEW_ASPECT = 3 / 4

/** Plays a few seconds around a moment in a loop, the zoom following the person; the cleanup. */
function loopMoment(element: HTMLVideoElement, moment: Moment, aspect: number): () => void {
  const from = (moment.clip[0]?.[0] ?? moment.t_ms - 1500) / 1000
  const to = (moment.clip.at(-1)?.[0] ?? moment.t_ms + 1500) / 1000
  let frame: number | null = null
  element.muted = true
  element.currentTime = from
  void element.play().catch(() => {})
  const follow = () => {
    if (element.currentTime > to || element.currentTime < from - 1) element.currentTime = from
    Object.assign(element.style, zoomOnto(boxAt(moment, element.currentTime * 1000), aspect, VIEW_ASPECT))
    frame = requestAnimationFrame(follow)
  }
  frame = requestAnimationFrame(follow)
  return () => {
    if (frame !== null) cancelAnimationFrame(frame)
    element.pause()
  }
}

/**
 * The person large: the video zoomed onto the card's box, looping a few seconds around each
 * moment (from the players index; no picture is made or stored).
 */
function Pictures({ video, card }: { video: Video; card: PlayerCluster }) {
  const { t } = useTranslation()
  const moments = useMoments(video.id, card.id)
  const [k, setK] = useState(0)
  const [element, setElement] = useState<HTMLVideoElement | null>(null)
  const state = useHlsSource(element, `/api/media/${video.id}/master.m3u8`)
  const list = moments.data ?? []
  const moment: Moment | undefined = list[Math.min(k, list.length - 1)]
  const aspect = video.width && video.height ? video.width / video.height : 16 / 9

  useEffect(() => {
    if (!element || !moment || state !== 'ready') return
    return loopMoment(element, moment, aspect)
  }, [element, moment, state, aspect])

  return (
    <div className="flex flex-col gap-2">
      <div className="relative mx-auto aspect-[3/4] w-full max-w-sm overflow-hidden rounded-card bg-navy">
        <video ref={setElement} muted playsInline aria-label={t('identify.picture')} className="absolute max-w-none" style={moment ? zoomOnto(moment.box, aspect, VIEW_ASPECT) : undefined} />
        {(moments.isPending || state === 'loading') && <p className="absolute inset-x-0 bottom-2 text-center text-sm text-white/80">{t('identify.loading')}</p>}
        {(state === 'error' || state === 'unsupported' || (moments.isSuccess && list.length === 0)) && (
          <p className="absolute inset-x-0 bottom-2 px-2 text-center text-sm text-white/80">{t('identify.noPicture')}</p>
        )}
      </div>
      {list.length > 1 && (
        <div role="group" aria-label={t('identify.moments')} className="flex flex-wrap justify-center gap-1.5">
          {list.map((m, i) => (
            <button
              key={m.t_ms}
              type="button"
              aria-pressed={i === k}
              onClick={() => setK(i)}
              className={cx(
                'min-h-11 min-w-11 cursor-pointer rounded-control border px-2 text-sm tabular-nums sm:min-h-9',
                i === k ? 'border-transparent bg-accent text-on-accent' : 'border-hairline-strong bg-surface',
              )}
            >
              {Math.floor(m.t_ms / 60000)}:{String(Math.floor(m.t_ms / 1000) % 60).padStart(2, '0')}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

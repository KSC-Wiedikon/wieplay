import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDate } from '../lib/format'
import { useToast } from '../lib/toast'
import type { Video, VideoGame } from '../lib/types'
import { gamePlace, gameResult, gameTeams, useGameChoices, useSetVideoGame } from '../lib/videoGame'
import { Button } from './Button'
import { LoadError } from './Message'
import { Modal } from './Modal'
import { Spinner } from './Spinner'

/**
 * The video's Wiedisync game under the page title ("Game: Volley S9 – KSC Wiedikon H1 · 3:0 ·
 * H1 · Mobiliar Volley Cup"), and for coaches, sport admins and admins the picker to change it.
 */
export function VideoGameLine({ video }: { video: Video }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const game = video.game ?? null
  if (!game && !video.can_link_game) return null
  // One line of text that wraps like a sentence on phones; the button follows it.
  const details = game ? [gameResult(game), gamePlace(game)].filter(Boolean).join(' · ') : ''
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <p className="min-w-0 tabular-nums wrap-break-word">
        <span className="font-semibold text-fg">{t('videoGame.label')}</span>{' '}
        {game ? (
          <>
            <span className="text-fg">{gameTeams(game)}</span>
            {details && ` · ${details}`}
          </>
        ) : (
          t('videoGame.none')
        )}
      </p>
      {video.can_link_game && (
        <>
          <Button variant="ghost" className="-mx-2.5 -my-2 px-2.5 text-link" onClick={() => setOpen(true)} aria-haspopup="dialog">
            {game ? t('videoGame.change') : t('videoGame.link')}
          </Button>
          {open && <VideoGameDialog video={video} onClose={() => setOpen(false)} />}
        </>
      )}
    </div>
  )
}

const NO_GAME = 'none'

/** Pick the game of the video's day: the team's own, another KSCW team's (a shared cup team's), or none. */
export function VideoGameDialog({ video, onClose }: { video: Video; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Modal open title={t('videoGame.dialogTitle')} onClose={onClose} size="lg">
      <PickForm video={video} onClose={onClose} />
    </Modal>
  )
}

function PickForm({ video, onClose }: { video: Video; onClose: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const choices = useGameChoices(video.id)
  const save = useSetVideoGame(video)
  const current = video.game_id === null ? NO_GAME : String(video.game_id)
  const [picked, setPicked] = useState(current)
  const [error, setError] = useState<string | null>(null)

  if (choices.isPending) return <Spinner label={t('common.loading')} className="self-center py-6" />
  if (choices.isError) {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="text-sm text-danger">
          {t('videoGame.loadFailed')}
        </p>
        <LoadError onRetry={() => void choices.refetch()} />
      </div>
    )
  }
  const { own, club, club_state: clubState } = choices.data
  const namesSet = Boolean(video.names?.us_set || video.names?.them_set)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (picked === current) {
      onClose()
      return
    }
    setError(null)
    const gameId = picked === NO_GAME ? null : Number(picked)
    save.mutate(gameId, {
      onSuccess: () => {
        toast.success(gameId === null ? t('videoGame.savedNone') : t('videoGame.saved'))
        onClose()
      },
      onError: (failure) => setError(t('videoGame.failed', { detail: failure instanceof ApiError ? failure.detail : t('common.error') })),
    })
  }

  const option = (game: VideoGame) => (
    <GameOption key={game.id} name={`${id}-game`} value={String(game.id)} picked={picked} onPick={setPicked}>
      <span className="font-semibold wrap-break-word text-fg">{gameTeams(game)}</span>
      <span className="text-muted tabular-nums wrap-break-word">
        {[gamePlace(game), game.time ? game.time.slice(0, 5) : null, gameResult(game) || null].filter(Boolean).join(' · ')}
      </span>
    </GameOption>
  )

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      <p className="text-sm text-muted">{t('videoGame.intro', { date: formatDate(video.recorded_on) })}</p>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-sm font-semibold">{t('videoGame.own', { team: video.team_name })}</legend>
        {own.length === 0 ? <p className="text-sm text-muted">{t('videoGame.ownNone', { team: video.team_name })}</p> : own.map(option)}
      </fieldset>

      {clubState !== 'none' && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-0.5 text-sm font-semibold">{t('videoGame.club')}</legend>
          <p className="mb-1 text-xs text-muted">{t('videoGame.clubHint')}</p>
          {clubState === 'unavailable' && (
            <p role="status" className="rounded-control border border-hairline bg-sunken px-3 py-2 text-sm">
              {t('videoGame.clubUnavailable')}
            </p>
          )}
          {club.length === 0 && clubState === 'ok' ? <p className="text-sm text-muted">{t('videoGame.clubNone')}</p> : club.map(option)}
        </fieldset>
      )}

      <fieldset className="flex flex-col gap-1.5">
        <legend className="sr-only">{t('videoGame.noGame')}</legend>
        <GameOption name={`${id}-game`} value={NO_GAME} picked={picked} onPick={setPicked}>
          <span className="font-semibold text-fg">{t('videoGame.noGame')}</span>
          <span className="text-muted">{t('videoGame.noGameHint')}</span>
        </GameOption>
      </fieldset>

      <p className="text-xs text-muted">{namesSet ? t('videoGame.namesSet') : t('videoGame.names')}</p>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? t('common.saving') : t('videoGame.save')}
        </Button>
      </div>
    </form>
  )
}

function GameOption({
  name,
  value,
  picked,
  onPick,
  children,
}: {
  name: string
  value: string
  picked: string
  onPick: (value: string) => void
  children: ReactNode
}) {
  const checked = picked === value
  return (
    <label
      className={cx(
        'flex min-h-11 cursor-pointer items-start gap-3 rounded-control border px-3 py-2.5 text-sm hover:bg-hover',
        checked ? 'border-accent bg-accent-soft' : 'border-hairline',
      )}
    >
      <input type="radio" name={name} value={value} checked={checked} onChange={() => onPick(value)} className="mt-0.5 size-4 shrink-0 accent-accent" />
      <span className="flex min-w-0 flex-col gap-0.5">{children}</span>
    </label>
  )
}

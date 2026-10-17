import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge, SportBadge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { Field, TextInput } from '../components/Field'
import { PlusIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError, ForbiddenError } from '../lib/api'
import { todayIso } from '../lib/format'
import { seasonOf, useCreateGuestTeam, useGuestTeams, useInviteCoach, useUpdateGuestTeam } from '../lib/guests'
import { useToast } from '../lib/toast'
import type { CoachInvited, GuestTeam } from '../lib/types'
import { InviteLinkNotice } from './GuestSections'

function label(team: GuestTeam): string {
  return `${team.club} ${team.name} ${team.season}`
}

/**
 * Admin → Teams → Guest clubs (PLAN §4.5): other clubs' teams with their own logins. Create one,
 * invite its first coach (a link shown once), set its video limit and whether its games may train
 * WiePlay, switch it off. Its people and their new password links are on its team page.
 */
export function GuestClubsSection() {
  const { t } = useTranslation()
  const id = useId()
  const teams = useGuestTeams()
  const update = useUpdateGuestTeam()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [inviting, setInviting] = useState<GuestTeam | null>(null)
  const [limiting, setLimiting] = useState<GuestTeam | null>(null)

  const save = (team: GuestTeam, body: Parameters<typeof update.mutate>[0]['body']) =>
    update.mutate({ team, body }, { onError: () => toast.error(t('guests.admin.saveFailed', { team: label(team) })) })

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader
        id={id}
        title={t('guests.admin.title')}
        hint={t('guests.admin.hint')}
        action={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <PlusIcon className="size-4" />
            {t('guests.admin.create')}
          </Button>
        }
      />
      {teams.isPending ? (
        <Spinner />
      ) : teams.isError ? (
        teams.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void teams.refetch()} />
        )
      ) : teams.data.length === 0 ? (
        <Message>{t('guests.admin.empty')}</Message>
      ) : (
        <Card className="overflow-x-auto">
          <Table caption={t('guests.admin.title')}>
            <THead>
              <tr>
                <Th>{t('columns.team')}</Th>
                <Th className="hidden md:table-cell">{t('guests.admin.people')}</Th>
                <Th>{t('guests.admin.videos')}</Th>
                <Th className="text-center">{t('guests.admin.trainingShort')}</Th>
                <Th className="text-center">{t('guests.admin.on')}</Th>
                <Th>
                  <span className="sr-only">{t('guests.admin.inviteCoach')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {teams.data.map((team) => (
                <tr key={team.id} className="[&>td]:align-middle">
                  <Td>
                    <span className="flex items-start gap-2">
                      <SportBadge sport={team.sport} />
                      <span className="flex flex-col">
                        <span className="font-semibold">
                          {team.club} · {team.name}
                        </span>
                        <span className="text-xs text-muted">
                          {[team.league, team.season].filter(Boolean).join(' · ')}
                        </span>
                        <span className="mt-1">
                          <Badge tone={team.adults_declared_at ? 'neutral' : 'danger'}>
                            {team.adults_declared_at ? t('guests.admin.declared') : t('guests.admin.notDeclared')}
                          </Badge>
                        </span>
                      </span>
                    </span>
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums md:table-cell">
                    {team.people} · {t('guests.admin.logins')} {team.accounts}
                  </Td>
                  <Td className="whitespace-nowrap tabular-nums">
                    {team.video_limit == null
                      ? team.videos
                      : t('guests.admin.videosOf', { count: team.videos, limit: team.video_limit })}
                  </Td>
                  <Td tight className="text-center">
                    <Switch
                      checked={team.training_ok}
                      label={t('guests.admin.trainingFor', { team: label(team) })}
                      onChange={(training_ok) => save(team, { training_ok })}
                    />
                  </Td>
                  <Td tight className="text-center">
                    <Switch
                      checked={team.enabled}
                      label={t('guests.admin.onFor', { team: label(team) })}
                      onChange={(enabled) => save(team, { enabled })}
                    />
                  </Td>
                  <Td tight className="text-right">
                    <span className="inline-flex flex-wrap justify-end gap-1">
                      <Button
                        variant="ghost"
                        className="px-3"
                        aria-label={t('guests.admin.editFor', { team: label(team) })}
                        onClick={() => setLimiting(team)}
                      >
                        {t('guests.admin.edit')}
                      </Button>
                      <Button
                        variant="ghost"
                        className="px-3"
                        disabled={!team.enabled}
                        aria-label={t('guests.admin.inviteCoachFor', { team: label(team) })}
                        onClick={() => setInviting(team)}
                      >
                        {t('guests.admin.inviteCoach')}
                      </Button>
                      {team.enabled && (
                        <ButtonLink
                          to={`/teams/${team.id}`}
                          variant="ghost"
                          className="px-3"
                          aria-label={t('guests.admin.teamPageFor', { team: label(team) })}
                        >
                          {t('guests.admin.teamPage')}
                        </ButtonLink>
                      )}
                    </span>
                  </Td>
                </tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}

      <Modal open={creating} title={t('guests.admin.createTitle')} onClose={() => setCreating(false)}>
        <CreateForm
          onDone={(team) => {
            setCreating(false)
            if (team) {
              toast.success(t('guests.admin.created', { team: label(team) }))
              setInviting(team)
            }
          }}
        />
      </Modal>
      <Modal
        open={inviting !== null}
        title={t('guests.admin.inviteTitle', { team: inviting ? label(inviting) : '' })}
        onClose={() => setInviting(null)}
      >
        {inviting && <InviteCoachForm team={inviting} onDone={() => setInviting(null)} />}
      </Modal>
      <Modal
        open={limiting !== null}
        title={t('guests.admin.editTitle', { team: limiting ? label(limiting) : '' })}
        onClose={() => setLimiting(null)}
      >
        {limiting && <LimitForm team={limiting} onDone={() => setLimiting(null)} />}
      </Modal>
    </section>
  )
}

function parseLimit(text: string): number | null | undefined {
  const value = text.trim()
  if (value === '') return null
  return /^\d{1,4}$/.test(value) ? Number(value) : undefined
}

function CreateForm({ onDone }: { onDone: (team?: GuestTeam) => void }) {
  const { t } = useTranslation()
  const id = useId()
  const create = useCreateGuestTeam()
  const [club, setClub] = useState('')
  const [name, setName] = useState('')
  const [league, setLeague] = useState('')
  const [season, setSeason] = useState(() => seasonOf(todayIso()))
  const [limit, setLimit] = useState('30')
  const [training, setTraining] = useState(false)
  const videoLimit = parseLimit(limit)
  const seasonOk = /^\d{4}\/\d{2}$/.test(season.trim())
  const ready = club.trim() !== '' && name.trim() !== '' && seasonOk && videoLimit !== undefined && !create.isPending

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!ready || videoLimit === undefined) return
    create.mutate(
      {
        club: club.trim(),
        name: name.trim(),
        league: league.trim() || null,
        season: season.trim(),
        video_limit: videoLimit,
        training_ok: training,
      },
      { onSuccess: (team) => onDone(team) },
    )
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <Field label={t('guests.admin.club')} hint={t('guests.admin.clubHint')} htmlFor={`${id}-club`}>
        <TextInput id={`${id}-club`} value={club} required maxLength={120} onChange={(e) => setClub(e.target.value)} />
      </Field>
      <Field label={t('guests.admin.team')} htmlFor={`${id}-team`}>
        <TextInput id={`${id}-team`} value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('guests.admin.league')} htmlFor={`${id}-league`}>
          <TextInput id={`${id}-league`} value={league} maxLength={64} onChange={(e) => setLeague(e.target.value)} />
        </Field>
        <Field label={t('guests.admin.season')} htmlFor={`${id}-season`}>
          <TextInput
            id={`${id}-season`}
            value={season}
            maxLength={7}
            aria-invalid={seasonOk ? undefined : true}
            onChange={(e) => setSeason(e.target.value)}
          />
        </Field>
      </div>
      <Field label={t('guests.admin.limit')} hint={t('guests.admin.limitHint')} htmlFor={`${id}-limit`}>
        <TextInput
          id={`${id}-limit`}
          inputMode="numeric"
          value={limit}
          inline
          className="w-28"
          aria-invalid={videoLimit === undefined ? true : undefined}
          onChange={(e) => setLimit(e.target.value)}
        />
      </Field>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-0.5 size-5 shrink-0 accent-accent"
          checked={training}
          onChange={(e) => setTraining(e.target.checked)}
        />
        <span className="flex flex-col">
          <span className="font-semibold">{t('guests.admin.training')}</span>
          <span className="text-xs text-muted">{t('guests.admin.trainingHint')}</span>
        </span>
      </label>
      {create.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('guests.admin.failed', {
            detail: create.error instanceof ApiError ? create.error.detail : t('common.error'),
          })}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={() => onDone()}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={!ready}>
          {create.isPending ? t('guests.admin.creating') : t('guests.admin.create')}
        </Button>
      </div>
    </form>
  )
}

function InviteCoachForm({ team, onDone }: { team: GuestTeam; onDone: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const invite = useInviteCoach(team.id)
  const [name, setName] = useState('')
  const [number, setNumber] = useState('')
  const [made, setMade] = useState<CoachInvited | null>(null)
  const numberOk = number.trim() === '' || /^\d{1,2}$/.test(number.trim())
  const ready = name.trim() !== '' && numberOk && !invite.isPending

  if (made) return <InviteLinkNotice name={made.person.name} invite={made} reset={false} onDone={onDone} />

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!ready) return
    const parsed = number.trim() === '' ? null : Number(number.trim())
    invite.mutate({ name: name.trim(), number: parsed }, { onSuccess: setMade })
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <p className="text-sm text-muted">{t('guests.admin.inviteHint')}</p>
      <Field label={t('guests.admin.coachName')} htmlFor={`${id}-name`}>
        <TextInput id={`${id}-name`} value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label={t('guests.people.number')} hint={t('guests.people.numberHint')} htmlFor={`${id}-number`}>
        <TextInput
          id={`${id}-number`}
          inputMode="numeric"
          value={number}
          maxLength={2}
          inline
          className="w-24"
          aria-invalid={numberOk ? undefined : true}
          onChange={(e) => setNumber(e.target.value)}
        />
      </Field>
      {invite.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('guests.admin.failed', {
            detail: invite.error instanceof ApiError ? invite.error.detail : t('common.error'),
          })}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={onDone}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={!ready}>
          {invite.isPending ? t('guests.admin.inviting') : t('guests.admin.invite')}
        </Button>
      </div>
    </form>
  )
}

function LimitForm({ team, onDone }: { team: GuestTeam; onDone: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const update = useUpdateGuestTeam()
  const [limit, setLimit] = useState(team.video_limit == null ? '' : String(team.video_limit))
  const videoLimit = parseLimit(limit)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (videoLimit === undefined || update.isPending) return
    update.mutate({ team, body: { video_limit: videoLimit } }, { onSuccess: onDone })
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <Field label={t('guests.admin.limit')} hint={t('guests.admin.limitHint')} htmlFor={`${id}-limit`}>
        <TextInput
          id={`${id}-limit`}
          inputMode="numeric"
          value={limit}
          inline
          className="w-28"
          aria-invalid={videoLimit === undefined ? true : undefined}
          onChange={(e) => setLimit(e.target.value)}
        />
      </Field>
      {update.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('guests.admin.saveFailed', { team: label(team) })}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={onDone}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={videoLimit === undefined || update.isPending}>
          {update.isPending ? t('common.saving') : t('common.save')}
        </Button>
      </div>
    </form>
  )
}

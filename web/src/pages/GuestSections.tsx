import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Chip } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Field, Select, TextInput } from '../components/Field'
import { AlertIcon, CheckIcon, CopyIcon, PlusIcon, UsersIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError } from '../lib/api'
import { formatDate } from '../lib/format'
import {
  useAddPerson,
  useDeclareAdults,
  useEditPerson,
  useInvitePerson,
  useRemovePerson,
  useTeamPeople,
} from '../lib/guests'
import { useToast } from '../lib/toast'
import type { GuestPerson, GuestRole, InviteLink, TeamDetail } from '../lib/types'

// A guest club's team on its team page (PLAN §4.5): its people, the adults declaration, the limit.

function detailOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback
}

/** "12 of 30 videos this season" for a guest team with a limit; null otherwise. */
export function QuotaLine({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  if (!team.guest || team.video_limit == null) return null
  const count = team.video_count ?? 0
  const full = count >= team.video_limit
  return (
    <p className={full ? 'text-sm font-semibold text-danger' : 'text-sm text-muted'}>
      {t('guests.quota', { count, limit: team.video_limit })}
      {full && team.can_upload && <span className="block font-normal">{t('guests.quotaFull')}</span>}
    </p>
  )
}

/** Before the first upload, a coach of the guest team confirms that everyone filmed is an adult. */
export function AdultsBanner({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const declare = useDeclareAdults(team.id)
  if (!team.guest || team.adults_declared_at) return null
  if (!team.can_upload) {
    // An admin or a player: only the team's coaches can say this.
    return team.can_manage_people ? <Message icon={<AlertIcon />}>{t('guests.adults.waiting')}</Message> : null
  }
  return (
    <section
      aria-labelledby="guest-adults"
      className="flex flex-col gap-3 rounded-card border border-hairline bg-accent-soft p-4 sm:p-5"
    >
      <div className="flex flex-col gap-1">
        <h2 id="guest-adults" className="font-semibold">
          {t('guests.adults.title')}
        </h2>
        <p className="text-sm text-muted">{t('guests.adults.why')}</p>
      </div>
      <p className="font-semibold">{t('guests.adults.body')}</p>
      {declare.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('guests.adults.failed')}
        </p>
      )}
      <Button variant="primary" className="self-start" disabled={declare.isPending} onClick={() => declare.mutate()}>
        <CheckIcon className="size-4" />
        {declare.isPending ? t('guests.adults.confirming') : t('guests.adults.confirm')}
      </Button>
    </section>
  )
}

/** A new invite link, shown once with a copy button (only its hash is kept). */
export function InviteLinkNotice({
  name,
  invite,
  reset,
  onDone,
}: {
  name: string
  invite: InviteLink
  reset: boolean
  onDone: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(invite.link)
      toast.success(t('guests.people.copied'))
    } catch {
      toast.error(t('guests.people.copyFailed'))
    }
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        {t('guests.people.linkBody', { name, date: formatDate(invite.expires_at) })}
        {reset && ` ${t('guests.people.linkReset', { name })}`}
      </p>
      <p className="flex items-start gap-2 text-sm font-semibold text-danger">
        <AlertIcon className="mt-0.5 size-4 shrink-0" />
        {t('guests.people.linkOnce')}
      </p>
      <code className="block rounded-xl bg-sunken p-3 font-mono text-sm break-all select-all">{invite.link}</code>
      <div className="flex flex-wrap gap-2 border-t border-hairline pt-4">
        <Button variant="primary" onClick={() => void copy()}>
          <CopyIcon />
          {t('guests.people.copy')}
        </Button>
        <Button onClick={onDone}>{t('guests.people.done')}</Button>
      </div>
    </div>
  )
}

/** The guest team's roster: its coaches (and admins) add, edit, remove and invite; players read it. */
export function PeopleSection({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const people = useTeamPeople(team.id, true)
  const remove = useRemovePerson(team.id)
  const invite = useInvitePerson(team.id)
  const manage = team.can_manage_people === true
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<GuestPerson | null>(null)
  const [removing, setRemoving] = useState<GuestPerson | null>(null)
  const [link, setLink] = useState<{ person: GuestPerson; invite: InviteLink } | null>(null)

  const makeLink = (person: GuestPerson) =>
    invite.mutate(person.member_id, {
      onSuccess: (made) => setLink({ person, invite: made }),
      onError: (error) => toast.error(t('guests.people.linkFailed', { detail: detailOf(error, t('common.error')) })),
    })

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader
        id={id}
        title={t('guests.people.title')}
        hint={manage ? t('guests.people.hint', { team: team.name }) : t('guests.people.hintPlayer', { team: team.name })}
        action={
          manage ? (
            <Button variant="primary" onClick={() => setAdding(true)}>
              <PlusIcon className="size-4" />
              {t('guests.people.add')}
            </Button>
          ) : null
        }
      />
      {people.isPending ? (
        <Spinner />
      ) : people.isError ? (
        <LoadError onRetry={() => void people.refetch()} />
      ) : people.data.length === 0 ? (
        <Message icon={<UsersIcon />}>{t('guests.people.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('guests.people.title')}>
            <THead>
              <tr>
                <Th className="w-16">{t('guests.people.number')}</Th>
                <Th>{t('guests.people.name')}</Th>
                {manage && <Th className="hidden sm:table-cell">{t('guests.people.login')}</Th>}
                {manage && (
                  <Th>
                    <span className="sr-only">{t('guests.people.invite')}</span>
                  </Th>
                )}
              </tr>
            </THead>
            <TBody>
              {people.data.map((person) => (
                <tr key={person.member_id}>
                  <Td className="font-semibold tabular-nums">{person.number ?? '–'}</Td>
                  <Td>
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="font-semibold">{person.name}</span>
                      {person.me && <span className="text-xs text-muted">{t('guests.people.you')}</span>}
                      {person.role === 'coach' && <Chip>{t('guests.people.roleCoach')}</Chip>}
                    </span>
                    {manage && (
                      <span className="mt-1 block text-xs text-muted sm:hidden">
                        <LoginState person={person} />
                      </span>
                    )}
                  </Td>
                  {manage && (
                    <Td className="hidden text-sm text-muted sm:table-cell">
                      <LoginState person={person} />
                    </Td>
                  )}
                  {manage && (
                    <Td tight className="text-right">
                      <span className="inline-flex flex-wrap justify-end gap-1">
                        <Button
                          variant="ghost"
                          className="px-3"
                          disabled={invite.isPending}
                          aria-label={t(person.account ? 'guests.people.newPasswordFor' : 'guests.people.inviteFor', {
                            name: person.name,
                          })}
                          onClick={() => makeLink(person)}
                        >
                          {person.account ? t('guests.people.newPassword') : t('guests.people.invite')}
                        </Button>
                        <Button
                          variant="ghost"
                          className="px-3"
                          aria-label={t('guests.people.editFor', { name: person.name })}
                          onClick={() => setEditing(person)}
                        >
                          {t('guests.people.edit')}
                        </Button>
                        {!person.me && (
                          <Button
                            variant="ghostDanger"
                            className="px-3"
                            aria-label={t('guests.people.removeFor', { name: person.name })}
                            onClick={() => setRemoving(person)}
                          >
                            {t('guests.people.remove')}
                          </Button>
                        )}
                      </span>
                    </Td>
                  )}
                </tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}

      <Modal open={adding} title={t('guests.people.addTitle', { team: team.name })} onClose={() => setAdding(false)}>
        <PersonForm
          team={team}
          onDone={(person) => {
            setAdding(false)
            if (person) toast.success(t('guests.people.added', { name: person.name }))
          }}
        />
      </Modal>
      <Modal
        open={editing !== null}
        title={t('guests.people.editTitle', { name: editing?.name ?? '' })}
        onClose={() => setEditing(null)}
      >
        {editing && <PersonForm team={team} person={editing} onDone={() => setEditing(null)} />}
      </Modal>
      <Modal
        open={link !== null}
        title={t('guests.people.linkTitle', { name: link?.person.name ?? '' })}
        onClose={() => setLink(null)}
      >
        {link && (
          <InviteLinkNotice
            name={link.person.name}
            invite={link.invite}
            reset={link.person.account === true}
            onDone={() => setLink(null)}
          />
        )}
      </Modal>
      <ConfirmDialog
        open={removing !== null}
        title={t('guests.people.removeTitle', { name: removing?.name ?? '', team: team.name })}
        confirmLabel={t('guests.people.remove')}
        danger
        busy={remove.isPending}
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          if (!removing) return
          const person = removing
          remove.mutate(person.member_id, {
            onSuccess: () => toast.success(t('guests.people.removed', { name: person.name })),
            onError: (error) =>
              toast.error(t('guests.people.removeFailed', { name: person.name, detail: detailOf(error, t('common.error')) })),
            onSettled: () => setRemoving(null),
          })
        }}
      >
        {t('guests.people.removeBody', { name: removing?.name ?? '', team: team.name })}
      </ConfirmDialog>
    </section>
  )
}

function LoginState({ person }: { person: GuestPerson }) {
  const { t } = useTranslation()
  const parts: string[] = []
  if (person.disabled) parts.push(t('guests.people.loginDisabled'))
  else if (person.account) {
    parts.push(t('guests.people.loginYes'))
    if (person.last_login_at) parts.push(t('guests.people.lastLogin', { date: formatDate(person.last_login_at) }))
  } else parts.push(t('guests.people.loginNo'))
  if (person.invite_expires_at) parts.push(t('guests.people.linkOpen', { date: formatDate(person.invite_expires_at) }))
  return <>{parts.join(' · ')}</>
}

/** Add a person (name, number, role) or edit one (name, number). */
function PersonForm({
  team,
  person,
  onDone,
}: {
  team: TeamDetail
  person?: GuestPerson
  onDone: (person?: GuestPerson) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const add = useAddPerson(team.id)
  const edit = useEditPerson(team.id)
  const [name, setName] = useState(person?.name ?? '')
  const [number, setNumber] = useState(person?.number != null ? String(person.number) : '')
  const [role, setRole] = useState<GuestRole>('player')
  const save = person ? edit : add
  const numberOk = number.trim() === '' || /^\d{1,2}$/.test(number.trim())
  const ready = name.trim() !== '' && numberOk && !save.isPending

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!ready) return
    const parsed = number.trim() === '' ? null : Number(number.trim())
    if (person) {
      edit.mutate({ memberId: person.member_id, body: { name: name.trim(), number: parsed } }, { onSuccess: () => onDone() })
    } else {
      add.mutate({ name: name.trim(), number: parsed, role }, { onSuccess: (created) => onDone(created) })
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <Field label={t('guests.people.name')} htmlFor={`${id}-name`}>
        <TextInput
          id={`${id}-name`}
          value={name}
          required
          maxLength={120}
          autoComplete="off"
          onChange={(e) => setName(e.target.value)}
        />
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
      {!person && (
        <Field label={t('guests.people.role')} htmlFor={`${id}-role`}>
          <Select id={`${id}-role`} value={role} inline onChange={(e) => setRole(e.target.value as GuestRole)}>
            <option value="player">{t('guests.people.rolePlayer')}</option>
            <option value="coach">{t('guests.people.roleCoach')}</option>
          </Select>
        </Field>
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('guests.people.saveFailed', { detail: detailOf(save.error, t('common.error')) })}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={() => onDone()}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={!ready}>
          {save.isPending ? t('common.saving') : person ? t('common.save') : t('guests.people.add')}
        </Button>
      </div>
    </form>
  )
}

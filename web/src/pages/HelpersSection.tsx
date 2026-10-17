import { useEffect, useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Chip } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Field, TextInput } from '../components/Field'
import { ArrowRightIcon, CheckIcon, CloseIcon, PlusIcon, SparkleIcon, UsersIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Modal } from '../components/Modal'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDate } from '../lib/format'
import { MIN_SEARCH, useAddHelper, useHelperCandidates, useRemoveHelper, useTeamHelpers } from '../lib/helpers'
import { useToast } from '../lib/toast'
import type { Helper, HelperCandidate, TeamDetail } from '../lib/types'

/** The guide for helpers. */
export const GUIDE_PATH = '/how-to-help'

/** A helper's own view of the team: what they are here for, and the guide. */
export function HelpingSection() {
  const { t } = useTranslation()
  return (
    <section
      aria-labelledby="team-helping"
      className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-card border border-hairline bg-accent-soft p-4 sm:p-5"
    >
      <span className="grid size-11 shrink-0 place-items-center rounded-full bg-accent text-on-accent">
        <SparkleIcon className="size-5" />
      </span>
      <div className="flex min-w-0 flex-1 basis-56 flex-col gap-0.5">
        <h2 id="team-helping" className="font-semibold">
          {t('team.helping.title')}
        </h2>
        <p className="text-sm text-muted">{t('team.helping.body')}</p>
      </div>
      <ButtonLink to={GUIDE_PATH} variant="primary">
        {t('guide.link')}
        <ArrowRightIcon className="size-4" />
      </ButtonLink>
    </section>
  )
}

/** What a helper can and can't do (PLAN §5.1 "analyst"), with the guide. */
function HelperRules() {
  const { t } = useTranslation()
  const can = [t('team.helpers.canWatch'), t('team.helpers.canTag'), t('team.helpers.canUpload'), t('team.helpers.canShare')]
  const cannot = [t('team.helpers.cannotDownload'), t('team.helpers.cannotDelete'), t('team.helpers.cannotGrant')]
  return (
    <div className="flex flex-col gap-3 rounded-card border border-hairline bg-sunken p-4 text-sm">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <h3 className="font-semibold">{t('team.helpers.can')}</h3>
          <ul className="flex flex-col gap-1">
            {can.map((text) => (
              <li key={text} className="flex items-start gap-2">
                <CheckIcon className="mt-0.5 size-4 text-link" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-col gap-1.5">
          <h3 className="font-semibold">{t('team.helpers.cannot')}</h3>
          <ul className="flex flex-col gap-1">
            {cannot.map((text) => (
              <li key={text} className="flex items-start gap-2">
                <CloseIcon className="mt-0.5 size-4 text-danger" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <Link to={GUIDE_PATH} className="link self-start font-semibold">
        {t('guide.linkHint')}
      </Link>
    </div>
  )
}

/** The team's helpers, for those who may pick them (`can_grant`): list, add, remove. */
export function HelpersSection({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const id = useId()
  const helpers = useTeamHelpers(team.id, true)
  const remove = useRemoveHelper(team.id)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Helper | null>(null)

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeader
        id={id}
        title={t('team.helpers.title')}
        hint={t('team.helpers.hint')}
        action={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <PlusIcon className="size-4" />
            {t('team.helpers.add')}
          </Button>
        }
      />
      <HelperRules />
      {helpers.isPending ? (
        <Spinner />
      ) : helpers.isError ? (
        <LoadError onRetry={() => void helpers.refetch()} />
      ) : helpers.data.length === 0 ? (
        <Message icon={<UsersIcon />}>{t('team.helpers.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('team.helpers.title')}>
            <THead>
              <tr>
                <Th>{t('team.helpers.name')}</Th>
                <Th className="hidden sm:table-cell">{t('team.helpers.added')}</Th>
                <Th className="hidden md:table-cell">{t('team.helpers.note')}</Th>
                <Th>
                  <span className="sr-only">{t('team.helpers.remove')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {helpers.data.map((helper) => {
                const added = t('team.helpers.addedBy', { date: formatDate(helper.granted_at), name: helper.granted_by_name })
                return (
                  <tr key={helper.id}>
                    <Td>
                      <span className="block font-semibold">{helper.name}</span>
                      {helper.teams.length > 0 && <span className="block text-xs text-muted">{helper.teams.join(', ')}</span>}
                      <span className="mt-1 block text-xs text-muted sm:hidden">{added}</span>
                      {helper.note && <span className="mt-1 block text-xs text-muted md:hidden">{helper.note}</span>}
                    </Td>
                    <Td className="hidden whitespace-nowrap text-muted tabular-nums sm:table-cell">{added}</Td>
                    <Td className="hidden text-muted md:table-cell">{helper.note || '–'}</Td>
                    <Td tight className="text-right">
                      <Button
                        variant="ghostDanger"
                        className="px-3"
                        aria-label={t('team.helpers.removeFor', { name: helper.name })}
                        onClick={() => setRemoving(helper)}
                      >
                        {t('team.helpers.remove')}
                      </Button>
                    </Td>
                  </tr>
                )
              })}
            </TBody>
          </Table>
        </Card>
      )}

      <AddHelperDialog team={team} open={adding} onClose={() => setAdding(false)} />
      <ConfirmDialog
        open={removing !== null}
        title={t('team.helpers.removeTitle', { name: removing?.name ?? '' })}
        confirmLabel={t('team.helpers.remove')}
        danger
        busy={remove.isPending}
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          if (removing) remove.mutate(removing, { onSettled: () => setRemoving(null) })
        }}
      >
        {t('team.helpers.removeBody', { name: removing?.name ?? '', team: team.name })}
      </ConfirmDialog>
    </section>
  )
}

/** The search waits this long after the last key before asking the API. */
const DEBOUNCE_MS = 250

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

function AddHelperDialog({ team, open, onClose }: { team: TeamDetail; open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Modal open={open} title={t('team.helpers.dialog.title', { team: team.name })} onClose={onClose}>
      {/* Mounted only while open: every opening starts empty. */}
      <AddHelperForm team={team} onDone={onClose} />
    </Modal>
  )
}

function AddHelperForm({ team, onDone }: { team: TeamDetail; onDone: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const add = useAddHelper(team.id)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [chosen, setChosen] = useState<HelperCandidate | null>(null)
  const [note, setNote] = useState('')
  const q = useDebounced(query, DEBOUNCE_MS)
  const candidates = useHelperCandidates(team.id, q)
  const typed = query.replace(/\s/g, '').length >= MIN_SEARCH
  const options = typed ? (candidates.data ?? []) : []
  const listId = `${id}-results`
  const showList = !chosen && typed && options.length > 0

  const choose = (candidate: HelperCandidate) => {
    setChosen(candidate)
    add.reset()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!showList) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((i) => (i + step + options.length) % options.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const option = options[Math.min(active, options.length - 1)]
      if (option) choose(option)
    }
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!chosen || add.isPending) return
    add.mutate(
      { memberId: chosen.id, note: note.trim() || null },
      {
        onSuccess: (helper) => {
          toast.success(t('team.helpers.dialog.added', { name: helper.name, team: team.name }))
          onDone()
        },
      },
    )
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      {chosen ? (
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold">{t('team.helpers.dialog.chosen')}</span>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control border border-hairline-strong bg-sunken px-3 py-2">
            <span className="min-w-0 flex-1 basis-40">
              <span className="block font-semibold">{chosen.name}</span>
              {chosen.teams.length > 0 && <span className="block text-xs text-muted">{chosen.teams.join(', ')}</span>}
            </span>
            <Button
              variant="ghost"
              aria-label={t('team.helpers.dialog.changeLabel')}
              title={t('team.helpers.dialog.changeLabel')}
              onClick={() => {
                setChosen(null)
                add.reset()
              }}
            >
              {t('team.helpers.dialog.change')}
            </Button>
          </div>
        </div>
      ) : (
        <Field label={t('team.helpers.dialog.search')} hint={t('team.helpers.dialog.searchHint')} htmlFor={`${id}-search`}>
          <TextInput
            id={`${id}-search`}
            type="search"
            role="combobox"
            aria-expanded={showList}
            aria-controls={showList ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={showList ? `${listId}-${Math.min(active, options.length - 1)}` : undefined}
            value={query}
            maxLength={100}
            autoComplete="off"
            spellCheck={false}
            placeholder={t('team.helpers.dialog.placeholder')}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            onKeyDown={onKeyDown}
          />
        </Field>
      )}

      {!chosen && (
        <div className="min-h-11">
          {!typed ? (
            query.trim() !== '' && <p className="text-sm text-muted">{t('team.helpers.dialog.minLetters')}</p>
          ) : candidates.isError ? (
            <p role="alert" className="text-sm text-danger">
              {t('common.error')}
            </p>
          ) : candidates.isPending || (candidates.isFetching && options.length === 0) ? (
            <p className="text-sm text-muted">{t('team.helpers.dialog.searching')}</p>
          ) : options.length === 0 ? (
            <p className="text-sm text-muted">{t('team.helpers.dialog.noMatch', { q: q.trim() })}</p>
          ) : (
            <ul
              id={listId}
              role="listbox"
              aria-label={t('team.helpers.dialog.results')}
              className="flex max-h-64 flex-col overflow-y-auto rounded-control border border-hairline p-1"
            >
              {options.map((candidate, index) => (
                <li
                  key={candidate.id}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={index === active}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(candidate)}
                  onMouseEnter={() => setActive(index)}
                  className={cx(
                    'flex min-h-11 cursor-pointer flex-col justify-center rounded-lg px-3 py-1.5 sm:min-h-9',
                    index === active && 'bg-hover',
                  )}
                >
                  <span className="text-sm font-semibold">{candidate.name}</span>
                  {candidate.teams.length > 0 && <span className="text-xs text-muted">{candidate.teams.join(', ')}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <Field label={t('team.helpers.dialog.note')} hint={t('team.helpers.dialog.noteHint')} htmlFor={`${id}-note`}>
        <TextInput id={`${id}-note`} value={note} maxLength={300} autoComplete="off" onChange={(e) => setNote(e.target.value)} />
      </Field>

      {chosen && (
        <p className="flex items-start gap-2 text-sm text-muted">
          <Chip accent className="shrink-0">
            {t('roles.helper')}
          </Chip>
          <span>{t('team.helpers.dialog.reminder', { name: chosen.name, team: team.name })}</span>
        </p>
      )}
      {add.isError && (
        <p role="alert" className="text-sm text-danger">
          {t('team.helpers.dialog.failed', { detail: add.error instanceof ApiError ? add.error.detail : t('common.error') })}
        </p>
      )}

      <div className="flex flex-wrap justify-end gap-2 border-t border-hairline pt-4">
        <Button onClick={onDone}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={!chosen || add.isPending}>
          {add.isPending ? t('team.helpers.dialog.adding') : t('team.helpers.dialog.submit')}
        </Button>
      </div>
    </form>
  )
}

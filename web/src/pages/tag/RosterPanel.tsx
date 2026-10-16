import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { Select, TextInput } from '../../components/Field'
import { TrashIcon } from '../../components/icons'
import { Modal } from '../../components/Modal'
import { Table, TBody, Td, Th, THead } from '../../components/Table'
import { api, ApiError } from '../../lib/api'
import { cx } from '../../lib/cx'
import { useRosterCandidates } from '../../lib/queries'
import { keys } from '../../lib/queryClient'
import { newId } from '../../lib/taggingStore'
import { useToast } from '../../lib/toast'
import type { RosterCandidate, RosterEntry, RosterEntryIn } from '../../lib/types'
import { useUi, useView, useWorkspace } from './context'

/** Roster chips (PLAN §9.1). A click starts a tag for that number. */
export function RosterPanel() {
  const { t } = useTranslation()
  const { controller } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const [editing, setEditing] = useState(false)
  const us = view.roster.filter((r) => r.side === 'us')
  const them = view.roster.filter((r) => r.side === 'them')
  const idle = !ui.entry && !ui.proposal

  const chip = (r: RosterEntry) => (
    <li key={`${r.side}${r.number}`}>
      <button
        type="button"
        disabled={!idle}
        onClick={() => controller.startEntry(`${r.side === 'them' ? 'a' : ''}${r.number}`)}
        title={r.name ?? undefined}
        aria-label={t('tagging.roster.startTag', { player: r.name ? `#${r.number} ${r.name}` : `#${r.number}` })}
        className={cx(
          'inline-flex min-h-9 min-w-9 cursor-pointer items-center justify-center gap-0.5 rounded-lg border border-hairline px-2 text-sm font-bold tabular-nums hover:bg-sunken disabled:cursor-default disabled:opacity-60',
          r.is_libero && 'border-accent',
        )}
      >
        {r.number}
        {r.is_libero && <span className="text-xs font-semibold text-muted">{t('tagging.roster.liberoShort')}</span>}
      </button>
    </li>
  )

  return (
    <section aria-labelledby="roster-title" className="flex flex-col gap-1 rounded-2xl border border-hairline bg-surface px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="roster-title" className="font-semibold">
          {t('tagging.roster.title')}
        </h2>
        <Button variant="ghost" onClick={() => setEditing(true)}>
          {t('tagging.roster.edit')}
        </Button>
      </div>
      <div className="grid grid-cols-[3.5rem_1fr] items-start gap-2 text-sm">
        <span className="pt-2 font-semibold text-muted">{t('tagging.us')}</span>
        {us.length > 0 ? <ul className="flex flex-wrap gap-1">{us.map(chip)}</ul> : <p className="pt-2 text-muted">{t('tagging.roster.none')}</p>}
        <span className="pt-2 font-semibold text-muted">{t('tagging.them')}</span>
        {them.length > 0 ? <ul className="flex flex-wrap gap-1">{them.map(chip)}</ul> : <p className="pt-2 text-muted">{t('tagging.roster.none')}</p>}
      </div>
      {editing && <RosterEditor onClose={() => setEditing(false)} />}
    </section>
  )
}

interface Row {
  key: string
  number: string
  member_id: number | null
  name: string | null
  position: string | null
  is_libero: boolean
}

function parseNumber(value: string): number | null {
  if (!/^\d{1,2}$/.test(value.trim())) return null
  return Number(value.trim())
}

/** Numbers, liberos and opponent numbers of this video (PUT /roster). Only the team's people can be added. */
function RosterEditor({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const client = useQueryClient()
  const toast = useToast()
  const { video, store } = useWorkspace()
  const view = useView()
  const candidates = useRosterCandidates(video.id)
  const [rows, setRows] = useState<Row[]>(() =>
    view.roster
      .filter((r) => r.side === 'us')
      .map((r) => ({ key: newId(), number: String(r.number), member_id: r.member_id, name: r.name, position: r.position, is_libero: r.is_libero })),
  )
  const [them, setThem] = useState(() =>
    view.roster
      .filter((r) => r.side === 'them')
      .map((r) => r.number)
      .join(' '),
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState('')

  // The team's players (any season) not on the list yet.
  const linked = new Set(rows.map((r) => r.member_id).filter((m) => m !== null))
  const available = (candidates.data ?? []).filter((p) => !linked.has(p.member_id))
  const label = (p: RosterCandidate) => (p.number === null ? p.name : `#${p.number} ${p.name}`)

  const patch = (key: string, change: Partial<Row>) => setRows((list) => list.map((r) => (r.key === key ? { ...r, ...change } : r)))

  const add = (value: string) => {
    setAdding('')
    if (value === '') return
    if (value === 'number') {
      setRows((list) => [...list, { key: newId(), number: '', member_id: null, name: null, position: null, is_libero: false }])
      return
    }
    const person = available.find((p) => String(p.member_id) === value)
    if (!person) return
    setRows((list) => [
      ...list,
      { key: newId(), number: person.number === null ? '' : String(person.number), member_id: person.member_id, name: person.name, position: null, is_libero: false },
    ])
  }

  const validate = (): RosterEntryIn[] | string => {
    const entries: RosterEntryIn[] = []
    const seen = new Set<string>()
    for (const row of rows) {
      const number = parseNumber(row.number)
      if (number === null) return t('tagging.roster.errors.invalid')
      if (seen.has(`us:${number}`)) return t('tagging.roster.errors.duplicate', { number })
      seen.add(`us:${number}`)
      entries.push({ side: 'us', number, member_id: row.member_id, position: row.position, is_libero: row.is_libero })
    }
    const parts = them.split(/[\s,;]+/).filter(Boolean)
    for (const part of parts) {
      const number = parseNumber(part)
      if (number === null) return t('tagging.roster.errors.themInvalid')
      if (seen.has(`them:${number}`)) return t('tagging.roster.errors.duplicate', { number })
      seen.add(`them:${number}`)
      entries.push({ side: 'them', number, is_libero: false })
    }
    if (entries.length === 0) return t('tagging.roster.errors.empty')
    return entries
  }

  const save = async () => {
    const entries = validate()
    if (typeof entries === 'string') {
      setError(entries)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.putRoster(video.id, entries)
      // Tags follow the new numbers on the server: reload, with queued changes staying on top.
      const fresh = await client.fetchQuery({ queryKey: keys.tagging(video.id), queryFn: ({ signal }) => api.tagging(video.id, signal), staleTime: 0 })
      store.replaceBase(fresh)
      toast.success(t('tagging.roster.saved'))
      onClose()
    } catch (e) {
      setError(t('tagging.roster.saveFailed', { detail: e instanceof ApiError ? e.detail : t('common.error') }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      title={t('tagging.roster.editorTitle')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={busy}>
            {busy ? t('common.saving') : t('common.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">{t('tagging.roster.editorHint')}</p>
        <Table caption={t('tagging.us')}>
          <THead>
            <tr>
              <Th className="w-20">{t('tagging.roster.number')}</Th>
              <Th>{t('tagging.roster.name')}</Th>
              <Th>{t('tagging.roster.libero')}</Th>
              <Th>
                <span className="sr-only">{t('tagging.table.actions')}</span>
              </Th>
            </tr>
          </THead>
          <TBody>
            {rows.map((row) => (
              <tr key={row.key}>
                <Td tight>
                  <TextInput
                    inline
                    className="w-16 tabular-nums"
                    inputMode="numeric"
                    maxLength={2}
                    value={row.number}
                    aria-label={t('tagging.roster.numberOf', { name: row.name ?? t('tagging.roster.noName') })}
                    onChange={(e) => patch(row.key, { number: e.target.value })}
                  />
                </Td>
                <Td className={cx(!row.name && 'text-muted')}>{row.name ?? t('tagging.roster.noName')}</Td>
                <Td tight>
                  <label className="inline-flex min-h-11 items-center gap-2 sm:min-h-9">
                    <input
                      type="checkbox"
                      className="size-4 accent-current"
                      checked={row.is_libero}
                      onChange={(e) => patch(row.key, { is_libero: e.target.checked })}
                    />
                    <span className="sr-only">{t('tagging.roster.liberoOf', { name: row.name ?? `#${row.number}` })}</span>
                  </label>
                </Td>
                <Td tight className="text-right">
                  <Button
                    variant="ghostDanger"
                    size="icon"
                    aria-label={t('tagging.roster.remove', { player: row.name ?? `#${row.number}` })}
                    onClick={() => setRows((list) => list.filter((r) => r.key !== row.key))}
                  >
                    <TrashIcon className="size-4" />
                  </Button>
                </Td>
              </tr>
            ))}
          </TBody>
        </Table>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-semibold" htmlFor={`${id}-add`}>
            {t('tagging.roster.add')}
          </label>
          <Select id={`${id}-add`} value={adding} onChange={(e) => add(e.target.value)}>
            <option value="">{t('tagging.roster.choose')}</option>
            {available.length > 0 && (
              <optgroup label={t('tagging.roster.teamPlayers')}>
                {available.map((p) => (
                  <option key={p.member_id} value={p.member_id}>
                    {label(p)}
                  </option>
                ))}
              </optgroup>
            )}
            <option value="number">{t('tagging.roster.numberOnly')}</option>
          </Select>
          {candidates.isError && <p className="text-xs text-muted">{t('tagging.roster.candidatesFailed')}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-semibold" htmlFor={`${id}-them`}>
            {t('tagging.roster.themTitle')}
          </label>
          <TextInput
            id={`${id}-them`}
            value={them}
            onChange={(e) => setThem(e.target.value)}
            inputMode="numeric"
            aria-describedby={`${id}-them-hint`}
          />
          <p id={`${id}-them-hint`} className="text-xs text-muted">
            {t('tagging.roster.themHint')}
          </p>
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Modal>
  )
}

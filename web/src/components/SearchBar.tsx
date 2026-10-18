import '../i18n/pages'
import { useId, useMemo, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { useDeleteFilter, useSavedFilters, useSaveFilter } from '../lib/filters'
import { applyHint, formatTerms, parseSearch, searchHints, type SearchHint } from '../lib/search'
import { termLabel } from '../lib/searchLabels'
import { useSideNames } from '../lib/sides'
import { useToast } from '../lib/toast'
import type { RosterEntry, Taxonomy } from '../lib/types'
import { Button } from './Button'
import { TextInput } from './Field'
import { BookmarkIcon, CloseIcon, SearchIcon, TrashIcon } from './icons'
import { Popover } from './Popover'

/** The team saved filters belong to (any season of it), and whether the viewer may share with it. */
export interface FilterTeam {
  id: number
  canShare: boolean
  /** "H3 2026/27". */
  label: string
}

interface SearchBarProps {
  value: string
  onChange: (text: string) => void
  taxonomy: Taxonomy | null
  /** Our players, for "#" suggestions and names on the chips. */
  roster?: readonly RosterEntry[]
  /** The match's set numbers (suggestions after "set"). */
  sets?: readonly number[]
  /** Saved filters of this team; null: none here. */
  team: FilterTeam | null
  className?: string
}

/**
 * The search bar (PLAN §9.7): a few words over tags and rallies (src/lib/search.ts), with
 * suggestions while typing, what it understood as chips (× takes one out), and the saved
 * filters of the person and the team.
 */
export function SearchBar({ value, onChange, taxonomy, roster = [], sets, team, className }: SearchBarProps) {
  const { t, i18n } = useTranslation()
  const id = useId()
  const names = useSideNames()
  const lang = i18n.language === 'de' ? 'de' : 'en'
  const parsed = useMemo(() => parseSearch(value, taxonomy), [value, taxonomy])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const hints = useMemo(() => searchHints(value, { lang, roster, sets }), [value, lang, roster, sets])
  const showHints = open && hints.length > 0
  const skills = parsed.search.codes
  const ctx = { taxonomy, roster, names, skills }

  const pick = (hint: SearchHint) => {
    onChange(applyHint(value, hint))
    setActive(0)
    setOpen(true)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!showHints) {
        setOpen(true)
        return
      }
      event.preventDefault()
      setActive((n) => (n + (event.key === 'ArrowDown' ? 1 : hints.length - 1)) % hints.length)
    } else if (event.key === 'Enter' && showHints) {
      event.preventDefault()
      const hint = hints[Math.min(active, hints.length - 1)]
      if (hint) pick(hint)
    } else if (event.key === 'Escape' && showHints) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
  }

  return (
    <div className={cx('flex min-w-0 flex-col gap-2', className)}>
      <div className="flex items-start gap-2">
        <div className="relative min-w-0 flex-1">
          <label htmlFor={`${id}-input`} className="sr-only">
            {t('search.label')}
          </label>
          <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
          <TextInput
            id={`${id}-input`}
            // Not type="search": its own clear button would double ours.
            type="text"
            inputMode="search"
            enterKeyHint="search"
            role="combobox"
            aria-expanded={showHints}
            aria-controls={`${id}-hints`}
            aria-autocomplete="list"
            aria-activedescendant={showHints ? `${id}-hint-${Math.min(active, hints.length - 1)}` : undefined}
            autoComplete="off"
            spellCheck={false}
            value={value}
            placeholder={t('search.placeholder')}
            maxLength={300}
            className="pr-9 pl-9"
            onChange={(e) => {
              onChange(e.target.value)
              setActive(0)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onKeyDown={onKeyDown}
          />
          {value && (
            <button
              type="button"
              onClick={() => onChange('')}
              aria-label={t('search.clear')}
              title={t('search.clear')}
              className="absolute top-1/2 right-1 grid size-9 -translate-y-1/2 cursor-pointer place-items-center rounded-control text-muted hover:bg-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-ring sm:size-7"
            >
              <CloseIcon className="size-4" />
            </button>
          )}
          {showHints && (
            <ul
              id={`${id}-hints`}
              role="listbox"
              aria-label={t('search.hintsLabel')}
              className="absolute top-full right-0 left-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-card border border-hairline bg-surface p-1 shadow-raised"
            >
              {hints.map((hint, i) => (
                <li
                  key={`${hint.insert}-${hint.kind}`}
                  id={`${id}-hint-${i}`}
                  role="option"
                  aria-selected={i === active}
                  // Before the input's blur: picking keeps the focus in the box.
                  onMouseDown={(event) => {
                    event.preventDefault()
                    pick(hint)
                  }}
                  onMouseEnter={() => setActive(i)}
                  className={cx('flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg px-2.5 text-sm sm:min-h-9', i === active && 'bg-accent-soft text-on-accent-soft')}
                >
                  <span className="shrink-0 font-mono font-semibold whitespace-nowrap">{hint.label}</span>
                  <span className="min-w-0 truncate text-xs text-muted">{hint.detail ?? t(`search.kinds.${hint.kind}`)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {team && <SavedFilters team={team} text={value} onApply={onChange} />}
      </div>
      {(parsed.terms.length > 0 || parsed.unknown.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 text-sm" aria-live="polite">
          {parsed.terms.length > 0 && <span className="text-xs font-semibold text-muted">{t('search.understood')}</span>}
          {parsed.terms.map((term, i) => {
            const label = termLabel(t, term, ctx)
            return (
              <span key={`${i}-${label}`} className="inline-flex items-center gap-0.5 rounded-full bg-accent-soft py-0.5 pr-0.5 pl-2.5 text-xs leading-5 font-semibold text-on-accent-soft">
                {label}
                <button
                  type="button"
                  aria-label={t('search.remove', { term: label })}
                  title={t('search.remove', { term: label })}
                  onClick={() => onChange(formatTerms(parsed.terms.filter((_, j) => j !== i), lang, taxonomy))}
                  className="grid size-6 cursor-pointer place-items-center rounded-full hover:bg-surface/60 focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <CloseIcon className="size-3.5" />
                </button>
              </span>
            )
          })}
          {parsed.unknown.length > 0 && <span className="text-xs text-danger">{t('search.unknown', { words: parsed.unknown.join(', ') })}</span>}
        </div>
      )}
    </div>
  )
}

/** The person's saved filters and the team's shared ones: use one, delete one, save this search. */
function SavedFilters({ team, text, onApply }: { team: FilterTeam; text: string; onApply: (text: string) => void }) {
  const { t } = useTranslation()
  return (
    <Popover
      label={
        <>
          <BookmarkIcon className="size-4" />
          <span className="hidden sm:inline">{t('search.saved.button')}</span>
        </>
      }
      ariaLabel={t('search.saved.button')}
      title={t('search.saved.button')}
      align="end"
      chevron
      panelClassName="w-80"
    >
      {(close) => <SavedPanel team={team} text={text} onApply={(query) => {
        onApply(query)
        close()
      }} />}
    </Popover>
  )
}

function SavedPanel({ team, text, onApply }: { team: FilterTeam; text: string; onApply: (text: string) => void }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const filters = useSavedFilters(team.id)
  const save = useSaveFilter()
  const remove = useDeleteFilter()
  const [title, setTitle] = useState('')
  const [share, setShare] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const query = text.trim()

  const submit = () => {
    if (!query) {
      setError(t('search.saved.nothing'))
      return
    }
    setError(null)
    save.mutate(
      { body: { title: (title.trim() || query).slice(0, 120), query, visibility: share ? 'team' : 'private', team_id: team.id } },
      {
        onSuccess: () => {
          setTitle('')
          toast.success(t('search.saved.saved'))
        },
        onError: (e) => setError(t('search.saved.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') })),
      },
    )
  }

  return (
    <div className="flex flex-col gap-2 p-1.5">
      <h3 className="text-sm font-semibold">{t('search.saved.title')}</h3>
      {filters.isPending ? null : (filters.data ?? []).length === 0 ? (
        <p className="text-sm text-muted">{t('search.saved.empty')}</p>
      ) : (
        <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
          {(filters.data ?? []).map((f) => (
            <li key={f.id} className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => onApply(f.query)}
                aria-label={t('search.saved.apply', { title: f.title })}
                className="flex min-h-11 min-w-0 flex-1 cursor-pointer flex-col items-start justify-center rounded-lg px-2 text-left hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring sm:min-h-9"
              >
                <span className="w-full truncate text-sm font-semibold">{f.title}</span>
                <span className="w-full truncate font-mono text-xs text-muted">{f.query}</span>
                {f.visibility === 'team' && (
                  <span className="text-xs text-muted">{f.mine ? t('search.saved.sharedByYou') : t('search.saved.sharedBy', { name: f.owner_name ?? '–' })}</span>
                )}
              </button>
              {f.can_delete && (
                <Button
                  variant="ghostDanger"
                  size="icon"
                  aria-label={t('search.saved.delete', { title: f.title })}
                  title={t('search.saved.delete', { title: f.title })}
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(f, {
                      onSuccess: () => toast.success(t('search.saved.deleted')),
                      onError: () => toast.error(t('search.saved.deleteFailed')),
                    })
                  }
                >
                  <TrashIcon className="size-4" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-2 border-t border-hairline pt-2"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <h3 className="text-sm font-semibold">{t('search.saved.saveTitle')}</h3>
        <label htmlFor={`${id}-name`} className="sr-only">
          {t('search.saved.name')}
        </label>
        <TextInput id={`${id}-name`} value={title} placeholder={query || t('search.saved.name')} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
        {team.canShare && (
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm sm:min-h-9">
            <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} className="size-4 accent-accent" />
            {t('search.saved.share', { team: team.label })}
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" disabled={save.isPending || !query}>
          {t('search.saved.save')}
        </Button>
      </form>
    </div>
  )
}

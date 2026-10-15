import { useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import type { Person } from '../lib/clips'
import { sortComments } from '../lib/comments'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { newId } from '../lib/id'
import { formatClock } from '../lib/labels'
import { useAddComment, useComments, useDeleteComment, useEditComment, useVideoPeople } from '../lib/library'
import { insertMention, matchPeople, mentionAt, mentionedIds, splitMentions, type MentionQuery } from '../lib/mentions'
import { usePlayhead, type Playhead } from '../lib/playhead'
import { useToast } from '../lib/toast'
import type { Audience, Comment } from '../lib/types'
import { Badge } from './Badge'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { Select } from './Field'
import { PencilIcon, TrashIcon } from './icons'
import { LoadError } from './Message'
import { Spinner } from './Spinner'

const textarea =
  'w-full rounded-xl border border-hairline bg-surface px-3 py-2 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring'

interface CommentsSectionProps {
  videoId: string
  meId: number
  playhead: Playhead
  onSeek: (t_ms: number) => void
}

/**
 * Comments on a video, pinned to a moment, to the team, its coaches or one person (PLAN §6.8).
 * Who can be mentioned or addressed comes from the API: the team's people in any season.
 */
export function CommentsSection({ videoId, meId, playhead, onSeek }: CommentsSectionProps) {
  const { t } = useTranslation()
  const id = useId()
  const comments = useComments(videoId)
  const sorted = useMemo(() => sortComments(comments.data ?? []), [comments.data])
  const people = useVideoPeople(videoId)
  const everyone: readonly Person[] = people.data ?? []

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('comments.title')}
      </h2>
      <Composer videoId={videoId} people={everyone.filter((p) => p.member_id !== meId)} loaded={people.isSuccess} playhead={playhead} />
      {comments.isPending ? (
        <Spinner />
      ) : comments.isError ? (
        <LoadError onRetry={() => void comments.refetch()} />
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted">{t('comments.empty')}</p>
      ) : (
        <ol className="flex flex-col divide-y divide-hairline rounded-2xl border border-hairline bg-surface" aria-label={t('comments.title')}>
          {sorted.map((comment) => (
            <CommentItem key={comment.id} comment={comment} videoId={videoId} people={everyone} onSeek={onSeek} />
          ))}
        </ol>
      )}
    </section>
  )
}

function Composer({ videoId, people, loaded, playhead }: { videoId: string; people: readonly Person[]; loaded: boolean; playhead: Playhead }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const add = useAddComment(videoId)
  const [body, setBody] = useState('')
  const [picked, setPicked] = useState<Person[]>([])
  const [atTime, setAtTime] = useState(true)
  const [audience, setAudience] = useState<Audience>('team')
  const [recipient, setRecipient] = useState('')
  const [error, setError] = useState<string | null>(null)
  const seconds = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 1000))

  const ready = body.trim().length > 0 && (audience !== 'person' || recipient !== '')

  const submit = () => {
    if (!ready) return
    const text = body.trim().slice(0, 4000)
    add.mutate(
      {
        id: newId(),
        target_type: 'video',
        t_ms: atTime ? playhead.getState().t_ms : null,
        body: text,
        mentions: mentionedIds(text, picked),
        audience,
        recipient: audience === 'person' ? Number(recipient) : null,
      },
      {
        onSuccess: () => {
          setBody('')
          setPicked([])
          setError(null)
          // The next comment goes to the team again unless chosen otherwise.
          setAudience('team')
          setRecipient('')
          toast.success(t('comments.posted'))
        },
        onError: (e) => setError(t('comments.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') })),
      },
    )
  }

  return (
    <form
      className="flex flex-col gap-2 rounded-2xl border border-hairline bg-surface p-3"
      aria-label={t('comments.new')}
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <label htmlFor={`${id}-body`} className="sr-only">
        {t('comments.label')}
      </label>
      <MentionTextarea
        id={`${id}-body`}
        value={body}
        people={people}
        placeholder={t('comments.placeholder')}
        onChange={setBody}
        onPick={(person) => setPicked((list) => (list.some((p) => p.member_id === person.member_id) ? list : [...list, person]))}
        onSubmit={submit}
      />
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm font-semibold sm:min-h-9">
          <input type="checkbox" checked={atTime} onChange={(e) => setAtTime(e.target.checked)} className="size-4 accent-accent" />
          <span className="tabular-nums">{t('comments.atTime', { time: formatClock(seconds * 1000, { tenths: false }) })}</span>
        </label>
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-audience`}>
          {t('comments.audience')}
          <Select id={`${id}-audience`} inline value={audience} onChange={(e) => setAudience(e.target.value as Audience)}>
            <option value="team">{t('comments.audienceTeam')}</option>
            <option value="coaches">{t('comments.audienceCoaches')}</option>
            <option value="person" disabled={people.length === 0}>
              {t('comments.audiencePerson')}
            </option>
          </Select>
        </label>
        {audience === 'person' && (
          <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-recipient`}>
            {t('comments.recipient')}
            <Select id={`${id}-recipient`} inline value={recipient} onChange={(e) => setRecipient(e.target.value)}>
              <option value="">{t('comments.choose')}</option>
              {people.map((p) => (
                <option key={p.member_id} value={p.member_id}>
                  {p.number !== null ? `#${p.number} ${p.name}` : p.name}
                </option>
              ))}
            </Select>
          </label>
        )}
        <Button type="submit" variant="primary" className="ml-auto" disabled={!ready || add.isPending}>
          {add.isPending ? t('comments.posting') : t('comments.post')}
        </Button>
      </div>
      {loaded && people.length === 0 && <p className="text-xs text-muted">{t('comments.noPeople')}</p>}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

interface MentionTextareaProps {
  id: string
  value: string
  people: readonly Person[]
  placeholder: string
  onChange: (value: string) => void
  onPick: (person: Person) => void
  /** Ctrl/Cmd+Enter. */
  onSubmit: () => void
}

/** A textarea that offers the team's people after an @ (arrows to choose, Enter or Tab to pick, Esc to close). */
function MentionTextarea({ id, value, people, placeholder, onChange, onPick, onSubmit }: MentionTextareaProps) {
  const { t } = useTranslation()
  const ref = useRef<HTMLTextAreaElement>(null)
  const caret = useRef<number | null>(null)
  const [query, setQuery] = useState<MentionQuery | null>(null)
  // After a mention is put in, the caret goes right behind it before the next key arrives.
  useLayoutEffect(() => {
    if (caret.current === null || !ref.current) return
    ref.current.setSelectionRange(caret.current, caret.current)
    caret.current = null
  })
  const [active, setActive] = useState(0)
  const options = query ? matchPeople(people, query.query) : []
  const open = options.length > 0
  const listId = `${id}-people`

  const update = (text: string, caret: number) => {
    onChange(text)
    const next = mentionAt(text, caret)
    setQuery(next)
    if (next?.query !== query?.query) setActive(0)
  }

  const pick = (person: Person) => {
    const element = ref.current
    if (!element || !query) return
    const result = insertMention(value, element.selectionStart, query, person.name)
    caret.current = result.caret
    onChange(result.text)
    onPick(person)
    setQuery(null)
    element.focus()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (open) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((i) => (i + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length)
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        const person = options[Math.min(active, options.length - 1)]
        if (person) {
          event.preventDefault()
          pick(person)
        }
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setQuery(null)
        return
      }
    }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="relative">
      <textarea
        ref={ref}
        id={id}
        value={value}
        rows={3}
        maxLength={4000}
        placeholder={placeholder}
        aria-autocomplete="list"
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        className={textarea}
        onChange={(e) => update(e.target.value, e.target.selectionStart)}
        onKeyDown={onKeyDown}
        onClick={(e) => setQuery(mentionAt(value, e.currentTarget.selectionStart))}
        onBlur={() => setQuery(null)}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={t('comments.suggestions')}
          className="absolute inset-x-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-xl border border-hairline bg-surface p-1 shadow-lg"
        >
          {options.map((person, index) => (
            <li
              key={person.member_id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              // mousedown, so the textarea keeps the focus.
              onMouseDown={(e) => {
                e.preventDefault()
                pick(person)
              }}
              className={cx('flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm sm:min-h-9', index === active && 'bg-sunken font-semibold')}
            >
              {person.number !== null ? `#${person.number} ${person.name}` : person.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function CommentItem({ comment, videoId, people, onSeek }: { comment: Comment; videoId: string; people: readonly Person[]; onSeek: (t_ms: number) => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const edit = useEditComment(videoId)
  const remove = useDeleteComment(videoId)
  const [editing, setEditing] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const mentioned = people.filter((p) => comment.mentions.includes(p.member_id))
  const parts = splitMentions(comment.body, mentioned)
  const author = comment.author_name ?? t('comments.unknownAuthor')

  return (
    <li className="flex flex-col gap-1.5 p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="font-semibold">{author}</span>
        {comment.t_ms !== null ? (
          <button
            type="button"
            onClick={() => onSeek(comment.t_ms ?? 0)}
            aria-label={t('comments.goTo', { time: formatClock(comment.t_ms, { tenths: false }) })}
            className="min-h-11 cursor-pointer rounded-lg px-1 font-mono font-semibold text-link tabular-nums underline-offset-2 hover:underline sm:min-h-0"
          >
            {formatClock(comment.t_ms, { tenths: false })}
          </button>
        ) : (
          <span className="text-xs text-muted">{t('comments.general')}</span>
        )}
        {comment.audience === 'coaches' && <Badge>{t('comments.forCoaches')}</Badge>}
        {comment.audience === 'person' && <Badge>{t('comments.toPerson', { name: comment.recipient_name ?? t('comments.someone') })}</Badge>}
        <span className="text-xs text-muted tabular-nums">
          {formatDateTime(comment.created_at)}
          {comment.edited_at && ` · ${t('comments.edited')}`}
        </span>
        <span className="ml-auto flex items-center">
          {comment.mine && editing === null && (
            <Button variant="ghost" size="icon" aria-label={t('comments.edit')} onClick={() => setEditing(comment.body)}>
              <PencilIcon className="size-4" />
            </Button>
          )}
          {comment.can_delete && (
            <Button variant="ghostDanger" size="icon" aria-label={t('comments.delete')} onClick={() => setConfirm(true)}>
              <TrashIcon className="size-4" />
            </Button>
          )}
        </span>
      </div>
      {editing === null ? (
        <p className="text-sm wrap-anywhere whitespace-pre-wrap">
          {parts.map((part, i) =>
            part.mention ? (
              <strong key={i} className="font-semibold text-link">
                {part.text}
              </strong>
            ) : (
              <span key={i}>{part.text}</span>
            ),
          )}
        </p>
      ) : (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            const body = editing.trim()
            if (!body) return
            edit.mutate(
              { comment, body },
              { onSuccess: () => setEditing(null), onError: () => toast.error(t('comments.editFailed')) },
            )
          }}
        >
          <textarea
            aria-label={t('comments.label')}
            className={textarea}
            rows={3}
            maxLength={4000}
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button onClick={() => setEditing(null)}>{t('common.cancel')}</Button>
            <Button type="submit" variant="primary" disabled={!editing.trim() || edit.isPending}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      )}
      <ConfirmDialog
        open={confirm}
        title={t('comments.deleteTitle')}
        confirmLabel={t('comments.deleteConfirm')}
        danger
        busy={remove.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() =>
          remove.mutate(comment, {
            onSuccess: () => toast.success(t('comments.deleted')),
            onError: () => toast.error(t('comments.deleteFailed')),
            onSettled: () => setConfirm(false),
          })
        }
      >
        {t('comments.deleteBody')}
      </ConfirmDialog>
    </li>
  )
}

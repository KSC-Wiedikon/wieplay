import type { TFunction } from 'i18next'
import { useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Field, Select, TextInput } from '../components/Field'
import { ChevronDownIcon, ChevronUpIcon, PlusIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { ReelPlayer } from '../components/ReelPlayer'
import { SearchBar } from '../components/SearchBar'
import { PageSpinner, Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError } from '../lib/api'
import { cx } from '../lib/cx'
import { formatDate, formatDuration } from '../lib/format'
import { skillByCode } from '../lib/grammar'
import { gradeLabel, pieceFacts, pieceLabel, skillLabel } from '../lib/labels'
import {
  pieceToItem,
  useLineageTeam,
  useMyClips,
  useReelItems,
  useReelPreview,
  useReels,
  useSaveReel,
  useTaxonomy,
  useTeamPeople,
} from '../lib/library'
import { useMe, useTeam, useTeams, useTeamSeasons } from '../lib/queries'
import { reelTimeline } from '../lib/reelPlayer'
import { compareTeams } from '../lib/teams'
import { compactSearch, isEmptySearch, parseSearch } from '../lib/search'
import { NO_GRADE } from '../lib/stats'
import { useToast } from '../lib/toast'
import type { Clip, Piece, Reel, ReelIn, ReelQuery, Side, Taxonomy, Visibility } from '../lib/types'
import { isUuid } from '../lib/video'

const LIMIT = 200

/** A query settles 300 ms after the last change before it is previewed. */
function useSettled(query: ReelQuery | null): ReelQuery | null {
  const text = query === null ? '' : JSON.stringify(query)
  const [settled, setSettled] = useState(text)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(text), 300)
    return () => window.clearTimeout(timer)
  }, [text])
  return useMemo(() => (settled ? (JSON.parse(settled) as ReelQuery) : null), [settled])
}

function saveError(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return t('reels.builder.forbidden')
  return t('reels.builder.failed', { detail: error instanceof ApiError ? error.detail : t('common.error') })
}

/** /reels/new (highlights; `?kind=manual` from clips) and /reels/:id/edit. */
export function ReelBuilderPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const [params] = useSearchParams()
  const reels = useReels()
  if (id !== undefined) {
    if (!isUuid(id)) return <Message>{t('reels.notFound')}</Message>
    if (reels.isPending) return <PageSpinner />
    if (reels.isError) return <LoadError onRetry={() => void reels.refetch()} />
    const reel = reels.data.find((r) => r.id === id && r.mine)
    if (!reel) return <Message>{t('reels.notFound')}</Message>
    return reel.kind === 'smart' ? <SmartEdit reel={reel} /> : <ManualBuilder reel={reel} />
  }
  if (params.get('kind') === 'manual') return <ManualBuilder reel={null} />
  const team = Number(params.get('team'))
  const member = Number(params.get('member'))
  const side = params.get('side')
  // From a number in the season stats: ?codes=A&grades=%23,none&side=them
  const list = (name: string) => (params.get(name) ?? '').split(',').filter(Boolean).slice(0, 10)
  return (
    <SmartBuilder
      reel={null}
      initial={{
        teamId: Number.isInteger(team) && team > 0 ? team : null,
        season: params.get('season'),
        member: Number.isInteger(member) && member > 0 ? member : null,
        codes: list('codes'),
        grades: list('grades').map((g) => (g === NO_GRADE ? null : g)),
        side: side === 'us' || side === 'them' ? side : null,
        // The words of a search ("set 3 from 4"): from a filtered season number or a zone.
        text: (params.get('q') ?? '').slice(0, 300),
      }}
    />
  )
}

interface Initial {
  teamId: number | null
  /** null: the team's season; '' all seasons. */
  season: string | null
  member: number | null
  codes: string[]
  /** null: tags without a grade. */
  grades: (string | null)[]
  side: Side | null
  /** The search bar's words (src/lib/search.ts). */
  text: string
}

function SmartEdit({ reel }: { reel: Reel }) {
  const query = reel.query ?? {}
  const { team, pending } = useLineageTeam(query.lineage_id ?? null)
  if (pending) return <PageSpinner />
  return (
    <SmartBuilder
      reel={reel}
      initial={{
        teamId: team?.id ?? null,
        season: query.season ?? '',
        member: query.member_id ?? null,
        codes: query.codes ?? [],
        grades: query.grades ?? [],
        side: query.side ?? null,
        text: query.text ?? '',
      }}
    />
  )
}

function Chips({ label, options, value, onChange }: { label: string; options: { value: string; label: string; title?: string }[]; value: string[]; onChange: (value: string[]) => void }) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 text-xs font-semibold">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
          const on = value.includes(option.value)
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={on}
              title={option.title}
              onClick={() => onChange(on ? value.filter((v) => v !== option.value) : [...value, option.value])}
              className={cx(
                'min-h-11 cursor-pointer rounded-control border px-3 text-sm font-semibold sm:min-h-9',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
                on ? 'border-transparent bg-accent text-on-accent shadow-card' : 'border-hairline-strong bg-surface text-fg hover:bg-sunken',
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

function SmartBuilder({ reel, initial }: { reel: Reel | null; initial: Initial }) {
  const { t } = useTranslation()
  const id = useId()
  const me = useMe()
  const teams = useTeams()
  const sorted = useMemo(() => [...(teams.data ?? [])].sort(compareTeams), [teams.data])
  const [teamId, setTeamId] = useState<number | null>(initial.teamId)
  const chosenId = teamId ?? me.data?.teams[0]?.id ?? sorted[0]?.id ?? null
  const team = useTeam(chosenId ?? 0)
  const detail = chosenId !== null && team.data?.id === chosenId ? team.data : undefined
  const seasons = useTeamSeasons(chosenId ?? 0, chosenId !== null)
  const [season, setSeason] = useState<string | null>(initial.season)
  const [member, setMember] = useState<number | null>(initial.member)
  const [side, setSide] = useState<Side | null>(initial.side)
  const [codes, setCodes] = useState<string[]>(initial.codes)
  const [grades, setGrades] = useState<(string | null)[]>(initial.grades)
  const [text, setText] = useState(initial.text)
  const taxonomy = useTaxonomy(detail?.sport ?? null)
  const parsed = useMemo(() => parseSearch(text, taxonomy.data ?? null), [text, taxonomy.data])
  const searching = !isEmptySearch(parsed.search)
  const { people } = useTeamPeople(detail)
  const meId = me.data?.member.id
  const meName = me.data?.member.name ?? ''
  const rel = detail?.rel
  const choices = useMemo(() => {
    const list = [...people]
    if (meId !== undefined && rel?.includes('player') && !list.some((p) => p.member_id === meId)) {
      list.unshift({ member_id: meId, name: meName, number: null })
    }
    // A saved reel's player who is no longer on the list stays choosable.
    if (member !== null && !list.some((p) => p.member_id === member)) list.push({ member_id: member, name: `#${member}`, number: null })
    return list
  }, [people, meId, meName, rel, member])

  const effectiveSeason = season === null ? (detail?.season ?? null) : season || null
  const query: ReelQuery | null = detail
    ? {
        lineage_id: detail.lineage_id,
        ...(effectiveSeason ? { season: effectiveSeason } : {}),
        ...(member !== null ? { member_id: member } : side ? { side } : {}),
        ...(codes.length > 0 ? { codes } : {}),
        ...(grades.length > 0 ? { grades } : {}),
        // A search on top (PLAN §9.7): what it names, and the words to show again.
        ...(searching ? { search: compactSearch(parsed.search), text: text.trim() } : {}),
        limit: LIMIT,
      }
    : null
  const settled = useSettled(query)
  const preview = useReelPreview(settled)
  const pieces = preview.data ?? []

  const skill = codes.length === 1 && taxonomy.data ? skillByCode(taxonomy.data, codes[0] ?? '') : null
  const player = choices.find((p) => p.member_id === member)
  const suggested = [
    player?.name ?? detail?.name,
    codes.map((c) => skillLabel(t, taxonomy.data ? skillByCode(taxonomy.data, c) : null, c)).join(', '),
    grades.map((g) => (g === null ? t('reels.builder.noGrade') : skill ? `${g} ${gradeLabel(t, skill, g)}` : g)).join(', '),
    searching ? text.trim() : null,
    effectiveSeason,
  ]
    .filter(Boolean)
    .join(' · ')

  if (teams.isPending || (chosenId !== null && team.isPending)) return <PageSpinner />
  if (chosenId === null) return <Message>{t('reels.builder.noTeams')}</Message>
  if (team.isError || !detail) return <LoadError onRetry={() => void team.refetch()} />
  // The team's seasons I can see, newest first; its own and a saved reel's season are always there.
  const seasonChoices = [
    ...new Set([...(seasons.data ?? []).map((s) => s.season), detail.season, ...(effectiveSeason ? [effectiveSeason] : [])]),
  ].sort((a, b) => b.localeCompare(a))

  return (
    // A form: it keeps a readable width on wide screens.
    <div className="flex max-w-5xl flex-col gap-6">
      <title>{`${reel ? t('reels.builder.editTitle', { title: reel.title }) : t('reels.builder.newTitle')} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <Link to="/reels" className="font-semibold link">
            {t('reels.back')}
          </Link>
        }
        title={reel ? t('reels.builder.editTitle', { title: reel.title }) : t('reels.builder.newTitle')}
      />

      <section aria-labelledby={`${id}-filters`} className="flex flex-col gap-3">
        <h2 id={`${id}-filters`} className="text-lg font-semibold">
          {t('reels.builder.filters')}
        </h2>
        <Card className="flex flex-col gap-4 p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label={t('reels.builder.team')} htmlFor={`${id}-team`}>
              <Select
                id={`${id}-team`}
                value={chosenId}
                onChange={(e) => {
                  setTeamId(Number(e.target.value))
                  setMember(null)
                  setSeason(null)
                }}
              >
                {sorted.map((team) => (
                  <option key={team.id} value={team.id}>{`${team.name} · ${team.season}`}</option>
                ))}
              </Select>
            </Field>
            <Field label={t('reels.builder.season')} htmlFor={`${id}-season`}>
              <Select id={`${id}-season`} value={effectiveSeason ?? ''} onChange={(e) => setSeason(e.target.value)}>
                {seasonChoices.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
                <option value="">{t('reels.builder.allSeasons')}</option>
              </Select>
            </Field>
            <Field label={t('reels.builder.player')} htmlFor={`${id}-player`}>
              <Select id={`${id}-player`} value={member ?? ''} onChange={(e) => setMember(e.target.value ? Number(e.target.value) : null)}>
                <option value="">{t('reels.builder.anyone')}</option>
                {choices.map((p) => (
                  <option key={p.member_id} value={p.member_id}>
                    {p.number !== null ? `#${p.number} ${p.name}` : p.name}
                  </option>
                ))}
              </Select>
            </Field>
            {member === null && (
              <Field label={t('reels.builder.side')} htmlFor={`${id}-side`}>
                <Select id={`${id}-side`} value={side ?? ''} onChange={(e) => setSide(e.target.value ? (e.target.value as Side) : null)}>
                  <option value="">{t('reels.builder.bothSides')}</option>
                  <option value="us">{detail?.name ?? t('tagging.us')}</option>
                  <option value="them">{t('reels.builder.opponents')}</option>
                </Select>
              </Field>
            )}
          </div>
          {detail.sport === 'volleyball' && (
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold">{t('search.label')}</span>
              <SearchBar
                value={text}
                onChange={setText}
                taxonomy={taxonomy.data ?? null}
                team={{ id: detail.id, canShare: detail.can_share, label: `${detail.name} ${detail.season}` }}
              />
            </div>
          )}
          {taxonomy.data && (
            <>
              <Chips
                label={t('reels.builder.skills')}
                value={codes}
                onChange={setCodes}
                options={taxonomy.data.skills.map((s) => ({ value: s.code, label: skillLabel(t, s) }))}
              />
              <Chips
                label={t('reels.builder.grades')}
                value={grades.map((g) => g ?? NO_GRADE)}
                onChange={(picked) => setGrades(picked.map((g) => (g === NO_GRADE ? null : g)))}
                options={[
                  ...taxonomy.data.grades.map((g) => ({ value: g, label: skill ? `${g} ${gradeLabel(t, skill, g)}` : g })),
                  { value: NO_GRADE, label: t('reels.builder.noGrade') },
                ]}
              />
            </>
          )}
        </Card>
      </section>

      <Preview
        pieces={pieces}
        taxonomy={taxonomy.data ?? null}
        pending={preview.isPending}
        error={preview.isError}
        fetching={preview.isFetching}
        title={suggested}
        onRetry={() => void preview.refetch()}
      />

      <SaveForm
        reel={reel}
        suggested={suggested}
        canShare={detail.can_share}
        teamLabel={`${detail.name} ${detail.season}`}
        build={(title, visibility) => ({ title, kind: 'smart', query: query ?? {}, visibility, lineage_id: detail.lineage_id })}
      />
    </div>
  )
}

interface PreviewProps {
  pieces: Piece[]
  taxonomy: Taxonomy | null
  pending: boolean
  error: boolean
  fetching: boolean
  title: string
  onRetry: () => void
}

function Preview({ pieces, taxonomy, pending, error, fetching, title, onRetry }: PreviewProps) {
  const { t } = useTranslation()
  const id = useId()
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {t('reels.builder.preview')}
        </h2>
        {!pending && !error && (
          <p className="text-sm text-muted tabular-nums" role="status">
            {`${t('reels.count', { count: pieces.length })} · ${formatDuration(reelTimeline(pieces).total_ms)}`}
            {fetching && ` · ${t('reels.builder.updating')}`}
          </p>
        )}
      </div>
      {pending ? (
        <Spinner />
      ) : error ? (
        <Message action={<Button onClick={onRetry}>{t('common.retry')}</Button>}>
          <span role="alert">{t('reels.builder.previewFailed')}</span>
        </Message>
      ) : pieces.length === 0 ? (
        <Message>{t('reels.builder.previewEmpty')}</Message>
      ) : (
        <>
          {pieces.length >= LIMIT && <p className="text-sm text-muted">{t('reels.builder.limited', { count: LIMIT })}</p>}
          <ReelPlayer pieces={pieces} title={title} taxonomy={taxonomy} />
        </>
      )}
    </section>
  )
}

interface SaveFormProps {
  reel: Reel | null
  suggested: string
  canShare: boolean
  teamLabel: string | null
  /** The reel to save, with the form's title and visibility. */
  build: (title: string, visibility: Visibility) => ReelIn
  children?: ReactNode
  disabled?: boolean
}

function SaveForm({ reel, suggested, canShare, teamLabel, build, children, disabled }: SaveFormProps) {
  const { t } = useTranslation()
  const id = useId()
  const navigate = useNavigate()
  const toast = useToast()
  const save = useSaveReel()
  const [title, setTitle] = useState(reel?.title ?? '')
  const [visibility, setVisibility] = useState<Visibility>(reel?.visibility ?? 'private')
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const name = (title.trim() || suggested).slice(0, 200)
    if (!name) {
      setError(t('reels.builder.titleMissing'))
      return
    }
    setError(null)
    save.mutate(
      { id: reel?.id, body: build(name, visibility) },
      {
        onSuccess: (saved) => {
          toast.success(t('reels.builder.saved'))
          void navigate(`/reels/${saved.id}`)
        },
        onError: (e) => setError(saveError(t, e)),
      },
    )
  }

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('reels.builder.saveTitle')}
      </h2>
      <form
        className="flex flex-col gap-4 rounded-card border border-hairline bg-surface shadow-card p-4"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <Field label={t('reels.builder.titleLabel')} htmlFor={`${id}-name`}>
          <TextInput id={`${id}-name`} value={title} placeholder={suggested} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        {children}
        {(canShare || reel?.visibility === 'team') && teamLabel && (
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-semibold">{t('reels.builder.who')}</legend>
            {(['private', 'team'] as const).map((v) => (
              <label key={v} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm sm:min-h-9">
                <input type="radio" name={`${id}-who`} checked={visibility === v} onChange={() => setVisibility(v)} className="size-4 accent-accent" />
                {v === 'private' ? t('reels.builder.private') : t('reels.builder.shareTeam', { team: teamLabel })}
              </label>
            ))}
          </fieldset>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div>
          <Button type="submit" variant="primary" disabled={save.isPending || disabled}>
            {save.isPending ? t('common.saving') : t('reels.builder.save')}
          </Button>
        </div>
      </form>
    </section>
  )
}

// A reel from clips (and moments added with "Add to reel")

function clipPiece(clip: Clip): Piece {
  return {
    video_id: clip.video_id,
    start_ms: clip.start_ms,
    end_ms: clip.end_ms,
    event_id: clip.event_id,
    clip_id: clip.id,
    caption: clip.title,
    video_title: clip.video_title,
    recorded_on: clip.recorded_on,
    code: null,
    grade: null,
    side: null,
    number: null,
    member_id: null,
    player_name: null,
    score_us: null,
    score_them: null,
  }
}

function ManualBuilder({ reel }: { reel: Reel | null }) {
  const items = useReelItems(reel?.id ?? '', reel !== null)
  if (reel && items.isPending) return <PageSpinner />
  if (reel && items.isError) return <LoadError onRetry={() => void items.refetch()} />
  return <ManualForm reel={reel} initial={items.data ?? []} />
}

function ManualForm({ reel, initial }: { reel: Reel | null; initial: Piece[] }) {
  const { t } = useTranslation()
  const id = useId()
  const teams = useTeams()
  const clips = useMyClips()
  const [pieces, setPieces] = useState<Piece[]>(initial)
  const targets = useMemo(() => (teams.data ?? []).filter((team) => team.can_share).sort(compareTeams), [teams.data])
  const [shareTeam, setShareTeam] = useState<number | null>(null)
  const shareId = shareTeam ?? targets[0]?.id ?? null
  const share = targets.find((team) => team.id === shareId)

  const move = (index: number, by: -1 | 1) =>
    setPieces((list) => {
      const next = [...list]
      const [item] = next.splice(index, 1)
      if (item) next.splice(index + by, 0, item)
      return next
    })

  return (
    <div className="flex flex-col gap-6">
      <title>{`${reel ? t('reels.builder.editTitle', { title: reel.title }) : t('reels.builder.newManualTitle')} · WiePlay`}</title>
      <div className="flex flex-col gap-1">
        <Link to="/reels" className="text-sm font-semibold link">
          {t('reels.back')}
        </Link>
        <h1 className="text-2xl font-bold wrap-break-word">{reel ? t('reels.builder.editTitle', { title: reel.title }) : t('reels.builder.newManualTitle')}</h1>
      </div>

      <section aria-labelledby={`${id}-items`} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h2 id={`${id}-items`} className="text-lg font-semibold">
            {t('reels.builder.items')}
          </h2>
          <p className="text-sm text-muted tabular-nums">
            {`${t('reels.count', { count: pieces.length })} · ${formatDuration(reelTimeline(pieces).total_ms)}`}
          </p>
        </div>
        {pieces.length === 0 ? (
          <Message>{t('reels.builder.itemsEmpty')}</Message>
        ) : (
          <Card className="overflow-hidden">
            <Table caption={t('reels.builder.items')}>
              <THead>
                <tr>
                  <Th className="w-0">#</Th>
                  <Th>{t('reels.builder.caption')}</Th>
                  <Th className="hidden sm:table-cell">{t('clips.columns.video')}</Th>
                  <Th className="text-right">
                    <span className="sr-only">{t('tagging.table.actions')}</span>
                  </Th>
                </tr>
              </THead>
              <TBody>
                {pieces.map((piece, index) => {
                  const facts = pieceFacts(piece)
                  return (
                    <tr key={`${piece.video_id}-${piece.start_ms}-${index}`}>
                      <Td className="text-muted tabular-nums">{index + 1}</Td>
                      <Td tight className="min-w-40">
                        <TextInput
                          aria-label={t('reels.builder.captionFor', { n: index + 1 })}
                          value={piece.caption ?? ''}
                          placeholder={pieceLabel(t, { ...piece, caption: null }, null)}
                          maxLength={200}
                          onChange={(e) => setPieces((list) => list.map((p, i) => (i === index ? { ...p, caption: e.target.value || null } : p)))}
                        />
                        <span className="block px-1 pt-1 text-xs text-muted tabular-nums sm:hidden">
                          {[facts, piece.video_title, piece.recorded_on ? formatDate(piece.recorded_on) : null, formatDuration(piece.end_ms - piece.start_ms)]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      </Td>
                      <Td className="hidden sm:table-cell">
                        {piece.video_title}
                        <span className="block text-xs text-muted tabular-nums">
                          {[facts, piece.recorded_on ? formatDate(piece.recorded_on) : null, formatDuration(piece.end_ms - piece.start_ms)].filter(Boolean).join(' · ')}
                        </span>
                      </Td>
                      <Td tight className="text-right whitespace-nowrap">
                        <Button variant="ghost" size="icon" disabled={index === 0} aria-label={t('reels.builder.moveUp', { n: index + 1 })} onClick={() => move(index, -1)}>
                          <ChevronUpIcon className="size-4" />
                        </Button>
                        <Button variant="ghost" size="icon" disabled={index === pieces.length - 1} aria-label={t('reels.builder.moveDown', { n: index + 1 })} onClick={() => move(index, 1)}>
                          <ChevronDownIcon className="size-4" />
                        </Button>
                        <Button variant="ghostDanger" size="icon" aria-label={t('reels.builder.remove', { n: index + 1 })} onClick={() => setPieces((list) => list.filter((_, i) => i !== index))}>
                          <TrashIcon className="size-4" />
                        </Button>
                      </Td>
                    </tr>
                  )
                })}
              </TBody>
            </Table>
          </Card>
        )}
      </section>

      <section aria-labelledby={`${id}-clips`} className="flex flex-col gap-3">
        <h2 id={`${id}-clips`} className="text-lg font-semibold">
          {t('reels.builder.addClips')}
        </h2>
        {clips.isPending ? (
          <Spinner />
        ) : clips.isError ? (
          <LoadError onRetry={() => void clips.refetch()} />
        ) : clips.data.length === 0 ? (
          <Message>{t('reels.builder.noClips')}</Message>
        ) : (
          <Card className="overflow-hidden">
            <ul className="flex max-h-80 flex-col divide-y divide-hairline overflow-y-auto">
              {clips.data.map((clip) => (
                <li key={clip.id} className="flex items-center justify-between gap-3 px-3 py-1">
                  <span className="min-w-0">
                    <span className="block font-semibold wrap-break-word">{clip.title}</span>
                    <span className="block text-xs text-muted tabular-nums">
                      {[clip.video_title, formatDate(clip.recorded_on), formatDuration(clip.end_ms - clip.start_ms)].join(' · ')}
                    </span>
                  </span>
                  <Button size="icon" aria-label={t('reels.builder.add', { title: clip.title })} onClick={() => setPieces((list) => [...list, clipPiece(clip)])}>
                    <PlusIcon className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </section>

      <SaveForm
        reel={reel}
        suggested=""
        canShare={share !== undefined}
        teamLabel={share ? `${share.name} ${share.season}` : null}
        disabled={pieces.length === 0}
        build={(title, visibility) => ({
          title,
          kind: 'manual',
          items: pieces.map(pieceToItem),
          visibility,
          // A team reel stays with its team unless another one is picked.
          lineage_id:
            visibility === 'team' && shareTeam === null && reel?.lineage_id
              ? reel.lineage_id
              : visibility === 'team' && share
                ? share.lineage_id
                : (reel?.lineage_id ?? null),
        })}
      >
        {targets.length > 1 && (
          <Field label={t('reels.builder.team')} htmlFor={`${id}-share`}>
            <Select id={`${id}-share`} value={shareId ?? ''} onChange={(e) => setShareTeam(Number(e.target.value))}>
              {targets.map((team) => (
                <option key={team.id} value={team.id}>{`${team.name} · ${team.season}`}</option>
              ))}
            </Select>
          </Field>
        )}
      </SaveForm>
    </div>
  )
}

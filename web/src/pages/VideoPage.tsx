import '../i18n/pages'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { buttonClasses } from '../components/buttonStyles'
import { Card } from '../components/Card'
import { ClipDialog } from '../components/ClipDialog'
import { ClipBar, ClipsSection } from '../components/ClipsSection'
import { CommentsSection } from '../components/CommentsSection'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ExportButton } from '../components/ExportButton'
import { AnalysisStatus } from '../components/AnalysisStatus'
import { AlertIcon, CheckIcon, ChevronRightIcon, PlayIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { MomentsStrip, type BreakMark, type StartMark } from '../components/MomentsStrip'
import { PageHeader } from '../components/PageHeader'
import { ProgressBar } from '../components/ProgressBar'
import { RallyPanel } from '../components/RallyPanel'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { UploadProgress } from '../components/UploadProgress'
import { VideoStatusBadge } from '../components/VideoBadges'
import { VideoPlayer } from '../components/VideoPlayer'
import { VIDEO_STATS_ID, VideoStats } from '../components/VideoStats'
import { ApiError, blockersOf } from '../lib/api'
import { clipRange, draftOf, nextInQueue, type ClipDraft, type PlayRange, type RangeQueue } from '../lib/clips'
import { commentMoments } from '../lib/comments'
import { PRE_ROLL_MS } from '../lib/condensed'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { formatClock, segmentKindLabel } from '../lib/labels'
import { useComments, useVideoClips } from '../lib/library'
import { Playhead } from '../lib/playhead'
import { useDeleteVideo, useMe, useTagging, useVideo, useVideoConfig } from '../lib/queries'
import { keys } from '../lib/queryClient'
import { sportOf, useSportAccent } from '../lib/sport'
import { isGap, isGapSuggestion, isSetSuggestion, isSuggestion } from '../lib/suggestions'
import type { StatPlay } from '../lib/stats'
import { useRangePlayback } from '../lib/useRangePlayback'
import { useToast } from '../lib/toast'
import type { Clip, ImportedFrom, Video } from '../lib/types'
import { matchFiles, useUpload, type FilePair } from '../lib/upload'
import { isUuid, kindLabel, parseVideoLink, platformName, VIDEO_ACCEPT, videoFacts } from '../lib/video'

export function VideoPage() {
  const { id } = useParams()
  if (!isUuid(id)) return <VideoNotFound />
  return <VideoView videoId={id} />
}

function VideoNotFound() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <title>nomal</title>
      <Message>{t('video.notFound')}</Message>
    </div>
  )
}

function VideoView({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const toast = useToast()
  const video = useVideo(videoId)
  const config = useVideoConfig()
  const remove = useDeleteVideo()
  const [confirmDelete, setConfirmDelete] = useState(false)
  useSportAccent(video.data ? sportOf(video.data.sport) : 'vb')

  if (video.isPending) return <PageSpinner />
  if (video.isError) {
    // Missing and not visible look the same on purpose.
    if (video.error instanceof ApiError && [403, 404, 422].includes(video.error.status)) return <VideoNotFound />
    return <LoadError onRetry={() => void video.refetch()} />
  }

  const v = video.data
  const enabled = config.data?.enabled !== false
  const kind = kindLabel(t, v.kind)

  const onDelete = () =>
    remove.mutate(v, {
      onSuccess: () => {
        toast.success(t('video.deleted'))
        void navigate(`/teams/${v.team_id}`, { replace: true })
      },
      onError: () => toast.error(t('video.deleteFailed')),
      onSettled: () => setConfirmDelete(false),
    })

  return (
    <div className="flex flex-col gap-6">
      <title>{`${v.title} · nomal`}</title>
      <PageHeader
        eyebrow={
          <>
            <Link to={`/teams/${v.team_id}`} className="font-semibold link">
              {`${v.team_name} ${v.team_season}`}
            </Link>
            <ChevronRightIcon className="size-4" />
            {kind && <Badge>{kind}</Badge>}
            {v.status !== 'ready' && <VideoStatusBadge status={v.status} />}
          </>
        }
        title={v.title}
        actions={
          v.can_delete && v.status !== 'uploading' ? (
            <Button variant="ghostDanger" onClick={() => setConfirmDelete(true)}>
              <TrashIcon className="size-4" />
              {t('video.delete')}
            </Button>
          ) : null
        }
        subtitle={
          <div className="flex flex-col gap-1">
            <span className="tabular-nums">{formatDate(v.recorded_on)}</span>
            {v.imported_from && <ImportOrigin origin={v.imported_from} />}
          </div>
        }
      />

      {v.status === 'uploading' ? (
        <UploadingPanel video={v} enabled={enabled} />
      ) : v.status === 'importing' || v.status === 'queued' || v.status === 'processing' ? (
        <Card className="flex flex-col gap-3 p-5">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-semibold">{t(`video.status.${v.status}`)}</h2>
            {v.progress !== null && <span className="tabular-nums">{Math.floor(v.progress * 100)} %</span>}
          </div>
          <ProgressBar value={v.progress} label={t(`video.status.${v.status}`)} />
          <p className="text-sm text-muted">
            {v.status === 'importing'
              ? t('video.importingBody')
              : v.status === 'queued'
                ? t('video.queuedBody')
                : t('video.processingBody')}
          </p>
        </Card>
      ) : v.status === 'failed' || v.status === 'refused' ? (
        <Problem video={v} />
      ) : enabled ? (
        <Ready video={v} />
      ) : (
        <Message>{t('video.notConfigured')}</Message>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={t('video.deleteTitle')}
        confirmLabel={t('video.delete')}
        danger
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={onDelete}
      >
        {t('video.deleteBody')}
      </ConfirmDialog>
    </div>
  )
}

/** "Imported from YouTube · KSC Wiedikon", linking to the original. */
function ImportOrigin({ origin }: { origin: ImportedFrom }) {
  const { t } = useTranslation()
  const text = [t('video.importedFrom', { platform: platformName(origin.platform) }), origin.channel]
    .filter(Boolean)
    .join(' · ')
  // Only a canonical YouTube or Twitch video address becomes a link, never other text.
  const href = parseVideoLink(origin.url)?.url
  return (
    <p className="text-sm text-muted">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          title={origin.title ?? undefined}
          className="link"
        >
          {text}
        </a>
      ) : (
        text
      )}
    </p>
  )
}

/** A failed or refused video; an import that failed or was refused says so. */
function Problem({ video }: { video: Video }) {
  const { t } = useTranslation()
  const refused = video.status === 'refused'
  // A failed job's error starts with its kind (pipeline.on_failed).
  const importFailed = !refused && video.error?.startsWith('import:') === true
  const title = refused
    ? video.source === 'import'
      ? t('video.refusedImportTitle')
      : t('video.refusedTitle')
    : importFailed
      ? t('video.importFailedTitle')
      : t('video.failedTitle')
  const body = refused
    ? video.source === 'import'
      ? t('video.refusedImportBody')
      : t('video.refusedBody')
    : importFailed
      ? t('video.importFailedBody')
      : t('video.failedBody')
  return (
    <Card className="flex flex-col gap-2 p-5">
      <h2 className="flex items-center gap-2 font-semibold text-danger">
        <AlertIcon />
        {title}
      </h2>
      <p className="text-sm">{body}</p>
      {video.error && <p className="text-sm wrap-anywhere whitespace-pre-wrap text-muted">{video.error}</p>}
    </Card>
  )
}

interface PlayItem extends PlayRange {
  key: string
  label: string
}

function clipItem(clip: Clip): PlayItem {
  return { key: clip.id, label: clip.title, start_ms: clip.start_ms, end_ms: clip.end_ms }
}

function Ready({ video }: { video: Video }) {
  const { t } = useTranslation()
  const tagging = useTagging(video.id, true)
  const clips = useVideoClips(video.id)
  const comments = useComments(video.id)
  const me = useMe()
  const [searchParams] = useSearchParams()
  const [element, setElement] = useState<HTMLVideoElement | null>(null)
  const [playhead] = useState(() => new Playhead())
  const [queue, setQueue] = useState<RangeQueue<PlayItem> | null>(null)
  // The playing moments came from a number in the stats: offer the way back to them.
  const [fromStats, setFromStats] = useState(false)
  const [marks, setMarks] = useState<{ in: number | null; out: number | null }>({ in: null, out: null })
  const [draft, setDraft] = useState<ClipDraft | null>(null)
  useEffect(() => {
    playhead.attach(element)
    return () => playhead.detach()
  }, [playhead, element])

  // Clips play from their start to their end, then stop or go on with the next one.
  const current = queue ? (queue.ranges[queue.index] ?? null) : null
  const play = useCallback(
    (ranges: readonly PlayItem[], index: number) => {
      const range = ranges[index]
      if (!range) return
      setQueue({ ranges, index })
      playhead.seek(range.start_ms)
      playhead.play()
    },
    [playhead],
  )
  useRangePlayback(element, current, {
    onEnd: (range) => {
      const next = queue && queue.ranges[queue.index] === range ? nextInQueue(queue) : null
      if (next) play(next.ranges, next.index)
      else {
        element?.pause()
        setQueue((q) => (q && q.ranges[q.index] === range ? null : q))
      }
    },
    onLeave: (range) => setQueue((q) => (q && q.ranges[q.index] === range ? null : q)),
  })

  // /videos/:id?clip=… (from "My clips"): play just that clip once the video can seek.
  const wanted = searchParams.get('clip')
  const started = useRef<string | null>(null)
  useEffect(() => {
    if (!wanted || started.current === wanted || !element || !clips.data) return
    const clip = clips.data.find((c) => c.id === wanted)
    if (!clip) return
    started.current = wanted
    const go = () => play([clipItem(clip)], 0)
    if (element.readyState >= 1) go()
    else element.addEventListener('loadedmetadata', go, { once: true })
  }, [wanted, element, clips.data, play])

  const { resolution, fps } = videoFacts(video)
  const facts = [formatDuration(video.duration_ms), resolution, fps ? t('video.fps', { fps }) : null].filter(Boolean)
  // Nomal's suggestions are not rallies until a coach reviews them (PLAN §10): the rally list,
  // skipping dead time and the counts leave them out.
  const data = useMemo(
    () => (tagging.data ? { ...tagging.data, segments: tagging.data.segments.filter((s) => !isSuggestion(s)) } : undefined),
    [tagging.data],
  )
  const rallyCount = data?.segments.filter((s) => s.kind === 'rally').length ?? 0
  const tagCount = data?.events.length ?? 0
  const showSummary = data !== undefined && (data.can_tag || tagCount > 0 || rallyCount > 0)
  const duration = video.duration_ms ?? playhead.getState().duration_ms
  // Breaks and set starts on the strip below the player; Nomal's open suggestions lighter (PLAN §10.3).
  const breaks = useMemo<BreakMark[]>(
    () =>
      (data?.segments ?? []).flatMap((s) =>
        isGap(s.kind)
          ? [{ id: s.id, start_ms: s.start_ms, end_ms: s.end_ms, kind: s.kind, label: segmentKindLabel(t, s.kind, video.sport), suggested: isGapSuggestion(s) }]
          : [],
      ),
    [data, t, video.sport],
  )
  const starts = useMemo<StartMark[]>(
    () =>
      video.sport === 'volleyball'
        ? (data?.periods ?? []).map((p) => ({ id: p.id, t_ms: p.start_ms, label: t('tagging.markers.setStart', { no: p.no }), suggested: isSetSuggestion(p) }))
        : [],
    [data, t, video.sport],
  )
  const firstServe = data?.first_serve_ms ?? null

  const seekTo = (t_ms: number) => {
    playhead.seek(t_ms)
    element?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }

  /** Skips the warm-up: plays from just before the match's first serve. */
  const startAtFirstServe = () => {
    if (firstServe === null) return
    setQueue(null)
    playhead.seek(Math.max(0, firstServe - PRE_ROLL_MS))
    playhead.play()
    element?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }

  const scrollToStats = () => document.getElementById(VIDEO_STATS_ID)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })

  const playStats = (plays: StatPlay[]) => {
    if (plays.length === 0) return
    setFromStats(true)
    play(plays, 0)
    element?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }

  const saveMarks = () => {
    const range = clipRange(marks.in, marks.out, playhead.getState().t_ms, video.duration_ms)
    if (!range) return
    setDraft({
      id: null,
      video_id: video.id,
      ...range,
      title: t('clips.dialog.defaultTitle', { time: formatClock(range.start_ms, { tenths: false }) }),
      note: null,
      event_id: null,
      visibility: 'private',
    })
  }

  return (
    <>
      {/* Wide screens: the player and its tools on the left, rallies, clips and comments beside it. */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_34rem] xl:items-start 2xl:grid-cols-[minmax(0,1fr)_38rem]">
        <div className="flex min-w-0 flex-col gap-3">
          <VideoPlayer video={video} videoRef={setElement} />
          {duration && ((clips.data?.length ?? 0) > 0 || comments.data?.some((c) => c.t_ms !== null) || breaks.length > 0 || starts.length > 0) ? (
            <MomentsStrip
              duration_ms={duration}
              ranges={(clips.data ?? []).map((c) => ({ id: c.id, start_ms: c.start_ms, end_ms: c.end_ms, shared: c.visibility === 'team' }))}
              marks={commentMoments(comments.data ?? [])}
              breaks={breaks}
              starts={starts}
              playhead={playhead}
              onSeek={(ms) => playhead.seek(ms)}
            />
          ) : null}
          {current && (
            <p className="flex flex-wrap items-center gap-2 text-sm" role="status">
              <span className="font-semibold">{t('clips.nowPlaying', { title: current.label })}</span>
              {queue && queue.ranges.length > 1 && (
                <span className="text-muted tabular-nums">{t('clips.queuePosition', { n: queue.index + 1, total: queue.ranges.length })}</span>
              )}
              <Button
                variant="ghost"
                onClick={() => {
                  element?.pause()
                  setQueue(null)
                }}
              >
                {t('clips.stop')}
              </Button>
              {fromStats && (
                <button type="button" onClick={scrollToStats} className="min-h-11 cursor-pointer px-2 font-semibold link sm:min-h-9">
                  {t('stats.backToStats')}
                </button>
              )}
            </p>
          )}
          {(facts.length > 0 || firstServe !== null) && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              {facts.length > 0 && <p className="text-sm text-muted tabular-nums">{facts.join(' · ')}</p>}
              {firstServe !== null && (
                <Button variant="ghost" onClick={startAtFirstServe} className="ml-auto">
                  <PlayIcon className="size-4" />
                  {t('tagging.markers.startAtFirstServe')}
                </Button>
              )}
            </div>
          )}
          <ClipBar
            playhead={playhead}
            clipIn={marks.in}
            clipOut={marks.out}
            onMark={(which, t_ms) => setMarks((m) => ({ ...m, [which]: t_ms }))}
            onClear={() => setMarks({ in: null, out: null })}
            onSave={saveMarks}
          />
          {showSummary && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="flex flex-wrap items-center gap-x-3 text-sm font-semibold tabular-nums">
                {/* Basketball marks no rallies. */}
                {data.sport === 'basketball'
                  ? t('tagging.tagCount', { count: tagCount })
                  : `${t('tagging.tagCount', { count: tagCount })} · ${t('tagging.rallyCount', { count: rallyCount })}`}
                {video.sport === 'volleyball' && tagCount > 0 && (
                  <button type="button" onClick={scrollToStats} className="min-h-11 cursor-pointer link sm:min-h-9">
                    {t('stats.jump')}
                  </button>
                )}
              </p>
              {data.can_tag && (
                // Tagging is a keyboard task: desktop only (PLAN §9.1).
                <div className="hidden lg:block">
                  <ButtonLink to={`/videos/${video.id}/tag`} variant="primary">
                    {t('tagging.tagThis')}
                  </ButtonLink>
                </div>
              )}
            </div>
          )}
          {data?.can_tag && video.sport === 'volleyball' && (
            <AnalysisStatus video={video} rallies={rallyCount} reviewHref={`/videos/${video.id}/tag?mode=review`} />
          )}
          {video.meets_standard === false && (
            <div className="flex flex-col gap-1 rounded-xl border border-hairline bg-surface p-3 text-sm">
              <p className="flex flex-wrap items-center gap-2 font-semibold">
                <AlertIcon className="size-4" />
                {t('video.belowStandard')}
                {video.standard_issues.map((issue) => (
                  <Badge key={issue}>{issue}</Badge>
                ))}
              </p>
              <p className="text-muted">{t('video.belowStandardNote')}</p>
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-6 xl:gap-8">
          {data && rallyCount > 0 && (
            <div>
              <RallyPanel
                tagging={data}
                duration_ms={video.duration_ms}
                element={element}
                playhead={playhead}
                suspended={current !== null}
                action={video.can_export ? <ExportButton request={{ kind: 'condensed', video_id: video.id }} /> : null}
              />
            </div>
          )}
          <div>
            <ClipsSection
              clips={clips}
              canManage={video.can_delete}
              canExport={video.can_export}
              playingId={current?.key ?? null}
              onPlay={(list, index, all) => {
                setFromStats(false)
                const items = list.map(clipItem)
                if (all) play(items, index)
                else play(items.slice(index, index + 1), 0)
              }}
              onEdit={(clip) => setDraft(draftOf(clip))}
            />
          </div>
          <div>
            <CommentsSection videoId={video.id} meId={me.data?.member.id ?? 0} playhead={playhead} onSeek={seekTo} />
          </div>
        </div>
        <ClipDialog
          draft={draft}
          canShare={video.can_share}
          duration_ms={video.duration_ms}
          onClose={() => setDraft(null)}
          onSaved={(clip) => {
            if (clip.id && draft?.id === null) setMarks({ in: null, out: null })
          }}
        />
      </div>
      {video.sport === 'volleyball' && <VideoStats video={video} tagging={data} onPlay={playStats} />}
    </>
  )
}

/** Files still to upload; the coach can pick them again to resume, or cancel the upload. */
function UploadingPanel({ video, enabled }: { video: Video; enabled: boolean }) {
  const { t } = useTranslation()
  const id = useId()
  const navigate = useNavigate()
  const client = useQueryClient()
  const toast = useToast()
  const upload = useUpload()
  const remove = useDeleteVideo()
  const [pairs, setPairs] = useState<FilePair[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const [missing, setMissing] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const state = upload.snapshot?.state
  const active = upload.snapshot !== null && state !== 'done'
  const canCancel = video.can_upload || video.can_delete

  // Done, or refused by the filming re-check on the last file: show the video's new status.
  const refused = blockersOf(upload.snapshot?.error) !== null
  useEffect(() => {
    if (state === 'done' || refused) void client.invalidateQueries({ queryKey: keys.video(video.id) })
  }, [state, refused, client, video.id])

  const done = (fileId: string, uploaded: boolean) =>
    uploaded || upload.snapshot?.files.some((f) => f.id === fileId && f.done) === true

  const onPick = (picked: File[]) => {
    const match = matchFiles(video.files, picked)
    if (match.unmatched.length > 0) {
      setProblem(t('video.resumeMismatch', { names: match.unmatched.map((f) => f.name).join(', ') }))
      return
    }
    setProblem(null)
    if (match.pairs.length === 0) return
    setMissing(match.missing.length > 0 ? t('video.resumeMissing', { names: match.missing.map((f) => f.name).join(', ') }) : null)
    setPairs(match.pairs)
    void upload.start(video, match.pairs)
  }

  const leave = () => {
    toast.success(t('video.cancelled'))
    void navigate(`/teams/${video.team_id}`, { replace: true })
  }

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby={`${id}-files`} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 id={`${id}-files`} className="text-lg font-semibold">
            {t('video.uploadingTitle')}
          </h2>
          <p className="text-sm text-muted">
            {t('video.uploadingBody')} {!video.can_upload && t('video.uploadingOther')}
          </p>
        </div>
        {video.files.length > 0 && (
        <Card className="overflow-hidden">
          <Table caption={t('video.uploadingTitle')}>
            <THead>
              <tr>
                <Th>{t('columns.file')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.size')}</Th>
                <Th className="text-right">{t('columns.status')}</Th>
              </tr>
            </THead>
            <TBody>
              {[...video.files]
                .sort((a, b) => a.part_no - b.part_no)
                .map((file) => (
                  <tr key={file.id}>
                    <Td className="break-all">{file.name}</Td>
                    <Td className="hidden whitespace-nowrap text-muted tabular-nums sm:table-cell">
                      {formatBytes(file.size)}
                    </Td>
                    <Td className="text-right whitespace-nowrap">
                      {done(file.id, file.uploaded) ? (
                        <span className="inline-flex items-center gap-1">
                          <CheckIcon className="size-4" />
                          {t('video.fileUploaded')}
                        </span>
                      ) : (
                        <span className="text-muted">{t('video.fileWaiting')}</span>
                      )}
                    </Td>
                  </tr>
                ))}
            </TBody>
          </Table>
        </Card>
        )}
      </section>

      {active && upload.snapshot ? (
        <UploadProgress
          snapshot={upload.snapshot}
          onRetry={() => void upload.start(upload.snapshot?.video ?? video, pairs)}
          onLeave={upload.stop}
          onCancel={async () => {
            try {
              await upload.cancel()
              leave()
            } catch {
              toast.error(t('video.cancelFailed'))
            }
          }}
        />
      ) : (
        <>
          {video.can_upload && enabled && (
            <section aria-labelledby={`${id}-resume`} className="flex flex-col gap-3">
              <h2 id={`${id}-resume`} className="text-lg font-semibold">
                {t('video.resumeTitle')}
              </h2>
              <Card className="flex flex-col gap-3 p-5">
                <p className="text-sm text-muted">{t('video.resumeHint')}</p>
                <label className={buttonClasses({ variant: 'primary' }, 'self-start')}>
                  <input
                    type="file"
                    multiple
                    accept={VIDEO_ACCEPT}
                    className="sr-only"
                    onChange={(e) => {
                      onPick(e.target.files ? [...e.target.files] : [])
                      e.target.value = ''
                    }}
                  />
                  {t('video.resumePick')}
                </label>
                {problem && (
                  <p role="alert" className="text-sm text-danger">
                    {problem}
                  </p>
                )}
                {missing && !problem && <p className="text-sm text-muted">{missing}</p>}
              </Card>
            </section>
          )}
          {canCancel && (
            <div>
              <Button variant="ghostDanger" onClick={() => setConfirmCancel(true)}>
                {t('video.cancelUpload')}
              </Button>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmCancel}
        title={t('video.cancelUploadTitle')}
        confirmLabel={t('video.cancelUpload')}
        cancelLabel={t('video.keepUploading')}
        danger
        busy={remove.isPending}
        onCancel={() => setConfirmCancel(false)}
        onConfirm={() =>
          remove.mutate(video, {
            onSuccess: leave,
            onError: () => toast.error(t('video.cancelFailed')),
            onSettled: () => setConfirmCancel(false),
          })
        }
      >
        {t('video.cancelUploadBody')}
      </ConfirmDialog>
    </div>
  )
}

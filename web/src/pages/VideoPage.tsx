import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertIcon, CheckIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { ProgressBar } from '../components/ProgressBar'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { UploadProgress } from '../components/UploadProgress'
import { VideoStatusBadge } from '../components/VideoBadges'
import { VideoPlayer } from '../components/VideoPlayer'
import { ApiError, blockersOf } from '../lib/api'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { useDeleteVideo, useVideo, useVideoConfig } from '../lib/queries'
import { keys } from '../lib/queryClient'
import { sportOf, useSportAccent } from '../lib/sport'
import { useToast } from '../lib/toast'
import type { Video } from '../lib/types'
import { matchFiles, useUpload, type FilePair } from '../lib/upload'
import { isUuid, VIDEO_ACCEPT, videoFacts } from '../lib/video'

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
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{t(`video.kind.${v.kind}`)}</Badge>
          {v.status !== 'ready' && <VideoStatusBadge status={v.status} />}
        </div>
        <h1 className="text-2xl font-bold wrap-break-word">{v.title}</h1>
        <p className="text-muted">
          <span className="tabular-nums">{formatDate(v.recorded_on)}</span>
          {' · '}
          <Link to={`/teams/${v.team_id}`} className="font-semibold text-link underline-offset-2 hover:underline">
            {`${v.team_name} ${v.team_season}`}
          </Link>
        </p>
      </div>

      {v.status === 'uploading' ? (
        <UploadingPanel video={v} enabled={enabled} />
      ) : v.status === 'queued' || v.status === 'processing' ? (
        <Card className="flex flex-col gap-3 p-5">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-semibold">{t(`video.status.${v.status}`)}</h2>
            {v.progress !== null && <span className="tabular-nums">{Math.floor(v.progress * 100)} %</span>}
          </div>
          <ProgressBar value={v.progress} label={t(`video.status.${v.status}`)} />
          <p className="text-sm text-muted">{v.status === 'queued' ? t('video.queuedBody') : t('video.processingBody')}</p>
        </Card>
      ) : v.status === 'failed' || v.status === 'refused' ? (
        <Card className="flex flex-col gap-2 p-5">
          <h2 className="flex items-center gap-2 font-semibold text-danger">
            <AlertIcon />
            {v.status === 'refused' ? t('video.refusedTitle') : t('video.failedTitle')}
          </h2>
          <p className="text-sm">{v.status === 'refused' ? t('video.refusedBody') : t('video.failedBody')}</p>
          {v.error && <p className="text-sm wrap-anywhere whitespace-pre-wrap text-muted">{v.error}</p>}
        </Card>
      ) : enabled ? (
        <Ready video={v} />
      ) : (
        <Message>{t('video.notConfigured')}</Message>
      )}

      {v.can_delete && v.status !== 'uploading' && (
        <div>
          <Button variant="ghostDanger" onClick={() => setConfirmDelete(true)}>
            {t('video.delete')}
          </Button>
        </div>
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

function Ready({ video }: { video: Video }) {
  const { t } = useTranslation()
  const { resolution, fps } = videoFacts(video)
  const facts = [formatDuration(video.duration_ms), resolution, fps ? t('video.fps', { fps }) : null].filter(Boolean)
  return (
    <div className="flex flex-col gap-3">
      <VideoPlayer video={video} />
      {facts.length > 0 && <p className="text-sm text-muted tabular-nums">{facts.join(' · ')}</p>}
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
                <label className="inline-flex min-h-11 cursor-pointer items-center justify-center self-start rounded-xl bg-accent px-4 text-sm font-semibold text-on-accent hover:brightness-95 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring sm:min-h-9">
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

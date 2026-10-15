import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { FilmIcon, UploadIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { Spinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead, Tr } from '../components/Table'
import { BlockerList, FilmingBadge, VideoStatusBadge } from '../components/VideoBadges'
import { ForbiddenError } from '../lib/api'
import { formatDate, formatDuration } from '../lib/format'
import {
  useRecordPaperConsent,
  useTeamConsent,
  useTeamVideos,
  useUpcomingFilming,
  useVideoConfig,
} from '../lib/queries'
import type { ConsentStatus, RosterConsent, TeamDetail, UpcomingFilming, Video } from '../lib/types'
import { consentOutdated, newestFirst } from '../lib/video'

function SectionHead({ id, title, hint, action }: { id: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-lg font-semibold">
          {title}
        </h2>
        {hint && <p className="text-sm text-muted">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

// Videos

export function VideosSection({ team }: { team: TeamDetail }) {
  const { t } = useTranslation()
  const id = useId()
  const config = useVideoConfig()
  const videos = useTeamVideos(team.id)
  const disabled = config.data?.enabled === false

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHead
        id={id}
        title={t('team.videos')}
        action={
          team.can_upload && config.data?.enabled ? (
            <ButtonLink to={`/teams/${team.id}/upload`} variant="primary">
              <UploadIcon />
              {t('team.upload')}
            </ButtonLink>
          ) : null
        }
      />
      {disabled ? (
        <Message>{t('video.notConfigured')}</Message>
      ) : videos.isPending ? (
        <Spinner />
      ) : videos.isError ? (
        <LoadError onRetry={() => void videos.refetch()} />
      ) : videos.data.length === 0 ? (
        <Message>{t('team.noVideos')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <VideoTable videos={newestFirst(videos.data)} />
        </Card>
      )}
    </section>
  )
}

function Poster({ video }: { video: Video }) {
  if (video.has_thumbs) {
    return (
      <img
        src={`/api/media/${video.id}/poster.jpg`}
        alt=""
        loading="lazy"
        width={64}
        height={36}
        className="aspect-video w-16 rounded-md bg-sunken object-cover"
      />
    )
  }
  return (
    <span className="grid aspect-video w-16 place-items-center rounded-md bg-sunken text-muted">
      <FilmIcon className="size-4" />
    </span>
  )
}

function VideoTable({ videos }: { videos: Video[] }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <Table caption={t('team.videos')}>
      <THead>
        <tr>
          <Th className="w-0">
            <span className="sr-only">{t('columns.preview')}</span>
          </Th>
          <Th>{t('columns.title')}</Th>
          <Th className="hidden sm:table-cell">{t('columns.date')}</Th>
          <Th className="hidden sm:table-cell">{t('columns.duration')}</Th>
          <Th>{t('columns.status')}</Th>
        </tr>
      </THead>
      <TBody>
        {videos.map((video) => (
          <Tr key={video.id} onActivate={() => navigate(`/videos/${video.id}`)}>
            <Td className="pr-0">
              <Poster video={video} />
            </Td>
            <Td>
              <Link to={`/videos/${video.id}`} className="font-semibold text-link underline-offset-2 hover:underline">
                {video.title}
              </Link>
              <span className="mt-0.5 block text-xs text-muted tabular-nums sm:hidden">
                {[formatDate(video.recorded_on), formatDuration(video.duration_ms)].filter(Boolean).join(' · ')}
              </span>
              <span className="mt-0.5 hidden text-xs text-muted sm:block">{t(`video.kind.${video.kind}`)}</span>
            </Td>
            <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDate(video.recorded_on)}</Td>
            <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
              {formatDuration(video.duration_ms) || '–'}
            </Td>
            <Td>
              <VideoStatusBadge status={video.status} />
            </Td>
          </Tr>
        ))}
      </TBody>
    </Table>
  )
}

// Filming allowed / blocked for the next games and trainings

export function FilmingSection({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const id = useId()
  const upcoming = useUpcomingFilming(teamId)
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHead id={id} title={t('team.filming')} hint={t('team.filmingHint')} />
      {upcoming.isPending ? (
        <Spinner />
      ) : upcoming.isError ? (
        <LoadError onRetry={() => void upcoming.refetch()} />
      ) : upcoming.data.length === 0 ? (
        <Message>{t('team.noUpcoming')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <FilmingTable items={upcoming.data} />
        </Card>
      )}
    </section>
  )
}

function FilmingTable({ items }: { items: UpcomingFilming[] }) {
  const { t } = useTranslation()
  return (
    <Table caption={t('team.filming')}>
      <THead>
        <tr>
          <Th>{t('columns.date')}</Th>
          <Th>{t('columns.what')}</Th>
          <Th className="hidden text-right sm:table-cell">{t('columns.filming')}</Th>
        </tr>
      </THead>
      <TBody>
        {items.map((item) => (
          <tr key={`${item.kind}-${item.id}`}>
            <Td className="whitespace-nowrap tabular-nums">{formatDate(item.date)}</Td>
            <Td>
              <span className="mb-1 block sm:hidden">
                <FilmingBadge allowed={item.allowed} />
              </span>
              <span className="block font-semibold">{t(`video.kind.${item.kind}`)}</span>
              {item.label && <span className="block text-muted">{item.label}</span>}
              {!item.allowed && item.blockers.length > 0 && (
                <div className="mt-2">
                  <BlockerList blockers={item.blockers} />
                </div>
              )}
            </Td>
            <Td className="hidden text-right sm:table-cell">
              <FilmingBadge allowed={item.allowed} />
            </Td>
          </tr>
        ))}
      </TBody>
    </Table>
  )
}

// Consent of the roster (coaches)

export function ConsentState({
  status,
  via,
  outdated = false,
}: {
  status: ConsentStatus | null
  via: string | null
  outdated?: boolean
}) {
  const { t } = useTranslation()
  const label = outdated
    ? t('teamConsent.older')
    : status === 'accepted'
      ? t('teamConsent.accepted')
      : status === 'withdrawn'
        ? t('teamConsent.withdrawn')
        : t('teamConsent.none')
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={status === 'accepted' && !outdated ? undefined : 'text-muted'}>{label}</span>
      {status && via === 'paper' && <Badge>{t('teamConsent.paper')}</Badge>}
    </span>
  )
}

export function ConsentSection({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const id = useId()
  const roster = useTeamConsent(teamId, true)
  const record = useRecordPaperConsent(teamId)
  const notice = useVideoConfig().data?.notice_version
  const [pending, setPending] = useState<{ player: RosterConsent; status: ConsentStatus } | null>(null)

  if (roster.isError && roster.error instanceof ForbiddenError) return null

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHead id={id} title={t('team.consent')} hint={t('teamConsent.hint')} />
      {roster.isPending ? (
        <Spinner />
      ) : roster.isError ? (
        <LoadError onRetry={() => void roster.refetch()} />
      ) : roster.data.length === 0 ? (
        <Message>{t('teamConsent.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('team.consent')}>
            <THead>
              <tr>
                <Th className="w-0">{t('columns.number')}</Th>
                <Th>{t('columns.name')}</Th>
                <Th className="hidden sm:table-cell">{t('columns.consent')}</Th>
                <Th>
                  <span className="sr-only">{t('columns.status')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {roster.data.map((player) => {
                // An answer to an older notice counts as no answer: offer the paper form again.
                const outdated = notice !== undefined && consentOutdated(player, notice)
                const next: ConsentStatus = player.status === 'accepted' && !outdated ? 'withdrawn' : 'accepted'
                return (
                  <tr key={player.member_id} className="[&>td]:align-middle">
                    <Td className="text-muted tabular-nums">{player.number ?? '–'}</Td>
                    <Td>
                      <span className="block font-semibold">{player.name}</span>
                      <span className="mt-0.5 block text-xs sm:hidden">
                        <ConsentState status={player.status} via={player.via} outdated={outdated} />
                      </span>
                    </Td>
                    <Td className="hidden sm:table-cell">
                      <ConsentState status={player.status} via={player.via} outdated={outdated} />
                    </Td>
                    <Td tight className="text-right">
                      <Button
                        className="px-3"
                        aria-label={
                          next === 'accepted'
                            ? t('teamConsent.recordAcceptFor', { name: player.name })
                            : t('teamConsent.recordWithdrawFor', { name: player.name })
                        }
                        onClick={() => setPending({ player, status: next })}
                      >
                        <span className="sm:hidden">
                          {next === 'accepted' ? t('teamConsent.recordAcceptShort') : t('teamConsent.recordWithdrawShort')}
                        </span>
                        <span className="hidden sm:inline">
                          {next === 'accepted' ? t('teamConsent.recordAccept') : t('teamConsent.recordWithdraw')}
                        </span>
                      </Button>
                    </Td>
                  </tr>
                )
              })}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={pending !== null}
        title={pending?.status === 'withdrawn' ? t('teamConsent.withdrawTitle') : t('teamConsent.acceptTitle')}
        confirmLabel={pending?.status === 'withdrawn' ? t('teamConsent.recordWithdraw') : t('teamConsent.recordAccept')}
        danger={pending?.status === 'withdrawn'}
        busy={record.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (!pending) return
          record.mutate(pending, { onSettled: () => setPending(null) })
        }}
      >
        {pending &&
          (pending.status === 'withdrawn'
            ? t('teamConsent.withdrawBody', { name: pending.player.name })
            : t('teamConsent.acceptBody', { name: pending.player.name }))}
      </ConfirmDialog>
    </section>
  )
}

import '../i18n/pages'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router'
import { ButtonLink } from '../components/Button'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { ApiError } from '../lib/api'
import { useTagging, useVideo } from '../lib/queries'
import { sportOf, useSportAccent } from '../lib/sport'
import { isUuid } from '../lib/video'
import { Workspace } from './tag/Workspace'

/** Wide enough for the workspace. Without matchMedia (tests): yes. */
function wideScreen(): boolean {
  return typeof window.matchMedia !== 'function' || window.matchMedia('(min-width: 1024px)').matches
}

/**
 * /videos/:id/tag — the tagging workspace (PLAN §9.1), desktop only; `?mode=review` opens the
 * review of Nomal's suggestions (PLAN §10.1), which smaller screens get on their own.
 */
export function TagPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  if (!isUuid(id)) return <Message>{t('video.notFound')}</Message>
  return <TagLoader videoId={id} />
}

function TagLoader({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  // Decided once, when the page opens: a narrower window later (or a screenshot tool resizing
  // the page for a moment) must not close a running session.
  const [desktop] = useState(wideScreen)
  const video = useVideo(videoId)
  const ready = video.data?.status === 'ready'
  // For the checks below only (who may tag, vocabulary, anything to review); the workspace
  // loads its data fresh.
  const tagging = useTagging(videoId, ready)
  useSportAccent(video.data ? sportOf(video.data.sport) : 'vb')

  const back = <ButtonLink to={`/videos/${videoId}`}>{t('tagging.back')}</ButtonLink>
  const phone = (
    <Message action={back}>
      <span>{t('tagging.phone')}</span>
    </Message>
  )
  if (!desktop && (video.isError || (video.data && !ready) || tagging.isError)) return phone
  if (video.isPending) return <PageSpinner />
  if (video.isError) {
    if (video.error instanceof ApiError && [403, 404, 422].includes(video.error.status)) {
      return <Message>{t('video.notFound')}</Message>
    }
    return <LoadError onRetry={() => void video.refetch()} />
  }
  if (!ready) return <Message action={back}>{t('tagging.notReady')}</Message>
  if (tagging.isError) {
    if (tagging.error instanceof ApiError && tagging.error.status === 404) return <Message>{t('video.notFound')}</Message>
    return <LoadError onRetry={() => void tagging.refetch()} />
  }
  if (tagging.isPending) return <PageSpinner />
  const bundle = tagging.data
  if (!bundle.taxonomy) return <Message action={back}>{t('tagging.noTaxonomy')}</Message>
  if (!bundle.can_tag) return <Message action={back}>{t('tagging.forbidden')}</Message>
  // Tagging is a keyboard task; reviewing Nomal's suggestions works by touch too (tablets).
  if (!desktop) {
    if (!bundle.segments.some((s) => s.source === 'model')) return phone
    return <Workspace video={video.data} taxonomy={bundle.taxonomy} narrow />
  }
  return <Workspace video={video.data} taxonomy={bundle.taxonomy} />
}

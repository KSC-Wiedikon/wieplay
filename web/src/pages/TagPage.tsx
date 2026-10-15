import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router'
import { ButtonLink } from '../components/Button'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { ApiError } from '../lib/api'
import { useTaggingForWorkspace, useVideo } from '../lib/queries'
import { sportOf, useSportAccent } from '../lib/sport'
import { useMediaQuery } from '../lib/useMediaQuery'
import { isUuid } from '../lib/video'
import { Workspace } from './tag/Workspace'

/** /videos/:id/tag — the tagging workspace (PLAN §9.1), desktop only. */
export function TagPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  if (!isUuid(id)) return <Message>{t('video.notFound')}</Message>
  return <TagLoader videoId={id} />
}

function TagLoader({ videoId }: { videoId: string }) {
  const { t } = useTranslation()
  const desktop = useMediaQuery('(min-width: 1024px)')
  const video = useVideo(videoId)
  const ready = video.data?.status === 'ready'
  const tagging = useTaggingForWorkspace(videoId, ready && desktop)
  useSportAccent(video.data ? sportOf(video.data.sport) : 'vb')

  const back = <ButtonLink to={`/videos/${videoId}`}>{t('tagging.back')}</ButtonLink>
  if (!desktop) {
    return (
      <Message action={back}>
        <span>{t('tagging.phone')}</span>
      </Message>
    )
  }
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
  // Never start from a cached copy: it may miss changes sent just before.
  if (tagging.isPending || !tagging.isFetchedAfterMount) return <PageSpinner />
  const bundle = tagging.data
  if (!bundle.taxonomy) return <Message action={back}>{t('tagging.noTaxonomy')}</Message>
  if (!bundle.can_tag) return <Message action={back}>{t('tagging.forbidden')}</Message>
  return <Workspace video={video.data} bundle={bundle} taxonomy={bundle.taxonomy} />
}

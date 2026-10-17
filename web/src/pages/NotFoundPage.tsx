import { useTranslation } from 'react-i18next'
import { ButtonLink } from '../components/Button'
import { AlertIcon, HomeIcon } from '../components/icons'
import { EmptyState } from '../components/Message'
import { PageHeader } from '../components/PageHeader'

export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('notFound.title')} · WiePlay`}</title>
      <PageHeader title={t('notFound.title')} />
      <EmptyState
        icon={<AlertIcon />}
        title={t('notFound.body')}
        action={
          <ButtonLink to="/" variant="primary">
            <HomeIcon className="size-4" />
            {t('notFound.back')}
          </ButtonLink>
        }
      />
    </div>
  )
}

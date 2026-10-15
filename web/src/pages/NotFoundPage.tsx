import { useTranslation } from 'react-i18next'
import { ButtonLink } from '../components/Button'
import { Message } from '../components/Message'

export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <title>{`${t('notFound.title')} · nomal`}</title>
      <h1 className="text-2xl font-bold">{t('notFound.title')}</h1>
      <Message action={<ButtonLink to="/">{t('notFound.back')}</ButtonLink>}>{t('notFound.body')}</Message>
    </div>
  )
}

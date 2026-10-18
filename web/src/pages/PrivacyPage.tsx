import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Mark, Wordmark } from '../components/Brand'
import { Card } from '../components/Card'

const SECTIONS = ['what', 'who', 'sheets', 'where', 'guests', 'keep', 'rights'] as const

/**
 * The short privacy notice (docs/PRIVACY.md), readable without a login: guest clubs accept it
 * when they set up their login. A draft until the club's privacy contact has reviewed it.
 */
export function PrivacyPage() {
  const { t } = useTranslation()
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8 sm:py-12">
      <title>{`${t('guests.privacy.title')} · WiePlay`}</title>
      <Link to="/" className="flex items-center gap-3 self-start" aria-label="WiePlay">
        <Mark className="size-10" />
        <Wordmark className="text-2xl" />
      </Link>
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t('guests.privacy.title')}</h1>
        <p className="text-sm text-muted">{t('guests.privacy.draft')}</p>
        <p>{t('guests.privacy.intro')}</p>
      </div>
      <Card className="flex flex-col gap-5 p-5 sm:p-6">
        {SECTIONS.map((key) => (
          <section key={key} aria-labelledby={`privacy-${key}`} className="flex flex-col gap-1">
            <h2 id={`privacy-${key}`} className="font-semibold">
              {t(`guests.privacy.sections.${key}.title`)}
            </h2>
            <p className="text-sm">{t(`guests.privacy.sections.${key}.body`)}</p>
          </section>
        ))}
      </Card>
    </main>
  )
}

import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router'
import { cx } from '../lib/cx'
import { useSession } from '../lib/session'
import { Message } from './Message'
import { PageHeader } from './PageHeader'

const sections = [
  { to: '/admin/teams', key: 'teams' },
  { to: '/admin/policies', key: 'policies' },
  { to: '/admin/workers', key: 'workers' },
  { to: '/admin/jobs', key: 'jobs' },
  { to: '/admin/import-channels', key: 'importChannels' },
] as const

export function AdminNav() {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('admin.nav.label')} className="-mx-4 overflow-x-auto border-b border-hairline px-4 sm:mx-0 sm:px-0">
      <div className="flex gap-1">
        {sections.map(({ to, key }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cx(
                // Tabs: the current section is underlined in the accent colour.
                '-mb-px inline-flex min-h-11 shrink-0 items-center border-b-2 px-3 text-sm font-semibold whitespace-nowrap',
                isActive ? 'border-accent text-fg' : 'border-transparent text-muted hover:border-hairline-strong hover:text-fg',
              )
            }
          >
            {t(`admin.nav.${key}`)}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

/** Heading, section navigation and the admins-only gate shared by the admin pages. */
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation()
  const me = useSession()
  return (
    <div className="flex flex-col gap-8">
      <title>{`${title} · nomal`}</title>
      <div className="flex flex-col gap-4">
        <PageHeader title={t('admin.title')} subtitle={t('admin.subtitle')} />
        <AdminNav />
      </div>
      {me.is_admin ? children : <Message>{t('admin.forbidden')}</Message>}
    </div>
  )
}

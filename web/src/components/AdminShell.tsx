import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router'
import { cx } from '../lib/cx'
import { useSession } from '../lib/session'
import { Message } from './Message'

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
    <nav aria-label={t('admin.nav.label')} className="-mx-1 flex flex-wrap gap-1">
      {sections.map(({ to, key }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            cx(
              'inline-flex min-h-11 items-center rounded-xl px-3 text-sm font-semibold text-fg hover:bg-sunken sm:min-h-9',
              isActive && 'bg-surface ring-1 ring-hairline',
            )
          }
        >
          {t(`admin.nav.${key}`)}
        </NavLink>
      ))}
    </nav>
  )
}

/** Heading, section navigation and the admins-only gate shared by the admin pages. */
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation()
  const me = useSession()
  return (
    <div className="flex flex-col gap-6">
      <title>{`${title} · nomal`}</title>
      <div className="flex flex-col gap-3">
        <h1 className="text-2xl font-bold">{t('admin.title')}</h1>
        <AdminNav />
      </div>
      {me.is_admin ? children : <Message>{t('admin.forbidden')}</Message>}
    </div>
  )
}

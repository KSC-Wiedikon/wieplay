import type { TFunction } from 'i18next'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router'
import { setLanguage } from '../i18n'
import { cx } from '../lib/cx'
import { initials } from '../lib/names'
import { useLogout } from '../lib/queries'
import { toggleTheme, useTheme } from '../lib/theme'
import type { Me } from '../lib/types'
import { Mark, Wordmark } from './Brand'
import { Button } from './Button'
import { DownloadIcon, HomeIcon, LogOutIcon, MoonIcon, ReelIcon, ScissorsIcon, SlidersIcon, SunIcon } from './icons'

/** Downloads are for coaches, team managers and sport admins (PLAN §5.1): the server says per team. */
function downloads(me: Me): boolean {
  return me.teams.some((team) => team.can_export)
}

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  active: boolean
}

/** Home (teams, their videos), Clips, Reels, Downloads (coaches) and Admin (admins). */
function navItems(me: Me, path: string, t: TFunction): NavItem[] {
  const icon = 'size-4'
  return [
    {
      to: '/',
      label: t('nav.home'),
      icon: <HomeIcon className={icon} />,
      active: path === '/' || path.startsWith('/teams/') || path.startsWith('/videos/'),
    },
    { to: '/clips', label: t('nav.clips'), icon: <ScissorsIcon className={icon} />, active: path.startsWith('/clips') },
    { to: '/reels', label: t('nav.reels'), icon: <ReelIcon className={icon} />, active: path.startsWith('/reels') },
    ...(downloads(me)
      ? [{ to: '/downloads', label: t('nav.downloads'), icon: <DownloadIcon className={icon} />, active: path.startsWith('/downloads') }]
      : []),
    ...(me.is_admin
      ? [{ to: '/admin/teams', label: t('header.admin'), icon: <SlidersIcon className={icon} />, active: path.startsWith('/admin/') }]
      : []),
  ]
}

/** From lg up: pills in the bar, with icons from xl. */
function BarNav({ items }: { items: NavItem[] }) {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('nav.label')} className="ml-4 hidden items-center gap-1 lg:flex">
      {items.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          aria-current={item.active ? 'page' : undefined}
          className={cx(
            'inline-flex min-h-11 shrink-0 items-center gap-2 rounded-control px-3 text-sm font-semibold whitespace-nowrap sm:min-h-9',
            item.active ? 'bg-accent-soft text-on-accent-soft' : 'text-muted hover:bg-hover hover:text-fg',
          )}
        >
          <span className="hidden xl:contents">{item.icon}</span>
          {item.label}
        </Link>
      ))}
    </nav>
  )
}

/** Phones and tablets: a row of tabs below the bar, icon above the label. */
function TabNav({ items }: { items: NavItem[] }) {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('nav.label')} className="border-t border-hairline lg:hidden">
      <div className="flex gap-0.5 px-1.5 py-1 sm:px-4">
        {items.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            aria-current={item.active ? 'page' : undefined}
            className={cx(
              'flex min-h-12 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-control px-0.5 text-[0.6875rem] leading-tight font-semibold',
              item.active ? 'bg-accent-soft text-on-accent-soft' : 'text-muted hover:bg-hover hover:text-fg',
            )}
          >
            {item.icon}
            <span className="max-w-full truncate">{item.label}</span>
          </Link>
        ))}
      </div>
    </nav>
  )
}

function MemberChip({ name }: { name: string }) {
  return (
    <span className="flex shrink-0 items-center gap-2 rounded-full xl:pr-2" title={name}>
      <span
        aria-hidden="true"
        className="grid size-8 shrink-0 place-items-center rounded-full bg-accent text-xs font-bold text-on-accent"
      >
        {initials(name)}
      </span>
      <span className="sr-only max-w-44 truncate text-sm font-semibold xl:not-sr-only">{name}</span>
    </span>
  )
}

export function Header({ me }: { me: Me }) {
  const { t, i18n } = useTranslation()
  const theme = useTheme()
  const logout = useLogout()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const otherLanguage = i18n.language === 'de' ? 'en' : 'de'
  const items = navItems(me, pathname, t)

  return (
    <header className="appbar border-b border-hairline">
      <div className="mx-auto flex max-w-[110rem] items-center gap-1 px-4 py-2 sm:gap-2 sm:px-6 lg:px-8">
        <Link
          to="/"
          aria-label={t('header.home')}
          className="-ml-1 flex min-h-11 items-center gap-2 rounded-control px-1 text-fg sm:min-h-9"
        >
          <Mark onDark className="size-8 sm:size-9" />
          <Wordmark className="text-xl sm:text-2xl" />
        </Link>

        <BarNav items={items} />

        <div className="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
          <Button
            variant="ghost"
            className="px-3"
            lang={otherLanguage}
            aria-label={t('header.otherLanguageLabel')}
            onClick={() => setLanguage(otherLanguage)}
          >
            <span className="xl:hidden">{t('header.otherLanguageShort')}</span>
            <span className="hidden xl:inline">{t('header.otherLanguage')}</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={theme === 'dark' ? t('header.toLight') : t('header.toDark')}
            title={theme === 'dark' ? t('header.toLight') : t('header.toDark')}
            onClick={toggleTheme}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </Button>
          <span aria-hidden="true" className="mx-1 hidden h-6 w-px bg-hairline sm:block" />
          <MemberChip name={me.member.name} />
          <Button
            variant="ghost"
            className="max-xl:w-11 max-xl:px-0 max-xl:sm:w-9"
            title={t('header.logout')}
            disabled={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/', { replace: true }) })}
          >
            <LogOutIcon className="size-4" />
            <span className="sr-only xl:not-sr-only">{t('header.logout')}</span>
          </Button>
        </div>
      </div>
      <TabNav items={items} />
    </header>
  )
}

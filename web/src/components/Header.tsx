import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router'
import { setLanguage } from '../i18n'
import { cx } from '../lib/cx'
import { useLogout } from '../lib/queries'
import { toggleTheme, useTheme } from '../lib/theme'
import type { Me } from '../lib/types'
import { useWidePage } from '../lib/useWidePage'
import { Mark, Wordmark } from './Brand'
import { Button } from './Button'
import { MoonIcon, SunIcon } from './icons'

/** Downloads are for coaches, team managers and sport admins (PLAN §5.1): the server says per team. */
function downloads(me: Me): boolean {
  return me.teams.some((team) => team.can_export)
}

const linkClass = (active: boolean) =>
  cx(
    'inline-flex min-h-11 shrink-0 items-center rounded-xl px-3 text-sm font-semibold whitespace-nowrap text-fg hover:bg-sunken sm:min-h-9',
    active && 'bg-sunken',
  )

/** Clips, Reels, Downloads (coaches) and Admin (admins). */
function Nav({ me, className }: { me: Me; className: string }) {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  const links: { to: string; label: string; active: (path: string) => boolean }[] = [
    { to: '/clips', label: t('nav.clips'), active: (p) => p.startsWith('/clips') },
    { to: '/reels', label: t('nav.reels'), active: (p) => p.startsWith('/reels') },
    ...(downloads(me) ? [{ to: '/downloads', label: t('nav.downloads'), active: (p: string) => p.startsWith('/downloads') }] : []),
    ...(me.is_admin ? [{ to: '/admin/teams', label: t('header.admin'), active: (p: string) => p.startsWith('/admin/') }] : []),
  ]
  return (
    <nav aria-label={t('nav.label')} className={cx('items-center gap-0.5', className)}>
      {links.map((link) => (
        <Link key={link.to} to={link.to} className={linkClass(link.active(pathname))} aria-current={link.active(pathname) ? 'page' : undefined}>
          {link.label}
        </Link>
      ))}
    </nav>
  )
}

export function Header({ me }: { me: Me }) {
  const { t, i18n } = useTranslation()
  const theme = useTheme()
  const logout = useLogout()
  const navigate = useNavigate()
  const otherLanguage = i18n.language === 'de' ? 'en' : 'de'
  const wide = useWidePage()

  return (
    <header className="border-b border-hairline bg-surface">
      <div className={cx('mx-auto flex items-center gap-1 px-4 py-2 sm:gap-2', wide ? 'max-w-[110rem]' : 'max-w-5xl')}>
        <Link
          to="/"
          aria-label={t('header.home')}
          className="-ml-1 flex min-h-11 items-center gap-2 rounded-xl px-1 text-fg sm:min-h-9"
        >
          <Mark className="size-9" />
          <Wordmark className="text-xl sm:text-2xl" />
        </Link>

        <Nav me={me} className="ml-2 hidden sm:flex" />

        <div className="ml-auto flex items-center gap-1 sm:gap-2">
          <span className="hidden truncate text-sm text-muted sm:inline sm:px-2">{me.member.name}</span>
          <Button
            variant="ghost"
            className="px-3"
            lang={otherLanguage}
            aria-label={t('header.otherLanguageLabel')}
            onClick={() => setLanguage(otherLanguage)}
          >
            <span className="sm:hidden">{t('header.otherLanguageShort')}</span>
            <span className="hidden sm:inline">{t('header.otherLanguage')}</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={theme === 'dark' ? t('header.toLight') : t('header.toDark')}
            onClick={toggleTheme}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </Button>
          <Button
            disabled={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/', { replace: true }) })}
          >
            {t('header.logout')}
          </Button>
        </div>
      </div>
      {/* Phones: the menu gets its own row; the name gives way when space runs out. */}
      <div className="flex items-center gap-2 px-4 pb-1 sm:hidden">
        <Nav me={me} className="-ml-3 flex min-w-0 overflow-x-auto" />
        <span className="ml-auto min-w-0 truncate text-sm text-muted">{me.member.name}</span>
      </div>
    </header>
  )
}

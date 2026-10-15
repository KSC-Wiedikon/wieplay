import { useTranslation } from 'react-i18next'
import { Link, NavLink, useNavigate } from 'react-router'
import { setLanguage } from '../i18n'
import { cx } from '../lib/cx'
import { useLogout } from '../lib/queries'
import { toggleTheme, useTheme } from '../lib/theme'
import type { Me } from '../lib/types'
import { Mark, Wordmark } from './Brand'
import { Button } from './Button'
import { MoonIcon, SunIcon } from './icons'

/** The user's name and, for admins, the admin link. */
function Account({ me, className }: { me: Me; className: string }) {
  const { t } = useTranslation()
  return (
    <div className={cx('items-center gap-1 sm:gap-2', className)}>
      <span className="text-sm text-muted sm:px-2">{me.member.name}</span>
      {me.is_admin && (
        <NavLink
          to="/admin/teams"
          className={({ isActive }) =>
            cx(
              'ml-auto inline-flex min-h-11 items-center rounded-xl px-3 text-sm font-semibold text-fg hover:bg-sunken sm:ml-0 sm:min-h-9',
              isActive && 'bg-sunken',
            )
          }
        >
          {t('header.admin')}
        </NavLink>
      )}
    </div>
  )
}

export function Header({ me }: { me: Me }) {
  const { t, i18n } = useTranslation()
  const theme = useTheme()
  const logout = useLogout()
  const navigate = useNavigate()
  const otherLanguage = i18n.language === 'de' ? 'en' : 'de'

  return (
    <header className="border-b border-hairline bg-surface">
      <div className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-2 sm:gap-2">
        <Link
          to="/"
          aria-label={t('header.home')}
          className="-ml-1 flex min-h-11 items-center gap-2 rounded-xl px-1 text-fg sm:min-h-9"
        >
          <Mark className="size-9" />
          <Wordmark className="text-xl sm:text-2xl" />
        </Link>

        <div className="ml-auto flex items-center gap-1 sm:gap-2">
          <Account me={me} className="hidden sm:flex" />
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
      <Account me={me} className="flex px-4 pb-1 sm:hidden" />
    </header>
  )
}

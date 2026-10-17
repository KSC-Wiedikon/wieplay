import { cx } from '../lib/cx'
import { useSport, type Sport } from '../lib/sport'
import { useTheme } from '../lib/theme'

/**
 * The logo mark for the current sport (or the given one); the dark variant has light trail dots.
 * `onDark` is for navy backgrounds in either theme, such as the app bar.
 */
export function Mark({ className, sport, onDark }: { className?: string; sport?: Sport; onDark?: boolean }) {
  const current = useSport()
  const dark = useTheme() === 'dark' || onDark === true
  return (
    <img
      src={`/brand/wieplay-mark-${sport ?? current}${dark ? '-dark' : ''}.svg`}
      alt=""
      width={512}
      height={512}
      className={cx('shrink-0', className)}
    />
  )
}

/** "WiePlay" in Bricolage Grotesque ExtraBold (called "nomal" until 17.10.2026). */
export function Wordmark({ className }: { className?: string }) {
  return <span className={cx('font-wordmark leading-none font-extrabold tracking-tight', className)}>WiePlay</span>
}

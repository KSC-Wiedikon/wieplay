import { cx } from '../lib/cx'
import { useSport } from '../lib/sport'
import { useTheme } from '../lib/theme'

/** The logo mark for the current sport; the dark variant has light trail dots. */
export function Mark({ className }: { className?: string }) {
  const sport = useSport()
  const dark = useTheme() === 'dark'
  return (
    <img
      src={`/brand/nomal-mark-${sport}${dark ? '-dark' : ''}.svg`}
      alt=""
      width={512}
      height={512}
      className={cx('shrink-0', className)}
    />
  )
}

/** Lowercase "nomal" in Bricolage Grotesque ExtraBold. */
export function Wordmark({ className }: { className?: string }) {
  return <span className={cx('font-wordmark leading-none font-extrabold tracking-tight', className)}>nomal</span>
}

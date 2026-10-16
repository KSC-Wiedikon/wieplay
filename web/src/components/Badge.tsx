import type { ComponentProps } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import type { SportName } from '../lib/types'

type Tone = 'vb' | 'bb' | 'neutral' | 'accent' | 'danger'

// Yellow and orange are fills with navy text, never text colours on light.
const tones: Record<Tone, string> = {
  vb: 'bg-vb-yellow text-navy ring-navy/25',
  bb: 'bg-bb-orange text-navy ring-navy/25',
  neutral: 'bg-sunken text-fg ring-hairline',
  accent: 'bg-accent text-on-accent ring-transparent',
  danger: 'bg-surface text-danger ring-danger/60',
}

export function Badge({ tone = 'neutral', className, ...props }: ComponentProps<'span'> & { tone?: Tone }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs leading-4 font-bold whitespace-nowrap ring-1 ring-inset',
        tones[tone],
        className,
      )}
      {...props}
    />
  )
}

/** A rounded label, e.g. the viewer's roles in a team. */
export function Chip({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-full bg-sunken px-2.5 py-0.5 text-xs leading-5 font-semibold text-fg ring-1 ring-hairline ring-inset',
        className,
      )}
      {...props}
    />
  )
}

export function SportBadge({ sport }: { sport: SportName }) {
  const { t } = useTranslation()
  const vb = sport === 'volleyball'
  return (
    <Badge tone={vb ? 'vb' : 'bb'} title={t(`sport.${sport}`)}>
      {vb ? 'VB' : 'BB'}
    </Badge>
  )
}

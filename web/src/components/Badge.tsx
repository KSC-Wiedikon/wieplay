import type { ComponentProps } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import type { SportName } from '../lib/types'

type Tone = 'vb' | 'bb' | 'neutral'

// Yellow and orange are fills with navy text, never text colours on light.
const tones: Record<Tone, string> = {
  vb: 'bg-vb-yellow text-navy ring-navy/25',
  bb: 'bg-bb-orange text-navy ring-navy/25',
  neutral: 'bg-sunken text-fg ring-hairline',
}

export function Badge({ tone = 'neutral', className, ...props }: ComponentProps<'span'> & { tone?: Tone }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs leading-4 font-bold ring-1 ring-inset',
        tones[tone],
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

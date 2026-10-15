import '../i18n/pages'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { formatClock } from '../lib/labels'
import type { Segment } from '../lib/types'
import { Card } from './Card'
import { Table, TBody, Td, Th, THead, Tr } from './Table'

interface DrillsSectionProps {
  drills: readonly Segment[]
  playingId: string | null
  onPlay: (drill: Segment) => void
}

/** The drills marked in a training: play one from its start to its end. */
export function DrillsSection({ drills, playingId, onPlay }: DrillsSectionProps) {
  const { t } = useTranslation()
  const id = useId()
  const name = (drill: Segment, index: number) => drill.label || t('drills.defaultName', { n: index + 1 })
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('drills.title')}
      </h2>
      <Card className="overflow-hidden">
        <Table caption={t('drills.title')}>
          <THead>
            <tr>
              <Th>{t('drills.label')}</Th>
              <Th>{t('clips.columns.time')}</Th>
              <Th className="hidden sm:table-cell">{t('columns.duration')}</Th>
            </tr>
          </THead>
          <TBody>
            {drills.map((drill, index) => {
              const playing = drill.id === playingId
              return (
                <Tr key={drill.id} onActivate={() => onPlay(drill)} aria-current={playing ? 'true' : undefined} className={cx(playing && 'bg-sunken')}>
                  <Td tight>
                    <button
                      type="button"
                      onClick={() => onPlay(drill)}
                      aria-label={t('drills.play', { label: name(drill, index) })}
                      className="min-h-11 cursor-pointer rounded-lg px-1 text-left font-semibold text-link underline-offset-2 hover:underline sm:min-h-9"
                    >
                      {name(drill, index)}
                    </button>
                  </Td>
                  <Td className="whitespace-nowrap text-muted tabular-nums">
                    {`${formatClock(drill.start_ms, { tenths: false })}–${formatClock(drill.end_ms, { tenths: false })}`}
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDuration(drill.end_ms - drill.start_ms)}</Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>
      </Card>
    </section>
  )
}

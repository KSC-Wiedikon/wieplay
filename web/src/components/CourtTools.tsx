import '../i18n/pages'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useCourt } from '../lib/court'
import type { Video } from '../lib/types'
import { Button } from './Button'
import { CourtSetupDialog } from './CourtSetupDialog'
import { CheckIcon } from './icons'
import type { TrajectoryState } from '../lib/trajectories'
import { TrajectoryToggle } from './Trajectories'

/** Below the player: "Trajectories" and, for those who may tag, "Set up the court". */
export function CourtTools({ video, element, trajectories }: { video: Video; element: HTMLVideoElement | null; trajectories: TrajectoryState }) {
  const { t } = useTranslation()
  const court = useCourt(video.id)
  const [open, setOpen] = useState(false)
  const data = court.data
  if (!data || (!trajectories.available && !trajectories.pending && !data.can_edit)) return null
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <TrajectoryToggle state={trajectories} />
      {trajectories.pending && <span className="text-sm text-muted">{t('court.trajectoriesPending')}</span>}
      {data.can_edit && (
        <Button onClick={() => setOpen(true)} aria-haspopup="dialog">
          {data.calibration && <CheckIcon className="size-4" />}
          {data.calibration ? t('court.setup.edit') : t('court.setup.open')}
        </Button>
      )}
      {open && <CourtSetupDialog open videoId={video.id} state={data} element={element} onClose={() => setOpen(false)} />}
    </div>
  )
}

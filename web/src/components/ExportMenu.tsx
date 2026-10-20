import '../i18n/pages'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { Video } from '../lib/types'
import { useExport } from '../lib/useExport'
import { ArrowRightIcon, DownloadIcon, FilmIcon, SparkleIcon } from './icons'
import { Popover } from './Popover'

function Item({ icon, title, hint, disabled, onClick }: { icon: ReactNode; title: string; hint: string; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full cursor-pointer items-start gap-2.5 rounded-control px-2.5 py-2 text-left hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:bg-transparent"
    >
      <span className="mt-0.5 text-muted">{icon}</span>
      <span className="flex min-w-0 flex-col">
        <span className="text-sm font-semibold">{title}</span>
        <span className="text-xs text-muted">{hint}</span>
      </span>
    </button>
  )
}

/**
 * "Export": the video's downloads in one place (coaches, team managers, sport admins): the
 * rallies only (the match without the dead time) and the game recap (volleyball; also from sure
 * and checked tags only), all as watermarked MP4s that show up under Downloads.
 */
export function ExportMenu({ video, rallies }: { video: Video; rallies: number }) {
  const { t, i18n } = useTranslation()
  const { start, busy, dialog } = useExport()
  const volleyball = video.sport === 'volleyball'
  return (
    <>
      <Popover
        label={
          <>
            <DownloadIcon className="size-4" />
            {t('exportMenu.button')}
          </>
        }
        chevron
        align="end"
      >
        {(close) => (
          <div className="flex flex-col">
            <Item
              icon={<FilmIcon className="size-4" />}
              title={t('exportMenu.condensed')}
              hint={rallies > 0 ? t('exportMenu.condensedHint') : t('exportMenu.noRallies')}
              disabled={rallies === 0 || busy}
              onClick={() => {
                close()
                start({ kind: 'condensed', video_id: video.id })
              }}
            />
            {volleyball && (
              <Item
                icon={<SparkleIcon className="size-4" />}
                title={t('exportMenu.recap')}
                hint={t('exportMenu.recapHint')}
                disabled={busy}
                onClick={() => {
                  close()
                  start({ kind: 'recap', video_id: video.id, lang: i18n.language === 'de' ? 'de' : 'en' }, t('recap.queued'))
                }}
              />
            )}
            {volleyball && (
              <Item
                icon={<SparkleIcon className="size-4" />}
                title={t('exportMenu.recapSure')}
                hint={t('exportMenu.recapSureHint')}
                disabled={busy}
                onClick={() => {
                  close()
                  start({ kind: 'recap', video_id: video.id, lang: i18n.language === 'de' ? 'de' : 'en', sure_only: true }, t('recap.queued'))
                }}
              />
            )}
            <div className="mt-1 border-t border-hairline pt-1">
              <Link
                to="/downloads"
                className="flex min-h-11 items-center gap-2 rounded-control px-2.5 text-sm font-semibold hover:bg-hover sm:min-h-9"
              >
                <ArrowRightIcon className="size-4 text-muted" />
                {t('exportMenu.downloads')}
              </Link>
            </div>
          </div>
        )}
      </Popover>
      {dialog}
    </>
  )
}

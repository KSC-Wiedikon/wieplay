import type { UseQueryResult } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Chip, SportBadge } from '../../components/Badge'
import { Mark } from '../../components/Brand'
import { Card } from '../../components/Card'
import { CalendarIcon, ChevronDownIcon, FilmIcon, HistoryIcon } from '../../components/icons'
import { VideoPoster } from '../../components/VideoPoster'
import { cx } from '../../lib/cx'
import { formatDate } from '../../lib/format'
import { teamRoleLabels } from '../../lib/roles'
import { sportOf } from '../../lib/sport'
import type { Team, Video } from '../../lib/types'
import { newestFirst } from '../../lib/video'

/** The viewer's roles in the team; a helper's grant shows as "Helper"; admins without one see "Admin access". */
function Roles({ team }: { team: Team }) {
  const { t } = useTranslation()
  const labels = teamRoleLabels(t, team)
  const helper = t('roles.helper')
  return (
    <ul aria-label={t('columns.roles')} className="flex flex-wrap gap-1.5">
      {(labels.length > 0 ? labels : [t('roles.none')]).map((label) => (
        <li key={label}>
          <Chip accent={team.helper === true && label === helper}>{label}</Chip>
        </li>
      ))}
    </ul>
  )
}

/**
 * One team on the start page: the poster of its latest ready video, name, league and season, the
 * viewer's roles, how many videos it has; earlier seasons of the same team behind a disclosure.
 */
export function TeamCard({
  latest,
  earlier,
  videos,
}: {
  latest: Team
  earlier: Team[]
  videos: UseQueryResult<Video[]> | undefined
}) {
  const { t } = useTranslation()
  const list = videos?.data ? newestFirst(videos.data) : null
  const poster = list?.find((video) => video.status === 'ready' && video.has_thumbs)
  const newest = list?.[0]

  return (
    <Card className="group relative flex w-full flex-col overflow-hidden hover:border-hairline-strong hover:shadow-raised">
      <div className="relative aspect-[16/7] overflow-hidden bg-sunken">
        {poster ? (
          <VideoPoster
            video={poster}
            className="size-full transition-transform duration-500 motion-safe:group-hover:scale-[1.03]"
          />
        ) : (
          // No poster yet: the sport's mark on a faint wash of the sport colour.
          <div
            className={cx(
              'grid size-full place-items-center bg-linear-135 from-sunken via-sunken',
              latest.sport === 'basketball' ? 'to-bb-orange/20' : 'to-vb-yellow/25',
            )}
          >
            <Mark sport={sportOf(latest.sport)} className="size-14 opacity-90" />
          </div>
        )}
        <span className="absolute top-3 left-3">
          <SportBadge sport={latest.sport} />
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="text-lg leading-tight font-bold">
            {/* The link covers the whole card; the earlier seasons sit above it. */}
            <Link
              to={`/teams/${latest.id}`}
              className={cx(
                'rounded-sm decoration-link-line underline-offset-4 group-hover:underline focus-visible:outline-none',
                'after:absolute after:inset-0 after:rounded-card focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring',
              )}
            >
              {latest.name}
            </Link>
          </h3>
          <p className="text-sm text-muted">{[latest.league, latest.season].filter(Boolean).join(' · ')}</p>
        </div>

        <Roles team={latest} />

        <div className="mt-auto flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-hairline pt-3 text-sm text-muted">
          {list === null ? (
            <span className="h-4 w-24 rounded bg-sunken motion-safe:animate-pulse" aria-hidden="true" />
          ) : (
            <>
              <span className="inline-flex items-center gap-1.5">
                <FilmIcon className="size-4" />
                {list.length > 0 ? t('home.videoCount', { count: list.length }) : t('home.noVideosYet')}
              </span>
              {newest && (
                <span className="inline-flex items-center gap-1.5 tabular-nums">
                  <CalendarIcon className="size-4" />
                  {t('home.latestVideo', { date: formatDate(newest.recorded_on) })}
                </span>
              )}
            </>
          )}
        </div>

        {earlier.length > 0 && <EarlierSeasons team={latest} earlier={earlier} />}
      </div>
    </Card>
  )
}

function EarlierSeasons({ team, earlier }: { team: Team; earlier: Team[] }) {
  const { t } = useTranslation()
  const id = useId()
  const [open, setOpen] = useState(false)
  const label = t(open ? 'home.hideEarlier' : 'home.showEarlier', { name: team.name })
  return (
    <div className="relative z-10 -mx-2 -mb-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={label}
        title={label}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-control px-2 text-sm font-semibold text-muted hover:bg-hover hover:text-fg sm:min-h-9"
      >
        <HistoryIcon className="size-4" />
        {t('home.earlierSeasons', { count: earlier.length })}
        <ChevronDownIcon className={cx('size-4 transition-transform motion-reduce:transition-none', open && 'rotate-180')} />
      </button>
      {open && (
        <ul id={id} className="flex flex-col pb-1">
          {earlier.map((season) => (
            <li key={season.id}>
              <Link
                to={`/teams/${season.id}`}
                className="flex min-h-11 items-center justify-between gap-3 rounded-control px-2 text-sm hover:bg-hover sm:min-h-9"
              >
                <span className="link font-semibold">{`${season.name} ${season.season}`}</span>
                <span className="truncate text-xs text-muted">
                  {teamRoleLabels(t, season).join(', ') || t('roles.none')}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

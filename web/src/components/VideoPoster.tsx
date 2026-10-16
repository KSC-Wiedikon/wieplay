import { cx } from '../lib/cx'
import type { Video } from '../lib/types'
import { FilmIcon } from './icons'

/** The video's poster frame, or a film icon while it has none. Size it with `className`. */
export function VideoPoster({
  video,
  className,
  small,
}: {
  video: Pick<Video, 'id' | 'has_thumbs'>
  className?: string
  /** A smaller placeholder icon, for thumbnails in table rows. */
  small?: boolean
}) {
  if (video.has_thumbs) {
    return (
      <img
        src={`/api/media/${video.id}/poster.jpg`}
        alt=""
        loading="lazy"
        width={640}
        height={360}
        className={cx('aspect-video bg-sunken object-cover', className)}
      />
    )
  }
  return (
    <span className={cx('grid aspect-video place-items-center bg-sunken text-muted', className)}>
      <FilmIcon className={small ? 'size-4' : 'size-7'} />
    </span>
  )
}

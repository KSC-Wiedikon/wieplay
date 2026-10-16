import '../i18n/pages'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { isBusy, proposalTotal, useAnalyse, useAnalysis, useSetRalliesReviewed } from '../lib/analysis'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { formatPercent, GAP_KINDS } from '../lib/suggestions'
import type { Scoring, Video } from '../lib/types'
import { Button, ButtonLink } from './Button'
import { Card } from './Card'
import { ConfirmDialog } from './ConfirmDialog'
import { AlertIcon, CheckIcon, SparkleIcon } from './icons'
import { Switch } from './Switch'

export interface LiveCounts {
  /** Every proposal (answered or not). */
  total: number
  /** Proposals nobody has answered yet. */
  open: number
}

interface AnalysisStatusProps {
  video: Video
  /** Rallies there are besides open suggestions (a game tagged by hand can be marked reviewed too). */
  rallies: number
  /** The tagging page counts from its own data (changes not sent yet included); else the server's. */
  live?: LiveCounts
  /** Where to review the suggestions (the video page links to the tagging page's review). */
  reviewHref?: string
  /** After "rallies reviewed" was saved: the server accepted the open suggestions itself. */
  onReviewed?: () => void
  className?: string
}

function Spinning() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5 shrink-0 text-muted motion-safe:animate-spin">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

/**
 * The automatic analysis of a video and the learning loop (PLAN §10.1), for those who may tag:
 * its state (queued and running update by themselves), "Analyse" when there is none or it
 * failed, how well the suggestions matched the coach's own rallies, the automatic score
 * (§10.3), and "Rallies reviewed", which tells Nomal to learn from the game.
 */
export function AnalysisStatus({ video, rallies, live, reviewHref, onReviewed, className }: AnalysisStatusProps) {
  const { t } = useTranslation()
  const analysis = useAnalysis(video.id, true)
  const analyse = useAnalyse(video.id)
  const data = analysis.data
  if (!data) return null

  const total = live?.total ?? proposalTotal(data.proposals)
  const open = live?.open ?? data.proposals.unreviewed
  const metrics = data.metrics_vs_human
  const canAnalyse = data.status === 'none' || data.status === 'failed'
  const action =
    reviewHref && open > 0 ? (
      <ButtonLink to={reviewHref} variant="primary">
        {t('tagging.analysis.reviewCount', { count: open })}
      </ButtonLink>
    ) : null

  let icon: ReactNode
  let line: string
  let body: string | null
  switch (data.status) {
    case 'queued':
    case 'running':
      icon = <Spinning />
      line = data.status === 'queued' ? t('tagging.analysis.queued') : t('tagging.analysis.running')
      body = t('tagging.analysis.busyBody')
      break
    case 'failed':
      icon = <AlertIcon className="size-5 text-danger" />
      line = t('tagging.analysis.failed')
      body = t('tagging.analysis.failedBody')
      break
    case 'done':
      icon = <SparkleIcon className="size-5" />
      // A game the coach tagged himself is only measured: no proposals, just the comparison.
      line = total > 0 ? t('tagging.analysis.done', { count: total }) : metrics ? t('tagging.analysis.measured') : t('tagging.analysis.noneFound')
      body = total > 0 ? (open > 0 ? t('tagging.analysis.open', { count: open }) : t('tagging.analysis.allReviewed')) : null
      break
    default:
      icon = <SparkleIcon className="size-5 text-muted" />
      line = t('tagging.analysis.none')
      body = t('tagging.analysis.noneBody')
  }

  return (
    <Card role="region" aria-label={t('tagging.analysis.title')} className={cx('flex flex-wrap items-start gap-x-6 gap-y-3 p-4', className)}>
      <div className="flex min-w-0 flex-[2_1_20rem] flex-col gap-1">
        <p role="status" className={cx('flex items-center gap-2 font-semibold', data.status === 'failed' && 'text-danger')}>
          {icon}
          <span>{line}</span>
        </p>
        {body && <p className="text-sm text-muted">{body}</p>}
        {metrics && data.status === 'done' && (
          <>
            <p className="text-sm font-semibold">{t('tagging.analysis.accuracy', { f1: formatPercent(metrics.f1_1s) })}</p>
            <p className="text-sm text-muted">
              {t('tagging.analysis.accuracyDetail', {
                precision: formatPercent(metrics.precision),
                recall: formatPercent(metrics.recall),
                model: metrics.rallies_model,
                human: metrics.rallies_human,
              })}
            </p>
          </>
        )}
        {data.scoring && <ScoringSummary scoring={data.scoring} />}
        {(canAnalyse || action) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {canAnalyse && (
              <Button variant={data.status === 'failed' ? 'secondary' : 'primary'} disabled={analyse.isPending} onClick={() => analyse.mutate()}>
                {data.status === 'failed' ? t('tagging.analysis.retry') : t('tagging.analysis.analyse')}
              </Button>
            )}
            {action}
          </div>
        )}
      </div>
      {!isBusy(data) && (total > 0 || rallies > 0 || video.rallies_reviewed_at) ? (
        <Reviewed video={video} open={open} total={total} onReviewed={onReviewed} />
      ) : null}
    </Card>
  )
}

/**
 * The automatic score in short: per set the official score against where Nomal's serve
 * evidence alone ends it ("official 25:27 · Nomal 25:27 ✓"), the rallies to check, the breaks
 * found, and how often Nomal agreed with the rallies a coach decided.
 */
function ScoringSummary({ scoring }: { scoring: Scoring }) {
  const { t } = useTranslation()
  const score = (pair: [number, number] | null) => (pair ? `${pair[0]}:${pair[1]}` : '–')
  const gaps = GAP_KINDS.filter((kind) => (scoring.gaps[kind] ?? 0) > 0).map((kind) =>
    t(`tagging.analysis.scoring.gap.${kind}`, { count: scoring.gaps[kind] }),
  )
  const vs = scoring.vs_coach
  return (
    <section aria-label={t('tagging.analysis.scoring.title')} className="mt-2 flex flex-col gap-1 border-t border-hairline pt-2 text-sm">
      <h3 className="font-semibold">{t('tagging.analysis.scoring.title')}</h3>
      {scoring.sets.length > 0 && (
        <ul className="flex flex-col gap-0.5 tabular-nums">
          {scoring.sets.map((set) => (
            <li key={set.no} className={cx('flex items-center gap-1.5', set.matches_official === false && 'font-semibold text-danger')}>
              {/* Against the official score: where the serve evidence alone ends the set (the score line
                  itself is aligned with the official score). */}
              {set.official
                ? t('tagging.analysis.scoring.set', { no: set.no, official: score(set.official), model: score(set.evidence ?? set.model) })
                : t('tagging.analysis.scoring.setNoOfficial', { no: set.no, model: score(set.model) })}
              {set.matches_official === true && (
                <>
                  <CheckIcon className="size-4" />
                  <span className="sr-only">{t('tagging.analysis.scoring.matches')}</span>
                </>
              )}
              {set.matches_official === false && (
                <>
                  <AlertIcon className="size-4" />
                  <span className="sr-only">{t('tagging.analysis.scoring.differs')}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className={cx(scoring.flagged > 0 && 'font-semibold')}>
        {scoring.flagged > 0 ? t('tagging.analysis.scoring.flagged', { count: scoring.flagged }) : t('tagging.analysis.scoring.noneFlagged')}
      </p>
      {gaps.length > 0 && <p className="text-muted">{t('tagging.analysis.scoring.gaps', { list: gaps.join(' · ') })}</p>}
      {vs?.serving_side_accuracy != null && (
        <p className="text-muted">{t('tagging.analysis.scoring.vsCoachServe', { value: formatPercent(vs.serving_side_accuracy), count: vs.serving_known })}</p>
      )}
      {vs?.winner_accuracy != null && (
        <p className="text-muted">{t('tagging.analysis.scoring.vsCoachWinner', { value: formatPercent(vs.winner_accuracy), count: vs.winners_known })}</p>
      )}
    </section>
  )
}

/** "Rallies reviewed": a switch with what it does, or a nudge once every suggestion is answered. */
function Reviewed({ video, open, total, onReviewed }: { video: Video; open: number; total: number; onReviewed?: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const mark = useSetRalliesReviewed(video.id)
  const [asking, setAsking] = useState(false)
  const save = (on: boolean) => mark.mutate(on, { onSuccess: () => onReviewed?.() })
  const at = video.rallies_reviewed_at ?? null
  const reviewed = at !== null

  if (!reviewed && total > 0 && open === 0) {
    return (
      <div className="flex flex-[1_1_100%] flex-wrap items-center justify-between gap-3 rounded-xl border border-accent bg-accent-soft p-3">
        <div className="flex min-w-0 flex-1 basis-56 items-start gap-2">
          <CheckIcon className="mt-0.5 size-5 text-on-accent-soft" />
          <div className="flex flex-col gap-0.5">
            <p className="font-semibold">{t('tagging.analysis.allDoneTitle')}</p>
            <p className="text-sm">{t('tagging.analysis.allDoneBody')}</p>
          </div>
        </div>
        <Button variant="primary" disabled={mark.isPending} onClick={() => save(true)}>
          {t('tagging.analysis.markReviewed')}
        </Button>
      </div>
    )
  }

  const hint = reviewed
    ? t('tagging.analysis.reviewedAt', { date: formatDateTime(at) })
    : open > 0
      ? t('tagging.analysis.reviewedOpen', { count: open })
      : t('tagging.analysis.reviewedHint')
  return (
    <div className="flex min-w-0 flex-[1_1_20rem] items-start gap-2 border-t border-hairline pt-3 md:border-t-0 md:border-l md:pt-0 md:pl-5">
      <Switch
        checked={reviewed}
        disabled={mark.isPending}
        label={t('tagging.analysis.reviewed')}
        describedBy={`${id}-hint`}
        // Open suggestions are accepted as they are by the server: ask first.
        onChange={(on) => (on && open > 0 ? setAsking(true) : save(on))}
      />
      <div className="flex min-w-0 flex-col gap-0.5 pt-2.5 sm:pt-1.5">
        <p className="font-semibold" aria-hidden="true">
          {t('tagging.analysis.reviewed')}
        </p>
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      </div>
      <ConfirmDialog
        open={asking}
        title={t('tagging.analysis.confirmTitle')}
        confirmLabel={t('tagging.analysis.markReviewed')}
        onCancel={() => setAsking(false)}
        onConfirm={() => {
          setAsking(false)
          save(true)
        }}
      >
        {t('tagging.analysis.confirmBody', { count: open })}
      </ConfirmDialog>
    </div>
  )
}

import '../i18n/pages'
import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { measuredText } from '../lib/actions'
import { isBusy, proposalsToCheck, proposalTotal, stepsLine, useAnalyse, useAnalysis, useSetActionsReviewed, useSetRalliesReviewed } from '../lib/analysis'
import { cx } from '../lib/cx'
import { formatDateTime } from '../lib/format'
import { formatPercent, GAP_KINDS } from '../lib/suggestions'
import type { ActionsState, Scoring, Video } from '../lib/types'
import { ActionGateNote } from './ActionGateNote'
import { Button, ButtonLink } from './Button'
import { Card } from './Card'
import { AlertIcon, CheckIcon, SparkleIcon } from './icons'
import { Switch } from './Switch'

export interface LiveCounts {
  /** Every rally WiePlay made (checked or not). */
  total: number
  /** Rallies still to check. */
  open: number
}

interface AnalysisStatusProps {
  video: Video
  /** A person's own rallies (a game tagged by hand can be marked reviewed too). */
  rallies: number
  /** The tagging page counts from its own data (changes not sent yet included); else the server's. */
  live?: LiveCounts
  /** Where "To check" is (the video page links to the tagging page's review). */
  reviewHref?: string
  /** After "rallies reviewed" was saved. */
  onReviewed?: () => void
  /** A person's own action tags (WiePlay's not counted): "actions reviewed" needs some. */
  actionTags?: number
  /** Without a card of its own (the video page's setup frames it). */
  plain?: boolean
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
 * where the chain is ("Analysing: 4 of 7 steps"; it updates by itself), "Analyse" when there is
 * none or it failed, how many rallies WiePlay found and how many are still to check, how well
 * its rallies matched the coach's own, the automatic score (§10.3), and "Rallies reviewed",
 * which tells WiePlay to learn from the game.
 */
export function AnalysisStatus({ video, rallies, live, reviewHref, onReviewed, actionTags = 0, plain = false, className }: AnalysisStatusProps) {
  const { t } = useTranslation()
  const analysis = useAnalysis(video.id, true)
  const analyse = useAnalyse(video.id)
  const data = analysis.data
  if (!data) return null

  const total = live?.total ?? proposalTotal(data.proposals)
  const open = live?.open ?? data.to_check?.rallies ?? proposalsToCheck(data.proposals)
  // Everything still to check (rallies, breaks, sets, actions, players): the link says so.
  const toCheck = data.to_check?.total ?? open
  const metrics = data.metrics_vs_human
  // Starting it by hand is for coaches and helpers (players tag and review; older servers: allowed).
  const canAnalyse = (data.can_run ?? true) && (data.status === 'none' || data.status === 'failed')
  const action =
    reviewHref && toCheck > 0 ? (
      <ButtonLink to={reviewHref} variant="primary">
        {t('tagging.analysis.reviewCount', { count: toCheck })}
      </ButtonLink>
    ) : null

  let icon: ReactNode
  let line: string
  let body: string | null
  switch (data.status) {
    case 'queued':
    case 'running':
      icon = <Spinning />
      line = stepsLine(t, data) ?? (data.status === 'queued' ? t('tagging.analysis.queued') : t('tagging.analysis.running'))
      body = t('tagging.analysis.busyBody')
      break
    case 'failed':
      icon = <AlertIcon className="size-5 text-danger" />
      line = t('tagging.analysis.failed')
      body = t('tagging.analysis.failedBody')
      break
    case 'done':
      // The rest of the chain (ball, court, actions, players) may still run: "Analysing: 4 of 7 steps".
      icon = data.steps?.running ? <Spinning /> : <SparkleIcon className="size-5" />
      line =
        stepsLine(t, data) ??
        // A game the coach tagged himself is only measured: no rallies of WiePlay's, just the comparison.
        (total > 0 ? t('tagging.analysis.done', { count: total }) : metrics ? t('tagging.analysis.measured') : t('tagging.analysis.noneFound'))
      body =
        total > 0
          ? `${data.steps && !data.steps.running ? `${t('tagging.analysis.done', { count: total })} ` : ''}${open > 0 ? t('tagging.analysis.open', { count: open }) : t('tagging.analysis.allReviewed')}`
          : null
      break
    default:
      icon = <SparkleIcon className="size-5 text-muted" />
      line = t('tagging.analysis.none')
      body = t('tagging.analysis.noneBody')
  }

  const Frame = plain ? 'div' : Card
  return (
    <Frame role="region" aria-label={t('tagging.analysis.title')} className={cx('flex flex-wrap items-start gap-x-6 gap-y-3', !plain && 'p-4', className)}>
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
        {data.actions && <ActionsSummary videoId={video.id} actions={data.actions} tags={actionTags} />}
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
    </Frame>
  )
}

/**
 * The automatic score in short: per set the official score against where WiePlay's serve
 * evidence alone ends it ("official 25:27 · WiePlay 25:27 ✓"), the rallies to check, the breaks
 * found, and how often WiePlay agreed with the rallies a coach decided.
 */
function ScoringSummary({ scoring }: { scoring: Scoring }) {
  const { t } = useTranslation()
  const score = (pair: [number, number] | null) => (pair ? `${pair[0]}:${pair[1]}` : '–')
  const gaps = GAP_KINDS.filter((kind) => (scoring.gaps[kind] ?? 0) > 0).map((kind) =>
    t(`tagging.analysis.scoring.gap.${kind}`, { count: scoring.gaps[kind] ?? 0 }),
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

/**
 * WiePlay's actions (PLAN §10.4a): how many and how many are still to check (switched off: why
 * there are none); what is measured on hand-checked rallies and against the coach's tags;
 * "Actions reviewed", which makes a game teach WiePlay.
 */
function ActionsSummary({ videoId, actions, tags }: { videoId: string; actions: ActionsState; tags: number }) {
  const { t } = useTranslation()
  const id = useId()
  const mark = useSetActionsReviewed(videoId)
  const s = actions.suggestions
  const open = (s.check ?? 0) + (s.unreviewed ?? 0)
  const total = (s.auto ?? 0) + open + s.confirmed + s.corrected + s.rejected
  const vs = actions.vs_coach
  const at = actions.reviewed_at
  const canMark = tags > 0 || total > 0 || at !== null
  const measured = measuredText(t, actions.gate)
  return (
    <section aria-label={t('actions.card.title')} className="mt-2 flex flex-col gap-1 border-t border-hairline pt-2 text-sm">
      <h3 className="font-semibold">{t('actions.card.title')}</h3>
      <ActionGateNote gate={actions.gate} />
      {total > 0 && <p>{t('actions.card.counts', { count: total, open })}</p>}
      {measured && <p className="text-muted">{measured}</p>}
      {vs && (
        <p className="text-muted">{t('actions.card.measured', { score: formatPercent(vs.action_precision ?? null), recall: formatPercent(vs.recall) })}</p>
      )}
      {canMark && (
        <div className="mt-1 flex items-start gap-2">
          <Switch
            checked={at !== null}
            disabled={mark.isPending}
            label={t('actions.card.reviewed')}
            describedBy={`${id}-hint`}
            onChange={(on) => mark.mutate(on)}
          />
          <div className="flex min-w-0 flex-col gap-0.5 pt-1.5">
            <p className="font-semibold" aria-hidden="true">
              {t('actions.card.reviewed')}
            </p>
            <p id={`${id}-hint`} className="text-muted">
              {at ? t('actions.card.reviewedAt', { date: formatDateTime(at) }) : t('actions.card.reviewedHint')}
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

/** "Rallies reviewed": a switch with what it does, or a nudge once nothing is left to check. */
function Reviewed({ video, open, total, onReviewed }: { video: Video; open: number; total: number; onReviewed?: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const mark = useSetRalliesReviewed(video.id)
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
        label={t('tagging.analysis.reviewed')}
        describedBy={`${id}-hint`}
        // The server waits until nothing is left to check.
        disabled={mark.isPending || (!reviewed && open > 0)}
        onChange={save}
      />
      <div className="flex min-w-0 flex-col gap-0.5 pt-2.5 sm:pt-1.5">
        <p className="font-semibold" aria-hidden="true">
          {t('tagging.analysis.reviewed')}
        </p>
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      </div>
    </div>
  )
}

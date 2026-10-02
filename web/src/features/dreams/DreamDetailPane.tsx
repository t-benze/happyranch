/**
 * DreamDetailPane — detail drawer for a single dream.
 * Direction-A Pasture fidelity pass (THR-030 Leg B Batch 9).
 *
 * Shows summary/transcript, learnings count, KB candidates with the
 * Accept/Dismiss review gate, and a link to the reflection thread.
 *
 * Pasture vocabulary:
 *   Cards: bg-surface + border-border-default + shadow-pasture-sm + rounded-lg
 *   Headings: font-display serif (Newsreader) where appropriate
 *   Dream ID / counts: font-mono tabular-nums
 *   Status pills: rounded-full bg-accent-soft/text-accent-text (completed)
 *   Semantic text: text-text-primary / secondary / muted
 *   Error panels: border-feedback-danger/30 bg-feedback-danger/5
 *
 * HONESTY FENCE: renders ONLY fields from DreamRecord + DreamKbCandidate
 * data models. No fabricated provenance, sub-states, or metrics.
 *
 * States: Loading, Populated, Error. Candidate mutations invalidate
 * the dream query so the list re-fetches after accept/dismiss.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import {
  useDream,
  useAcceptCandidate,
  useDismissCandidate,
} from '@/hooks/dreams';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import {
  classifyDreamError,
  dreamStatusLabel,
  renderDreamError,
  type DreamErrorView,
} from './strings';
import { isValidCount, formatCount } from './count-helpers';
import type { DreamKbCandidate } from '@/hooks/dreams';
import { useTranslation } from '@/hooks/i18n';

/* ------------------------------------------------------------------ */
/*  Status pill                                                        */
/* ------------------------------------------------------------------ */

function statusPill(status: string): string {
  switch (status) {
    case 'completed': return 'bg-accent-soft text-accent-text';
    case 'failed': case 'timeout': return 'bg-danger-soft text-feedback-danger';
    case 'missed': case 'skipped': return 'bg-surface-sunken text-text-muted';
    case 'running': return 'bg-accent-soft text-accent-text';
    default: return 'bg-surface-sunken text-text-muted';
  }
}

/* ------------------------------------------------------------------ */
/*  Candidate card with review gate — Pasture card pattern             */
/* ------------------------------------------------------------------ */

function CandidateCard({
  candidate,
  onAccept,
  onDismiss,
  acceptPending,
  dismissPending,
}: {
  candidate: DreamKbCandidate;
  onAccept: (id: number) => void;
  onDismiss: (id: number) => void;
  acceptPending: boolean;
  dismissPending: boolean;
}): JSX.Element {
  const { t } = useTranslation();
  const isPending = candidate.status === 'pending';
  const isPromoted = candidate.status === 'promoted';
  const isRejected = candidate.status === 'rejected';
  const anyPending = acceptPending || dismissPending;

  const label = isPending
    ? t('dreams.candidate.pending', { agent: candidate.agent_name })
    : isPromoted
      ? t('dreams.candidate.accepted', { agent: candidate.agent_name })
      : isRejected
        ? t('dreams.candidate.rejected', { agent: candidate.agent_name })
        : candidate.status;

  return (
    <div className="kb-cand bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      {/* Header with dream marker */}
      <div className="mb-2 flex items-center gap-2">
        <CrescentMoonBadge className="h-3 w-3" label={t('dreams.badge')} />
        <span className="text-text-muted text-xs italic">{label}</span>
      </div>

      {/* Title */}
      <h4 className="text-text-primary mb-1 text-sm font-medium">{candidate.title}</h4>

      {/* Slug / type / topic */}
      <div className="text-text-muted mb-2 flex items-center gap-2 font-mono text-xs tabular-nums">
        <span>{candidate.slug}</span>
        <span>·</span>
        <span>{candidate.topic}</span>
      </div>

      {/* Rationale */}
      {candidate.rationale && (
        <p className="text-text-secondary mb-3 text-xs italic">
          &ldquo;{candidate.rationale}&rdquo;
        </p>
      )}

      {/* Body preview */}
      {candidate.body_markdown && (
        <div className="text-text-secondary border-border-default mb-3 max-h-32 overflow-hidden border-l-2 pl-3 font-mono text-xs whitespace-pre-wrap">
          {candidate.body_markdown.slice(0, 300)}
          {candidate.body_markdown.length > 300 ? '…' : ''}
        </div>
      )}

      {/* Review gate — only for pending candidates */}
      {isPending && (
        <div className="mt-3 flex gap-2">
          <Button
            size="sm"
            aria-label={t('dreams.candidate.accept')}
            disabled={anyPending}
            onClick={(e) => {
              e.stopPropagation();
              onAccept(candidate.id);
            }}
          >
            {t('dreams.candidate.accept')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('dreams.candidate.dismiss')}
            disabled={anyPending}
            onClick={(e) => {
              e.stopPropagation();
              onDismiss(candidate.id);
            }}
          >
            {t('dreams.candidate.dismiss')}
          </Button>
        </div>
      )}

      {/* Resolved indicator */}
      {isPromoted && (
        <p className="text-accent-text mt-2 text-xs">
          {candidate.promoted_kb_slug
            ? t('dreams.candidate.promotedSlug', { slug: candidate.promoted_kb_slug })
            : t('dreams.candidate.promoted')}
        </p>
      )}
      {isRejected && (
        <p className="text-text-muted mt-2 text-xs">{t('dreams.candidate.dismissed')}</p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail pane                                                        */
/* ------------------------------------------------------------------ */

export function DreamDetailPane({
  dreamId,
  onClose,
}: {
  dreamId: string;
  onClose: () => void;
}): JSX.Element {
  const { slug: orgSlug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const dreamQ = useDream(dreamId);
  const acceptMutation = useAcceptCandidate();
  const dismissMutation = useDismissCandidate();
  const { t } = useTranslation();
  const [actionError, setActionError] = useState<DreamErrorView | null>(null);

  const dream = dreamQ.data;

  const handleAccept = async (candidateId: number) => {
    setActionError(null);
    try {
      await acceptMutation.mutateAsync(candidateId);
    } catch (err) {
      setActionError(classifyDreamError(err, 'dreams.action.acceptFailed'));
    }
  };

  const handleDismiss = async (candidateId: number) => {
    setActionError(null);
    try {
      await dismissMutation.mutateAsync(candidateId);
    } catch (err) {
      setActionError(classifyDreamError(err, 'dreams.action.dismissFailed'));
    }
  };

  const candidates = dream?.kb_candidates ?? [];
  const pendingCount = candidates.filter((c) => c.status === 'pending').length;
  // Quiet state requires both authoritative supplied counts to be valid finite
  // non-negative numbers. A malformed kb_candidate_count must not be replaced
  // by candidates.length; it is unavailable and must prevent any "nothing
  // escalated" claim, even when the candidate array happens to be empty.
  const dreamLearnings = dream?.new_learnings_count;
  const dreamCandidates = dream?.kb_candidate_count;
  const isQuiet =
    dream?.status === 'completed' &&
    isValidCount(dreamCandidates) &&
    dreamCandidates === 0 &&
    isValidCount(dreamLearnings) &&
    dreamLearnings > 0;

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent className="flex flex-col">
        {/* Header */}
        <header className="border-border-default border-b p-4">
          <div className="mb-1 flex items-center gap-2">
            <CrescentMoonBadge className="h-3.5 w-3.5" label={t('dreams.badge')} />
            <span className="text-text-primary font-mono text-xs font-medium tabular-nums">{dreamId}</span>
          </div>
          <DrawerTitle className="text-text-primary font-display mt-1 text-lg">
            {dream ? `${dream.agent_name} · ${dream.local_date}` : t('dreams.drawer.loading')}
          </DrawerTitle>
          {dream && (
            <div className="mt-1 flex items-center gap-2">
              <span className={[
                'text-overline px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide',
                statusPill(dream.status),
              ].join(' ')}>
                {dreamStatusLabel(dream.status, t)}
              </span>
              {dream.ended_at && (
                <span className="text-text-muted text-xs">
                  {new Date(dream.ended_at).toLocaleTimeString()}
                </span>
              )}
              {dream.error && (
                <span className="text-feedback-danger ml-auto truncate text-xs">{dream.error}</span>
              )}
            </div>
          )}
        </header>

        {/* Body */}
        <section className="flex-1 overflow-y-auto p-4">
          {dreamQ.isLoading ? (
            <div className="animate-pulse space-y-4 p-2">
              {/* Skeleton header row */}
              <div className="flex items-center gap-2">
                <div className="bg-surface-sunken h-3 w-3 rounded-full" />
                <div className="bg-surface-sunken h-3 w-24 rounded" />
              </div>
              <div className="bg-surface-sunken h-4 w-3/4 rounded" />
              {/* Skeleton stat row */}
              <div className="flex items-center gap-3">
                <div className="bg-surface-sunken h-2.5 w-16 rounded" />
                <div className="bg-surface-sunken h-2.5 w-16 rounded" />
                <div className="bg-surface-sunken h-2.5 w-24 rounded" />
              </div>
              {/* Skeleton content cards */}
              <div className="bg-surface border-border-default shadow-pasture-sm h-24 rounded-lg border" />
              <div className="bg-surface border-border-default shadow-pasture-sm h-20 rounded-lg border" />
            </div>
          ) : dreamQ.isError ? (
            <div className="space-y-3 p-4 text-center">
              <p className="text-feedback-danger text-sm">{t('dreams.error.load')}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['dream', orgSlug, dreamId],
                  })
                }
              >
                {t('common.retry')}
              </Button>
            </div>
          ) : dream ? (
            <div className="space-y-4">
              {/* Quote / summary */}
              {dream.summary && (
                <blockquote className="border-accent-default text-text-secondary border-l-2 pl-4 text-sm italic">
                  &ldquo;{dream.summary}&rdquo;
                </blockquote>
              )}

              {/* Quiet-dream indicator — Pasture card */}
              {isQuiet && (
                <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
                  <p className="text-text-primary text-sm font-medium">{t('dreams.quiet.title')}</p>
                  <p className="text-text-muted mt-1 text-xs">{t('dreams.quiet.body')}</p>
                </div>
              )}

              {/* Stat strip — font-mono tabular-nums */}
              <div className="text-text-muted border-border-default flex items-center gap-4 border-b pb-3 font-mono text-xs tabular-nums">
                <span>
                  {formatCount(dream.new_learnings_count, t, 'dreams.count.learnings', 'dreams.count.learningsUnavailable')}
                </span>
                <span>·</span>
                <span>
                  {formatCount(dream.kb_candidate_count, t, 'dreams.count.candidates', 'dreams.count.candidatesUnavailable')}
                  {pendingCount > 0 && (
                    <span className="text-accent-default ml-1 font-medium">{t('dreams.detail.toReview', { count: pendingCount })}</span>
                  )}
                </span>
                {dream.scheduled_for && (
                  <>
                    <span>·</span>
                    <span>{t('dreams.detail.scheduled', { when: new Date(dream.scheduled_for).toLocaleString() })}</span>
                  </>
                )}
              </div>

              {/* Transcript — Pasture card */}
              {dream.transcript && (
                <div className="text-text-secondary bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4 font-mono text-xs whitespace-pre-wrap">
                  {dream.transcript}
                </div>
              )}

              {/* Action error — Pasture error panel */}
              {actionError && (
                <div className="border-feedback-danger/30 bg-feedback-danger/5 rounded-lg border p-4">
                  <p className="text-feedback-danger text-xs">{renderDreamError(actionError, t)}</p>
                </div>
              )}

              {/* KB candidates — review gate */}
              {candidates.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-text-muted text-xs font-medium tracking-wider uppercase">
                    {t('dreams.rail.candidates')}
                  </h3>
                  {candidates.map((c) => (
                    <CandidateCard
                      key={c.id}
                      candidate={c}
                      onAccept={handleAccept}
                      onDismiss={handleDismiss}
                      acceptPending={acceptMutation.isPending}
                      dismissPending={dismissMutation.isPending}
                    />
                  ))}
                </div>
              )}

              {/* Reflection thread link */}
              {dream.founder_thread_id ? (
                <Link
                  to={orgSlug ? `/orgs/${orgSlug}/threads/${dream.founder_thread_id}` : '#'}
                  className="text-accent-default inline-block text-sm hover:underline"
                >
                  {t('dreams.thread.open')} &rarr;
                </Link>
              ) : (
                <p className="text-text-muted text-xs italic">
                  {t('dreams.thread.none')}
                </p>
              )}
            </div>
          ) : null}
        </section>
      </DrawerContent>
    </Drawer>
  );
}

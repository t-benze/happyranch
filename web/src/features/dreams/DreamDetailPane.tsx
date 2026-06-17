/**
 * DreamDetailPane — detail drawer for a single dream.
 *
 * Shows summary/transcript, learnings count, KB candidates with the
 * Accept/Dismiss review gate, and a link to the reflection thread.
 *
 * States: Loading, Populated, Error. Candidate mutations invalidate
 * the dream query so the list re-fetches after accept/dismiss.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
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
import { cn } from '@/lib/utils';
import { DREAM_STRINGS } from './strings';
import type { DreamKbCandidate } from '@/lib/api/dreams';

/* ------------------------------------------------------------------ */
/*  Crescent moon icon (reused)                                        */
/* ------------------------------------------------------------------ */

function CrescentMoonBadge({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      className={cn('text-accent inline-block shrink-0', className)}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a6.4 6.4 0 0 1-4.54 1.86c-3.53 0-6.4-2.87-6.4-6.4 0-1.62.6-3.1 1.6-4.24A9 9 0 0 0 12 3Z" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Status badge                                                       */
/* ------------------------------------------------------------------ */

function statusColor(status: string): string {
  switch (status) {
    case 'completed': return 'bg-accent/10 text-accent';
    case 'failed': case 'timeout': return 'bg-feedback-danger/10 text-feedback-danger';
    case 'missed': case 'skipped': return 'bg-bg-raised text-text-muted';
    case 'running': return 'bg-feedback-success/10 text-feedback-success';
    default: return 'bg-bg-raised text-text-muted';
  }
}

/* ------------------------------------------------------------------ */
/*  Candidate card with review gate                                    */
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
  const isPending = candidate.status === 'pending';
  const isPromoted = candidate.status === 'promoted';
  const isRejected = candidate.status === 'rejected';
  const anyPending = acceptPending || dismissPending;

  const label = isPending
    ? DREAM_STRINGS.candidatePendingLabel(candidate.agent_name)
    : isPromoted
      ? DREAM_STRINGS.candidateAcceptedLabel(candidate.agent_name)
      : isRejected
        ? DREAM_STRINGS.candidateRejectedLabel(candidate.agent_name)
        : candidate.status;

  return (
    <div className="kb-cand border border-border-subtle rounded-lg p-4 bg-surface-sunken">
      {/* Header with dream marker */}
      <div className="flex items-center gap-2 mb-2">
        <CrescentMoonBadge className="w-3 h-3" />
        <span className="text-xs text-text-muted italic">{label}</span>
      </div>

      {/* Title */}
      <h4 className="text-text-primary text-sm font-medium mb-1">{candidate.title}</h4>

      {/* Slug / type / topic */}
      <div className="flex items-center gap-2 text-xs text-text-muted mb-2">
        <span className="font-mono">{candidate.slug}</span>
        <span>·</span>
        <span>{candidate.topic}</span>
      </div>

      {/* Rationale */}
      {candidate.rationale && (
        <p className="text-xs text-text-secondary mb-3 italic">
          &ldquo;{candidate.rationale}&rdquo;
        </p>
      )}

      {/* Body preview */}
      {candidate.body_markdown && (
        <div className="text-xs text-text-secondary mb-3 max-h-32 overflow-hidden border-l-2 border-border-subtle pl-3 font-mono whitespace-pre-wrap">
          {candidate.body_markdown.slice(0, 300)}
          {candidate.body_markdown.length > 300 ? '…' : ''}
        </div>
      )}

      {/* Review gate — only for pending candidates */}
      {isPending && (
        <div className="flex gap-2 mt-3">
          <Button
            size="sm"
            aria-label={DREAM_STRINGS.acceptButton}
            disabled={anyPending}
            onClick={(e) => {
              e.stopPropagation();
              onAccept(candidate.id);
            }}
          >
            {DREAM_STRINGS.acceptButton}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={DREAM_STRINGS.dismissButton}
            disabled={anyPending}
            onClick={(e) => {
              e.stopPropagation();
              onDismiss(candidate.id);
            }}
          >
            {DREAM_STRINGS.dismissButton}
          </Button>
        </div>
      )}

      {/* Resolved indicator */}
      {isPromoted && (
        <p className="text-xs text-feedback-success mt-2">
          Promoted to KB{candidate.promoted_kb_slug ? ` — ${candidate.promoted_kb_slug}` : ''}
        </p>
      )}
      {isRejected && (
        <p className="text-xs text-text-muted mt-2">Dismissed</p>
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
  const { slug } = useParams<{ slug: string }>();
  const dreamQ = useDream(dreamId);
  const acceptMutation = useAcceptCandidate();
  const dismissMutation = useDismissCandidate();
  const [actionError, setActionError] = useState<string | null>(null);

  const dream = dreamQ.data;

  const handleAccept = async (candidateId: number) => {
    setActionError(null);
    try {
      await acceptMutation.mutateAsync(candidateId);
    } catch {
      setActionError('Accept failed — retry');
    }
  };

  const handleDismiss = async (candidateId: number) => {
    setActionError(null);
    try {
      await dismissMutation.mutateAsync(candidateId);
    } catch {
      setActionError('Dismiss failed — retry');
    }
  };

  const candidates = dream?.kb_candidates ?? [];
  const pendingCount = candidates.filter((c) => c.status === 'pending').length;
  const isQuiet = dream?.status === 'completed' && candidates.length === 0 && (dream?.new_learnings_count ?? 0) > 0;

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent className="flex flex-col">
        {/* Header */}
        <header className="border-b border-border-subtle p-4">
          <div className="flex items-center gap-2 mb-1">
            <CrescentMoonBadge className="w-3.5 h-3.5" />
            <span className="font-mono text-xs text-text-primary font-medium">{dreamId}</span>
          </div>
          <DrawerTitle className="text-fg mt-1 text-lg">
            {dream ? `${dream.agent_name} · ${dream.local_date}` : DREAM_STRINGS.drawerLoading}
          </DrawerTitle>
          {dream && (
            <div className="flex items-center gap-2 mt-1">
              <span className={cn(
                'text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide',
                statusColor(dream.status),
              )}>
                {DREAM_STRINGS.statusLabel(dream.status)}
              </span>
              {dream.ended_at && (
                <span className="text-xs text-text-muted">
                  {new Date(dream.ended_at).toLocaleTimeString()}
                </span>
              )}
              {dream.error && (
                <span className="text-xs text-feedback-danger ml-auto truncate">{dream.error}</span>
              )}
            </div>
          )}
        </header>

        {/* Body */}
        <section className="flex-1 overflow-y-auto p-4">
          {dreamQ.isLoading ? (
            <p className="text-text-muted text-sm">{DREAM_STRINGS.drawerLoading}</p>
          ) : dreamQ.isError ? (
            <div className="text-center">
              <p className="text-feedback-danger text-sm">{DREAM_STRINGS.errorTitle}</p>
            </div>
          ) : dream ? (
            <div className="space-y-4">
              {/* Quote / summary */}
              {dream.summary && (
                <blockquote className="text-sm italic border-l-2 border-accent pl-4 text-text-secondary">
                  &ldquo;{dream.summary}&rdquo;
                </blockquote>
              )}

              {/* Quiet-dream indicator */}
              {isQuiet && (
                <div className="rounded-lg border border-border-subtle bg-surface-sunken p-4">
                  <p className="text-text-primary text-sm font-medium">{DREAM_STRINGS.quietTitle}</p>
                  <p className="text-text-muted text-xs mt-1">{DREAM_STRINGS.quietBody}</p>
                </div>
              )}

              {/* Stat strip */}
              <div className="flex items-center gap-4 text-xs text-text-muted border-b border-border-subtle pb-3">
                <span>{DREAM_STRINGS.learningsCount(dream.new_learnings_count)}</span>
                <span>·</span>
                <span>
                  {DREAM_STRINGS.candidatesCount(dream.kb_candidate_count)}
                  {pendingCount > 0 && (
                    <span className="text-accent ml-1 font-medium">{pendingCount} to review</span>
                  )}
                </span>
                {dream.scheduled_for && (
                  <>
                    <span>·</span>
                    <span>Scheduled {new Date(dream.scheduled_for).toLocaleString()}</span>
                  </>
                )}
              </div>

              {/* Transcript */}
              {dream.transcript && (
                <div className="text-xs text-text-secondary whitespace-pre-wrap font-mono bg-surface-sunken rounded-lg p-4 border border-border-subtle">
                  {dream.transcript}
                </div>
              )}

              {/* Action error */}
              {actionError && (
                <div className="rounded border border-feedback-danger/30 bg-feedback-danger/5 p-3">
                  <p className="text-feedback-danger text-xs">{actionError}</p>
                </div>
              )}

              {/* KB candidates — review gate */}
              {candidates.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-text-muted text-xs font-medium tracking-wider uppercase">
                    Knowledge candidates
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
                  to={slug ? `/orgs/${slug}/threads/${dream.founder_thread_id}` : '#'}
                  className="text-accent text-sm hover:underline inline-block"
                >
                  {DREAM_STRINGS.openReflectionThread} &rarr;
                </Link>
              ) : (
                <p className="text-xs text-text-disabled italic">
                  {DREAM_STRINGS.noReflectionThread}
                </p>
              )}
            </div>
          ) : null}
        </section>
      </DrawerContent>
    </Drawer>
  );
}

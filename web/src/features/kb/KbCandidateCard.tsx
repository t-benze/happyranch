/**
 * KbCandidateCard — candidate review gate for the Knowledge surface.
 *
 * Reuses the same useAcceptCandidate/useDismissCandidate hooks from
 * @/hooks/dreams that the Dreams surface uses (merged PR #113).
 * The card shares the same semantics but uses KB-specific strings.
 *
 * States: pending (shows Accept/Dismiss), promoted (shows slug link),
 * rejected (shows dismissed).
 */
import { useState } from 'react';
import { useAcceptCandidate, useDismissCandidate } from '@/hooks/dreams';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { KB_STRINGS } from './strings';
import type { DreamKbCandidate } from '@/hooks/dreams';

/* ------------------------------------------------------------------ */
/*  Crescent moon icon                                                 */
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
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface KbCandidateCardProps {
  candidate: DreamKbCandidate;
  /** Called after successful accept/dismiss with the resolved status. */
  onResolved?: (result: {
    status: string;
    promotedKbSlug: string | null;
  }) => void;
}

/* ------------------------------------------------------------------ */
/*  Card                                                               */
/* ------------------------------------------------------------------ */

export function KbCandidateCard({
  candidate,
  onResolved,
}: KbCandidateCardProps): JSX.Element {
  const [mutationError, setMutationError] = useState<string | null>(null);
  const acceptMutation = useAcceptCandidate();
  const dismissMutation = useDismissCandidate();
  const anyPending = acceptMutation.isPending || dismissMutation.isPending;

  const isPending = candidate.status === 'pending';
  const isPromoted = candidate.status === 'promoted';
  const isRejected = candidate.status === 'rejected';

  const label = isPending
    ? KB_STRINGS.candidatePendingLabel(candidate.agent_name)
    : isPromoted
      ? KB_STRINGS.candidateAcceptedLabel(candidate.agent_name)
      : isRejected
        ? KB_STRINGS.candidateRejectedLabel(candidate.agent_name)
        : candidate.status;

  const handleAccept = async () => {
    setMutationError(null);
    try {
      const result = await acceptMutation.mutateAsync(candidate.id);
      onResolved?.({
        status: result.status,
        promotedKbSlug: result.promoted_kb_slug,
      });
    } catch {
      setMutationError('Accept failed — retry');
    }
  };

  const handleDismiss = async () => {
    setMutationError(null);
    try {
      const result = await dismissMutation.mutateAsync(candidate.id);
      onResolved?.({
        status: result.status,
        promotedKbSlug: result.promoted_kb_slug,
      });
    } catch {
      setMutationError('Dismiss failed — retry');
    }
  };

  return (
    <div className="kb-cand border border-border-subtle rounded-lg p-4 bg-surface-sunken">
      {/* Header with dream marker */}
      <div className="flex items-center gap-2 mb-2">
        <CrescentMoonBadge className="w-3 h-3" />
        <span className="text-xs text-text-muted italic">{label}</span>
      </div>

      {/* Title */}
      <h4 className="text-text-primary text-sm font-medium mb-1">{candidate.title}</h4>

      {/* Slug / topic */}
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

      {/* Error */}
      {mutationError && (
        <div className="rounded border border-feedback-danger/30 bg-feedback-danger/5 p-3 mb-2">
          <p className="text-feedback-danger text-xs">{mutationError}</p>
          <div className="flex gap-2 mt-2">
            <Button size="sm" onClick={handleAccept} disabled={anyPending}>
              {KB_STRINGS.acceptButton}
            </Button>
            <Button size="sm" variant="ghost" onClick={handleDismiss} disabled={anyPending}>
              {KB_STRINGS.dismissButton}
            </Button>
          </div>
        </div>
      )}

      {/* Review gate — only for pending candidates, only if no error */}
      {isPending && !mutationError && (
        <div className="flex gap-2 mt-3">
          <Button
            size="sm"
            aria-label={KB_STRINGS.acceptButton}
            disabled={anyPending}
            tabIndex={0}
            onClick={handleAccept}
          >
            {KB_STRINGS.acceptButton}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={KB_STRINGS.dismissButton}
            disabled={anyPending}
            tabIndex={0}
            onClick={handleDismiss}
          >
            {KB_STRINGS.dismissButton}
          </Button>
        </div>
      )}

      {/* Resolved indicator */}
      {isPromoted && (
        <p className="text-xs text-feedback-success mt-2">
          Promoted to KB
          {candidate.promoted_kb_slug ? ` — ${candidate.promoted_kb_slug}` : ''}
        </p>
      )}
      {isRejected && (
        <p className="text-xs text-text-muted mt-2">Dismissed</p>
      )}
    </div>
  );
}

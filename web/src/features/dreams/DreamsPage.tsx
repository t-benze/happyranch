/**
 * DreamsPage — the reflection feed (§4.8).
 *
 * Lists dreams with card routing: .dream-card -> dream detail,
 * .kb-cand -> KB candidate detail (within the dream detail drawer),
 * "Open reflection thread" -> the dream's thread.
 *
 * States: Loading (skeleton), Empty ("No dreams yet"),
 * Quiet (positive first-class per §2.5.5), Error (retry).
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDreamsList } from '@/hooks/dreams';
import { Button } from '@/design-system/primitives/Button';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { cn } from '@/lib/utils';
import { DreamDetailPane } from './DreamDetailPane';
import { DREAM_STRINGS } from './strings';
import type { DreamRecord } from '@/hooks/dreams';

/* ------------------------------------------------------------------ */
/*  Dream marker — crescent moon SVG badge                             */
/* ------------------------------------------------------------------ */

function CrescentMoonBadge({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      className={cn('text-accent inline-block shrink-0', className)}
      width="16"
      height="16"
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
/*  Relative time helper                                               */
/* ------------------------------------------------------------------ */

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

/* ------------------------------------------------------------------ */
/*  Dream card                                                         */
/* ------------------------------------------------------------------ */

function DreamCard({
  dream,
  slug,
  active,
  onClick,
}: {
  dream: DreamRecord;
  slug: string;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  const isQuiet = dream.status === 'completed' && dream.kb_candidate_count === 0 && dream.new_learnings_count > 0;

  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'dream-card w-full text-left p-4 border-b border-border-subtle',
          'hover:bg-surface-sunken transition-colors',
          active && 'bg-surface-sunken',
        )}
      >
        {/* Header row */}
        <div className="flex items-center gap-2 mb-1">
          <CrescentMoonBadge className="w-3.5 h-3.5" />
          <span className="text-xs font-mono text-text-primary font-medium">{dream.dream_id}</span>
          <span className="text-xs text-text-muted">·</span>
          <span className="text-xs text-text-muted">{dream.agent_name}</span>
          <span className={cn(
            'ml-auto text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide',
            statusColor(dream.status),
          )}>
            {DREAM_STRINGS.statusLabel(dream.status)}
          </span>
        </div>

        {/* Quote / summary */}
        {dream.summary && (
          <p className={cn(
            'text-sm italic border-l-2 border-accent pl-3 mb-2',
            'text-text-secondary line-clamp-2',
          )}>
            {dream.summary}
          </p>
        )}

        {/* Quiet-dream state — positive first-class */}
        {isQuiet && (
          <p className="text-xs text-text-muted italic mb-2">
            {DREAM_STRINGS.quietTitle}
          </p>
        )}

        {/* Stat strip */}
        <div className="flex items-center gap-3 text-xs text-text-muted">
          <span>{dream.local_date}</span>
          <span>·</span>
          <span>{DREAM_STRINGS.learningsCount(dream.new_learnings_count)}</span>
          <span>·</span>
          <span>{DREAM_STRINGS.candidatesCount(dream.kb_candidate_count)}</span>
          {dream.ended_at && (
            <>
              <span>·</span>
              <span>{relativeAge(dream.ended_at)} ago</span>
            </>
          )}
        </div>

        {/* Error indicator */}
        {dream.error && (
          <p className="text-xs text-feedback-danger mt-1 font-mono truncate">{dream.error}</p>
        )}

        {/* Open reflection thread link */}
        <div className="mt-2">
          {dream.founder_thread_id ? (
            <Link
              to={`/orgs/${slug}/threads/${dream.founder_thread_id}`}
              className="text-xs text-accent hover:underline inline-block"
              onClick={(e) => e.stopPropagation()}
            >
              {DREAM_STRINGS.openReflectionThread} &rarr;
            </Link>
          ) : (
            <span className="text-xs text-text-disabled italic">
              {DREAM_STRINGS.noReflectionThread}
            </span>
          )}
        </div>
      </button>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  return (
    <div className="animate-pulse space-y-4 p-4">
      {[1, 2, 3].map((i) => (
        <div key={i} className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="bg-bg-raised h-3 w-3 rounded-full" />
            <div className="bg-bg-raised h-3 w-16 rounded" />
            <div className="bg-bg-raised h-3 w-24 rounded" />
          </div>
          <div className="bg-bg-raised h-3 w-3/4 rounded" />
          <div className="bg-bg-raised h-3 w-1/2 rounded" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function DreamsPage(): JSX.Element {
  const { slug: orgSlug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const dreamsQ = useDreamsList();
  const [selectedDreamId, setSelectedDreamId] = useState<string | null>(null);

  const dreams = dreamsQ.data?.dreams ?? [];

  return (
    <div className="flex h-full">
      {/* List */}
      <main className="flex-1 overflow-y-auto border-r border-border-subtle bg-surface-canvas">
        <header className="border-b border-border-subtle p-4">
          <h1 className="text-h2 text-text-primary">{DREAM_STRINGS.pageTitle}</h1>
          <p className="text-text-muted text-sm">{DREAM_STRINGS.pageSubtitle}</p>
        </header>

        {dreamsQ.isLoading ? (
          <LoadingSkeleton />
        ) : dreamsQ.isError ? (
          <div className="p-4 text-center space-y-3">
            <p className="text-feedback-danger text-sm">{DREAM_STRINGS.errorTitle}</p>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                queryClient.invalidateQueries({
                  queryKey: ['dreams-list', orgSlug],
                })
              }
            >
              {DREAM_STRINGS.retry}
            </Button>
          </div>
        ) : dreams.length === 0 ? (
          <EmptyState
            title={DREAM_STRINGS.emptyTitle}
            body={DREAM_STRINGS.emptyBody}
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {dreams.map((d) => (
              <DreamCard
                key={d.dream_id}
                dream={d}
                slug={orgSlug ?? ''}
                active={selectedDreamId === d.dream_id}
                onClick={() =>
                  setSelectedDreamId((prev) =>
                    prev === d.dream_id ? null : d.dream_id,
                  )
                }
              />
            ))}
          </ul>
        )}
      </main>

      {/* Detail drawer */}
      {selectedDreamId && (
        <DreamDetailPane
          dreamId={selectedDreamId}
          onClose={() => setSelectedDreamId(null)}
        />
      )}
    </div>
  );
}

/**
 * DreamsPage — the reflection feed (§4.8). Direction-A Pasture fidelity
 * pass (THR-030 Leg B Batch 9).
 *
 * Lists dreams with Pasture card routing: dream card -> dream detail,
 * .kb-cand -> KB candidate detail (within the dream detail drawer),
 * "Open reflection thread" -> the dream's thread.
 *
 * Pasture vocabulary:
 *   Cards: bg-surface + border-border-default + shadow-pasture-sm + rounded-lg
 *   Heading: font-display serif (Newsreader) page heading + PageHeader
 *   Dream ID / timestamps / counts: font-mono tabular-nums
 *   Status pills: rounded-full bg-accent-soft/text-accent-text (completed),
 *     bg-danger-soft/text-feedback-danger (failed/timeout)
 *   List rail: w-rail (244px)
 *   Semantic text: text-text-primary / secondary / muted — no hardcoded colors
 *   Calm empty state: EmptyState with display heading
 *
 * States: Loading (skeleton cards), Empty ("No dreams yet"),
 * Quiet (positive first-class per §2.5.5), Error (retry).
 *
 * HONESTY FENCE: renders ONLY fields from DreamRecord data model
 * (dream_id, agent_name, local_date, status, summary, new_learnings_count,
 * kb_candidate_count, founder_thread_id, error). No fabricated provenance,
 * sub-states, scores, or schedule glance fields not on the model.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDreamsList } from '@/hooks/dreams';
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { cn } from '@/lib/utils';
import { DreamDetailPane } from './DreamDetailPane';
import { DREAM_STRINGS } from './strings';
import type { DreamRecord } from '@/hooks/dreams';

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
/*  Dream card — Pasture card pattern                                  */
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
          'bg-surface border border-border-default shadow-pasture-sm hover:bg-surface-sunken',
          'w-full text-left p-4 rounded-lg transition-colors',
          active && 'ring-2 ring-accent-soft',
        )}
      >
        {/* Header row — dream ID + agent + status pill */}
        <div className="mb-1.5 flex items-center gap-2">
          <CrescentMoonBadge className="h-3.5 w-3.5" />
          <span className="text-text-primary font-mono text-xs font-medium tabular-nums">{dream.dream_id}</span>
          <span className="text-text-muted text-xs">·</span>
          <span className="text-text-secondary text-xs">{dream.agent_name}</span>
          <span className={cn(
            'ml-auto text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide',
            statusPill(dream.status),
          )}>
            {DREAM_STRINGS.statusLabel(dream.status)}
          </span>
        </div>

        {/* Quote / summary — italic with accent left border */}
        {dream.summary && (
          <p className={cn(
            'text-sm italic border-l-2 border-accent-default pl-3 mb-2',
            'text-text-secondary line-clamp-2',
          )}>
            {dream.summary}
          </p>
        )}

        {/* Quiet-dream state — positive first-class */}
        {isQuiet && (
          <p className="text-text-muted mb-2 text-xs italic">
            {DREAM_STRINGS.quietTitle}
          </p>
        )}

        {/* Stat strip — font-mono tabular-nums for counts */}
        <div className="text-text-muted flex items-center gap-3 font-mono text-xs tabular-nums">
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
          <p className="text-feedback-danger mt-1 truncate font-mono text-xs">{dream.error}</p>
        )}

        {/* Open reflection thread link */}
        <div className="mt-2">
          {dream.founder_thread_id ? (
            <Link
              to={`/orgs/${slug}/threads/${dream.founder_thread_id}`}
              className="text-accent-default inline-block text-xs hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {DREAM_STRINGS.openReflectionThread} &rarr;
            </Link>
          ) : (
            <span className="text-text-muted text-xs italic">
              {DREAM_STRINGS.noReflectionThread}
            </span>
          )}
        </div>
      </button>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton — Pasture card-shaped                             */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  return (
    <div className="flex flex-col gap-3 p-4" aria-label="Loading dreams">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="bg-surface border-border-default shadow-pasture-sm animate-pulse space-y-2 rounded-lg border p-4"
        >
          <div className="flex items-center gap-2">
            <div className="bg-surface-sunken h-3.5 w-3.5 rounded-full" />
            <div className="bg-surface-sunken h-3 w-16 rounded" />
            <div className="bg-surface-sunken h-3 w-24 rounded" />
          </div>
          <div className="bg-surface-sunken h-3 w-3/4 rounded" />
          <div className="bg-surface-sunken h-3 w-1/2 rounded" />
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
      {/* List rail — w-rail (244px) */}
      <main className="w-rail border-border-default bg-surface shrink-0 overflow-y-auto border-r">
        <header className="border-border-default border-b p-4">
          <PageHeader
            title={<span className="font-display">{DREAM_STRINGS.pageTitle}</span>}
            meta={DREAM_STRINGS.pageSubtitle}
          />
        </header>

        {dreamsQ.isLoading ? (
          <LoadingSkeleton />
        ) : dreamsQ.isError ? (
          <div className="space-y-3 p-4 text-center">
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
          <div className="flex flex-col gap-1 p-3">
            {/* Count eyebrow — Pasture label */}
            <p className="text-text-secondary mb-1 px-1 text-xs font-semibold tracking-wider uppercase">
              {dreams.length} dream{dreams.length !== 1 ? 's' : ''}
            </p>
            <ul className="flex flex-col gap-3">
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
          </div>
        )}
      </main>

      {/* Detail drawer — fills remaining space */}
      {selectedDreamId && (
        <DreamDetailPane
          dreamId={selectedDreamId}
          onClose={() => setSelectedDreamId(null)}
        />
      )}
    </div>
  );
}

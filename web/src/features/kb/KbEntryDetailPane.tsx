/**
 * KbEntryDetailPane — detail drawer for a KB entry or candidate.
 *
 * For regular KB entries: renders the markdown body with metadata.
 * For dream KB candidates: renders the candidate body with the
 * Accept/Dismiss review gate (shared logic from KbCandidateCard).
 *
 * Accept → candidate becomes a live KB entry, banner collapses,
 * the detail re-fetches the now-live entry.
 *
 * States: Loading (skeleton), Populated (entry or candidate),
 * Error (retry). Candidate mutations invalidate both KB and dreams
 * queries so the pending queue stays consistent.
 */
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { Markdown } from '@/design-system/patterns/Markdown';
import { Button } from '@/design-system/primitives/Button';
import { useKBEntry, useKbRoutes } from '@/hooks/kb';
import { useTasksRoutes } from '@/hooks/tasks';
import { KB_STRINGS } from './strings';
import { KbCandidateCard } from './KbCandidateCard';
import type { DreamKbCandidate } from '@/hooks/dreams';

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

export interface KbEntryDetailPaneProps {
  /** Slug of a live KB entry. */
  entrySlug?: string;
  /** Candidate object for the review gate. Mutually exclusive with entrySlug. */
  candidate?: DreamKbCandidate;
  /** Called when the drawer closes. */
  onClose: () => void;
}

export function KbEntryDetailPane({
  entrySlug,
  candidate,
  onClose,
}: KbEntryDetailPaneProps): JSX.Element {
  const queryClient = useQueryClient();
  const kbRoutes = useKbRoutes();
  const tasksRoutes = useTasksRoutes();

  // For regular entries, fetch from KB API
  const entryQuery = useKBEntry(entrySlug);
  const entry = entryQuery.data;

  const isCandidate = !!candidate;
  const handleCandidateResolved = () => {
    // Invalidate both KB and dreams queries so surfaces stay consistent
    queryClient.invalidateQueries({ queryKey: ['kb-list'] });
    queryClient.invalidateQueries({ queryKey: ['kb-search'] });
    queryClient.invalidateQueries({ queryKey: ['dreams-list'] });
    queryClient.invalidateQueries({ queryKey: ['dream'] });
  };

  const handleClose = () => {
    onClose();
  };

  return (
    <Drawer open onOpenChange={(o) => !o && handleClose()}>
      <DrawerContent className="flex flex-col">
        {/* Header */}
        <header className="border-b border-border-subtle p-4">
          {isCandidate ? (
            <>
              <div className="text-fg-muted font-mono text-xs">
                {candidate.slug}
              </div>
              <DrawerTitle className="text-fg mt-1 text-lg">
                {candidate.title}
              </DrawerTitle>
              <p className="text-fg-muted mt-1 text-xs">
                {candidate.topic} · from {candidate.dream_id}
              </p>
            </>
          ) : entry ? (
            <>
              <div className="text-fg-muted font-mono text-xs">{entrySlug}</div>
              <DrawerTitle className="text-fg mt-1 text-lg">
                {entry.title}
              </DrawerTitle>
              <p className="text-fg-muted mt-1 text-xs">
                {entry.type} · updated {relativeAge(entry.updated_at)} ·{' '}
                {KB_STRINGS.authoredBy(entry.authored_by)}
              </p>
              {entry.tags.length > 0 && (
                <p className="text-fg-muted mt-1 text-xs">
                  {entry.tags.join(', ')}
                </p>
              )}
            </>
          ) : (
            <>
              <div className="text-fg-muted font-mono text-xs">
                {entrySlug ?? ''}
              </div>
              <DrawerTitle className="text-fg mt-1 text-lg">
                {KB_STRINGS.drawerLoading}
              </DrawerTitle>
            </>
          )}
        </header>

        {/* Body */}
        <section className="flex-1 overflow-y-auto p-4">
          {isCandidate ? (
            <div className="space-y-4">
              {/* Body preview (full, no truncation in detail) */}
              {candidate.body_markdown && (
                <div className="text-sm text-text-secondary whitespace-pre-wrap font-mono bg-surface-sunken rounded-lg p-4 border border-border-subtle">
                  {candidate.body_markdown}
                </div>
              )}

              {/* Candidate review gate — sticky banner */}
              <div className="sticky bottom-0">
                <KbCandidateCard
                  candidate={candidate}
                  onResolved={handleCandidateResolved}
                />
              </div>
            </div>
          ) : entryQuery.isLoading ? (
            <div className="animate-pulse space-y-4 p-2">
              <div className="bg-bg-raised h-4 w-3/4 rounded" />
              <div className="bg-bg-raised h-4 w-1/2 rounded" />
              <div className="bg-bg-raised h-32 rounded" />
            </div>
          ) : entryQuery.isError ? (
            <div className="text-center space-y-3 p-4">
              <p className="text-feedback-danger text-sm">
                Could not load entry
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['kb-entry', undefined, entrySlug],
                  })
                }
              >
                {KB_STRINGS.retry}
              </Button>
            </div>
          ) : entry ? (
            <div className="space-y-4">
              <Markdown body={entry.body} />

              {/* Source task badge */}
              {entry.source_task && (
                <p className="text-fg-muted mt-6 text-xs">
                  {KB_STRINGS.sourceTaskLabel}{' '}
                  <IdBadge
                    kind="task"
                    id={entry.source_task}
                    to={tasksRoutes.detail(entry.source_task)}
                  />
                </p>
              )}

              {/* Related entries */}
              {entry.related_entries && entry.related_entries.length > 0 && (
                <div className="text-fg-muted mt-3 text-xs">
                  <div>{KB_STRINGS.relatedEntriesLabel}</div>
                  <ul className="mt-1 list-disc pl-5">
                    {entry.related_entries.map((slug) => (
                      <li key={slug}>
                        <Link
                          to={kbRoutes.detail(slug)}
                          className="text-accent hover:underline"
                        >
                          {slug}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : null}
        </section>
      </DrawerContent>
    </Drawer>
  );
}

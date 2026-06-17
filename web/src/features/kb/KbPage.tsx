/**
 * KbPage — the Knowledge surface (§4.5).
 *
 * List view: folder rail (filters by topic) + stacked entry feed.
 * Pending dream-proposed candidates appear first, visually distinct
 * with the accent crescent-moon glyph. The pending-count tag
 * shows the total candidate count from dreams data.
 *
 * Detail view: fully-rendered KB doc by slug OR candidate review
 * gate with Accept/Dismiss. Accept promotes the candidate to a live
 * entry (shared STEP-1 route, consistent across surfaces).
 *
 * States: Loading skeleton, Empty ("No entries yet"), Error (retry).
 */
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useKbRoutes, useKBList } from '@/hooks/kb';
import { useDensity } from '@/hooks/density';
import { useDreamsList, useDream } from '@/hooks/dreams';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Input } from '@/design-system/primitives/Input';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { KbEntryCard } from './KbEntryCard';
import { KbEntryDetailPane } from './KbEntryDetailPane';
import { ComposeKbEntryDialog } from './ComposeKbEntryDialog';
import { KB_STRINGS } from './strings';
import type { DreamKbCandidate } from '@/hooks/dreams';

const COMPOSE_ENABLED = import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true';

/* ------------------------------------------------------------------ */
/*  Crescent moon icon                                                 */
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
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  return (
    <div className="animate-pulse space-y-4 p-4">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="h-3 w-20 rounded bg-bg-raised" />
            <div className="h-3 w-16 rounded bg-bg-raised" />
          </div>
          <div className="h-4 w-3/4 rounded bg-bg-raised" />
          <div className="h-3 w-1/2 rounded bg-bg-raised" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  DreamCandidateRow — fetches one dream's candidates                */
/* ------------------------------------------------------------------ */

function DreamCandidateRow({
  dreamId,
  onSelect,
  detailCandidate,
}: {
  dreamId: string;
  onSelect: (c: DreamKbCandidate) => void;
  detailCandidate: DreamKbCandidate | null;
}): JSX.Element | null {
  const dreamQ = useDream(dreamId);

  if (dreamQ.isLoading) return null;
  if (!dreamQ.data?.kb_candidates) return null;

  const pending = dreamQ.data.kb_candidates.filter(
    (c) => c.status === 'pending',
  );

  if (pending.length === 0) return null;

  return (
    <>
      {pending.map((c) => (
        <li key={`cand-${c.id}`}>
          <button
            type="button"
            onClick={() => onSelect(c)}
            className={cn(
              'kb-cand w-full text-left p-3 border-b border-border-subtle',
              'hover:bg-surface-sunken transition-colors',
              detailCandidate?.id === c.id && 'bg-surface-sunken',
            )}
          >
            <div className="flex items-center gap-2 mb-1">
              <CrescentMoonBadge className="w-3 h-3" />
              <span className="text-xs text-text-primary font-medium font-mono">
                {c.slug}
              </span>
              <span className="text-2xs px-1 rounded-full font-medium bg-accent/10 text-accent ml-auto">
                pending review
              </span>
            </div>
            <p className="text-sm text-text-primary font-medium line-clamp-1">
              {c.title}
            </p>
            <p className="text-xs text-text-muted mt-0.5">
              from dream · proposed by {c.agent_name}
            </p>
          </button>
        </li>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function KbPage(): JSX.Element {
  const params = useParams<{ '*'?: string; slug?: string }>();
  const openSlug = params['*'] && params['*'].length > 0 ? params['*'] : undefined;
  const navigate = useNavigate();
  const [folder, setFolder] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [detailCandidate, setDetailCandidate] = useState<DreamKbCandidate | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const { density } = useDensity();
  const queryClient = useQueryClient();
  const routes = useKbRoutes();

  // Fetch live KB entries, filtered by type (folder)
  const listQuery = useKBList(folder ? { type: folder } : undefined);
  const liveEntries = listQuery.data?.entries ?? [];

  // Fetch dreams to find those with candidates
  const dreamsQuery = useDreamsList({ limit: 50 });
  const dreamsWithCandidates = useMemo(() => {
    if (!dreamsQuery.data?.dreams) return [];
    return dreamsQuery.data.dreams.filter((d) => d.kb_candidate_count > 0);
  }, [dreamsQuery.data?.dreams]);

  // Candidate count from dreams list data (server-reported, not a UI guess)
  const pendingCount = useMemo(() => {
    return dreamsWithCandidates.reduce(
      (sum, d) => sum + d.kb_candidate_count,
      0,
    );
  }, [dreamsWithCandidates]);

  // Build folder filter options from KB entry types.
  // FilterSidebar renders its own "All" button — don't duplicate.
  const folders = useMemo(() => {
    const set = new Set<string>();
    liveEntries.forEach((e) => set.add(e.type));
    return Array.from(set).sort();
  }, [liveEntries]);

  const filterGroups: FilterGroup[] = [
    {
      key: 'folder',
      label: KB_STRINGS.filterFolders,
      options: folders.map((f) => ({ value: f, label: f })),
    },
  ];

  const filterState: Record<string, string | null> = { folder };
  const handleFilterChange = (next: Record<string, string | null>) => {
    const val = next.folder;
    setFolder(val || null);
  };

  const loading = listQuery.isLoading || dreamsQuery.isLoading;

  // Handle candidate select
  const handleCandidateSelect = (candidate: DreamKbCandidate) => {
    setDetailCandidate((prev) =>
      prev?.id === candidate.id ? null : candidate,
    );
  };

  const detailOpen = !!openSlug || !!detailCandidate;

  const hasEntries = liveEntries.length > 0 || dreamsWithCandidates.length > 0;

  return (
    <div className="flex h-full">
      {/* Folder rail */}
      <aside
        aria-label="KB folders"
        className="w-56 shrink-0 overflow-y-auto border-r border-border-subtle bg-surface-sunken p-3"
      >
        <div className="mb-3">
          <Input
            aria-label="Search KB entries"
            placeholder={KB_STRINGS.searchPlaceholder}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <FilterSidebar
          groups={filterGroups}
          value={filterState}
          onChange={handleFilterChange}
        />
      </aside>

      {/* Main feed area */}
      <main className="flex-1 overflow-y-auto bg-surface-canvas">
        {/* Header */}
        <header className="border-b border-border-subtle p-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-h2 text-text-primary">{KB_STRINGS.pageTitle}</h1>
              <p className="text-text-muted text-sm">
                {KB_STRINGS.pageSubtitle}
              </p>
            </div>
            {COMPOSE_ENABLED && (
              <Button size="sm" onClick={() => setComposeOpen(true)}>
                {KB_STRINGS.composeButton}
              </Button>
            )}
            {pendingCount > 0 && (
              <span className="text-xs font-medium text-accent bg-accent/10 px-2 py-1 rounded-full">
                {KB_STRINGS.pendingCandidatesTag(pendingCount)}
              </span>
            )}
          </div>
        </header>

        {/* Content */}
        {loading ? (
          <LoadingSkeleton />
        ) : listQuery.isError ? (
          <div className="p-4 text-center space-y-3">
            <p className="text-feedback-danger text-sm">
              Could not load Knowledge
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ['kb-list'] });
                queryClient.invalidateQueries({ queryKey: ['dreams-list'] });
              }}
            >
              {KB_STRINGS.retry}
            </Button>
          </div>
        ) : !hasEntries ? (
          <EmptyState
            title={KB_STRINGS.emptyListTitle}
            body={KB_STRINGS.emptyListBody}
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {/* Dream candidates (loaded per-dream via DreamCandidateRow) */}
            {dreamsWithCandidates.map((d) => (
              <DreamCandidateRow
                key={d.dream_id}
                dreamId={d.dream_id}
                onSelect={handleCandidateSelect}
                detailCandidate={detailCandidate}
              />
            ))}

            {/* Live KB entries */}
            {liveEntries.map((entry) => (
              <li key={entry.slug}>
                <KbEntryCard
                  entry={entry}
                  to={routes.detail(entry.slug)}
                  active={openSlug === entry.slug}
                  density={density}
                />
              </li>
            ))}
          </ul>
        )}
      </main>

      {/* Detail drawer */}
      {detailOpen && (
        <KbEntryDetailPane
          entrySlug={openSlug}
          candidate={detailCandidate ?? undefined}
          onClose={() => {
            setDetailCandidate(null);
            if (openSlug) {
              navigate(routes.inbox());
            }
          }}
        />
      )}
      {composeOpen && (
        <ComposeKbEntryDialog onClose={() => setComposeOpen(false)} />
      )}
    </div>
  );
}

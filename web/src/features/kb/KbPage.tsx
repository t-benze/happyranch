/**
 * KbPage — the Knowledge surface (§4.5).
 *
 * List view: folder rail (filters by topic) + stacked entry feed.
 * The feed shows live KB documents ONLY. Pending dream-proposed
 * candidates live in a DEDICATED Candidates view — reached via the
 * rail's "Candidates (N)" row or the header pending pill — so they
 * never flood the document feed. Candidate cards keep the accent
 * crescent-moon glyph. The pending-count (N) is the live count of
 * per-dream `status==='pending'` candidates, not the stored total.
 *
 * Detail view: fully-rendered KB doc by slug OR candidate review
 * gate with Accept/Dismiss. Accept promotes the candidate to a live
 * entry (shared STEP-1 route, consistent across surfaces).
 *
 * Usage label: "viewed Nx (CLI)" from kb_views data (PRD §4.5 K1).
 *
 * Search: debounced 200ms search via /kb/search.
 *
 * States: Loading skeleton, Empty ("No entries yet"), Error (retry).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useKbRoutes, useKBList, useKBSearch, useKBStats } from '@/hooks/kb';
import { useDensity } from '@/hooks/density';
import { useDreamsList, useDream } from '@/hooks/dreams';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
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
/*  Folder rail icons + grouped rail (KB-01)                           */
/* ------------------------------------------------------------------ */

const RAIL_ICON_PROPS = {
  width: 16,
  height: 16,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  'aria-hidden': true,
} as const;

/** Library/drawer glyph for the "All entries" row (matches a-knowledge). */
function LibraryIcon(): JSX.Element {
  return (
    <svg className="shrink-0" {...RAIL_ICON_PROPS}>
      <path d="M3 5h18v14H3z" />
      <path d="M3 9h18" />
    </svg>
  );
}

/** Folder glyph for each per-type row (matches a-knowledge). */
function FolderIcon(): JSX.Element {
  return (
    <svg className="shrink-0" {...RAIL_ICON_PROPS}>
      <path d="M3 7a2 2 0 012-2h4l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
    </svg>
  );
}

/**
 * GroupedFolderRail — KB-01. Replaces the flat type list with labeled
 * sections (folder icons + per-folder counts), matching the Direction-A
 * `a-knowledge` reference.
 *
 * DATA FENCE (Confusion Protocol): the design also calls for ENGINEERING
 * (review/qa/build) vs ORG (protocols/from-dreams) origin sections. The
 * kb-list payload carries no origin/category/path field to back that split,
 * and KBEntry has no "from dream" flag, so those folders are honestly OMITTED
 * here (not zero-faked). The backed grouping below is over the existing
 * `type` field — the same dimension the flat rail already filtered on.
 */
function GroupedFolderRail({
  folders,
  counts,
  total,
  selected,
  onSelect,
  candidatesCount,
  candidatesActive,
  onSelectCandidates,
}: {
  folders: string[];
  counts: Map<string, number>;
  total: number;
  selected: string | null;
  onSelect: (type: string | null) => void;
  /** Live pending-candidate count (same signal as the header pill). */
  candidatesCount: number;
  /** Whether the dedicated Candidates view is the active selection. */
  candidatesActive: boolean;
  onSelectCandidates: () => void;
}): JSX.Element {
  const rowClass = (active: boolean) =>
    cn(
      'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm',
      active
        ? 'bg-accent-muted text-accent-text font-medium'
        : 'text-text-muted hover:bg-surface-raised',
    );
  const countClass = 'ml-auto font-mono text-xs tabular-nums';

  return (
    <div className="space-y-4">
      <section>
        <h3 className="text-text-muted font-display mb-1.5 px-2.5 text-2xs font-medium tracking-wider uppercase">
          {KB_STRINGS.railLibrarySection}
        </h3>
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={rowClass(selected == null && !candidatesActive)}
        >
          <LibraryIcon />
          <span>{KB_STRINGS.railAllEntries}</span>
          <span className={countClass}>{total}</span>
        </button>
      </section>

      {/* Candidates — dream-proposed entries awaiting review live in their OWN
          view so they never flood the live-document feed. Shown only when there
          is live pending work (mirrors the header pill's `> 0` gate). */}
      {candidatesCount > 0 && (
        <section>
          <button
            type="button"
            onClick={onSelectCandidates}
            className={rowClass(candidatesActive)}
          >
            <CrescentMoonBadge className="h-4 w-4" />
            <span>{KB_STRINGS.railCandidates}</span>
            <span className={countClass}>{candidatesCount}</span>
          </button>
        </section>
      )}

      {folders.length > 0 && (
        <section>
          <h3 className="text-text-muted font-display mb-1.5 px-2.5 text-2xs font-medium tracking-wider uppercase">
            {KB_STRINGS.filterFolders}
          </h3>
          <ul className="space-y-0.5">
            {folders.map((f) => (
              <li key={f}>
                <button
                  type="button"
                  onClick={() => onSelect(f)}
                  className={rowClass(selected === f && !candidatesActive)}
                >
                  <FolderIcon />
                  <span className="truncate">{f}</span>
                  <span className={countClass}>{counts.get(f) ?? 0}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  return (
    <div className="animate-pulse space-y-4">
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
  onPendingCountChange,
  visible,
}: {
  dreamId: string;
  onSelect: (c: DreamKbCandidate) => void;
  detailCandidate: DreamKbCandidate | null;
  onPendingCountChange?: (dreamId: string, count: number) => void;
  /**
   * When false the row still fetches and REPORTS its pending count (so the
   * header pill + rail 'Candidates' row stay live) but renders no cards. The
   * cards are shown only in the dedicated Candidates view.
   */
  visible: boolean;
}): JSX.Element | null {
  const dreamQ = useDream(dreamId);

  // Derive pending candidates from fetched statuses (not kb_candidate_count total).
  const pending = useMemo(() => {
    if (!dreamQ.data?.kb_candidates) return [];
    return dreamQ.data.kb_candidates.filter((c) => c.status === 'pending');
  }, [dreamQ.data?.kb_candidates]);

  // Report pending count for this dream back to the parent so the header tag
  // and the rail 'Candidates (N)' row track live statuses, not the stored
  // kb_candidate_count total. This runs regardless of `visible`: the row is
  // kept mounted in every non-search view precisely to keep the count live.
  useEffect(() => {
    onPendingCountChange?.(dreamId, pending.length);
    return () => onPendingCountChange?.(dreamId, 0);
  }, [dreamId, pending.length, onPendingCountChange]);

  if (!visible || dreamQ.isLoading || pending.length === 0) return null;

  return (
    <>
      {pending.map((c) => (
        <li key={`cand-${c.id}`}>
          <button
            type="button"
            onClick={() => onSelect(c)}
            className={cn(
              'kb-cand w-full text-left p-3 border-b border-border-default',
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
  // Dedicated Candidates view: when true (and not searching) the main area
  // shows dream-proposed candidate cards ONLY; the default/folder views show
  // live KB documents ONLY. Candidates never interleave with the feed again.
  const [candidatesView, setCandidatesView] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [detailCandidate, setDetailCandidate] = useState<DreamKbCandidate | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const { density } = useDensity();
  const queryClient = useQueryClient();
  const routes = useKbRoutes();

  // 200ms debounce — coalesces rapid keystrokes into a single /kb/search request.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(searchInput.trim()), 200);
    return () => clearTimeout(id);
  }, [searchInput]);

  const isSearching = debouncedQ.length > 0;

  // Fetch live KB entries, filtered by type (folder)
  const listQuery = useKBList(folder ? { type: folder } : undefined);
  // Unfiltered list backing the folder rail's counts (KB-01). Kept separate
  // from the feed query so per-folder counts and the "All entries" total stay
  // stable regardless of the active folder/search. Same route, no params —
  // when no folder is selected this dedupes with listQuery's cache.
  const railListQuery = useKBList();
  const searchQuery = useKBSearch(debouncedQ);
  const statsQuery = useKBStats();

  // Build a slug→view_count map from the stats endpoint.
  // When stats are still loading, statsQuery.data is undefined → map stays empty.
  // Once loaded, entries without a recorded view are absent from the map and
  // get `viewCount=0` (so the label always renders once stats are available).
  const statsLoaded = !!statsQuery.data;
  const viewCountBySlug = useMemo(() => {
    const map = new Map<string, number>();
    if (statsQuery.data?.entries) {
      for (const s of statsQuery.data.entries) {
        map.set(s.slug, s.view_count);
      }
    }
    return map;
  }, [statsQuery.data?.entries]);

  const rawEntries = useMemo(
    () =>
      isSearching
        ? (searchQuery.data?.entries ?? [])
        : (listQuery.data?.entries ?? []),
    [isSearching, searchQuery.data?.entries, listQuery.data?.entries],
  );

  // When searching, /kb/search returns matches across ALL types — apply
  // the selected folder/type client-side so the active pill stays honored.
  const liveEntries = useMemo(() => {
    if (isSearching && folder) {
      return rawEntries.filter((e) => e.type === folder);
    }
    return rawEntries;
  }, [rawEntries, folder, isSearching]);

  // Fetch dreams to find those with candidates
  const dreamsQuery = useDreamsList({ limit: 50 });
  const dreamsWithCandidates = useMemo(() => {
    if (!dreamsQuery.data?.dreams) return [];
    return dreamsQuery.data.dreams.filter((d) => d.kb_candidate_count > 0);
  }, [dreamsQuery.data?.dreams]);

  // Pending candidate count derived from the SAME per-dream candidate
  // statuses the feed already fetches (status === 'pending'), NOT from the
  // stored kb_candidate_count total which never decrements after Accept/Dismiss.
  const pendingCountsRef = useRef<Map<string, number>>(new Map());
  const [candidatePendingCount, setCandidatePendingCount] = useState(0);

  const handlePendingCountChange = useCallback(
    (dreamId: string, count: number) => {
      pendingCountsRef.current.set(dreamId, count);
      const total = Array.from(pendingCountsRef.current.values()).reduce(
        (sum, c) => sum + c,
        0,
      );
      setCandidatePendingCount(total);
    },
    [],
  );

  // Derive the grouped folder rail (KB-01) from the unfiltered library:
  // one folder per existing `type` value, each with its live count, plus the
  // total backing the "All entries" row. Counts come from railListQuery so
  // they stay stable when a folder filter narrows the feed.
  const railEntries = useMemo(
    () => railListQuery.data?.entries ?? [],
    [railListQuery.data?.entries],
  );
  const folderCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of railEntries) {
      counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
    }
    return counts;
  }, [railEntries]);
  const railFolders = useMemo(
    () => Array.from(folderCounts.keys()).sort(),
    [folderCounts],
  );

  const loading = listQuery.isLoading || dreamsQuery.isLoading;

  // Handle candidate select
  const handleCandidateSelect = (candidate: DreamKbCandidate) => {
    setDetailCandidate((prev) =>
      prev?.id === candidate.id ? null : candidate,
    );
  };

  // Called after a candidate is resolved (Accept or Dismiss).
  // On promoted → navigate to the promoted KB slug as a live entry.
  // On rejected → clear the candidate detail drawer.
  const handleCandidateResolved = (result: {
    status: string;
    promotedKbSlug: string | null;
  }) => {
    // Invalidate queries so the feed + pending-count update
    queryClient.invalidateQueries({ queryKey: ['kb-list'] });
    queryClient.invalidateQueries({ queryKey: ['kb-search'] });
    queryClient.invalidateQueries({ queryKey: ['dreams-list'] });
    queryClient.invalidateQueries({ queryKey: ['dream'] });

    if (result.status === 'promoted' && result.promotedKbSlug) {
      setDetailCandidate(null);
      navigate(routes.detail(result.promotedKbSlug));
    } else {
      setDetailCandidate(null);
    }
  };

  const detailOpen = !!openSlug || !!detailCandidate;

  // The Candidates view is only active when NOT searching — a search always
  // shows document matches (candidates stay hidden while searching, unchanged).
  const showCandidatesView = candidatesView && !isSearching;

  // The document feed shows live entries ONLY, so emptiness is decided from the
  // live entries alone — candidates no longer live here and never suppress the
  // "No entries yet" / "No matches" empty state.
  const isListEmpty = liveEntries.length === 0;

  return (
    <div className="flex h-full">
      {/* Folder rail */}
      <aside
        aria-label="KB folders"
        className="w-rail shrink-0 overflow-y-auto border-r border-border-default bg-surface-sunken p-3"
      >
        <div className="mb-3">
          <Input
            aria-label="Search KB entries"
            placeholder={KB_STRINGS.searchPlaceholder}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <GroupedFolderRail
          folders={railFolders}
          counts={folderCounts}
          total={railEntries.length}
          selected={folder}
          onSelect={(type) => {
            setCandidatesView(false);
            setFolder(type);
          }}
          candidatesCount={candidatePendingCount}
          candidatesActive={showCandidatesView}
          onSelectCandidates={() => setCandidatesView(true)}
        />
      </aside>

      {/* Main feed area — THR-099: cap the main content region at max-w-content
          (1180) while the folder rail stays functional and full-height. Mirrors
          Slice 3a/3b: the pinned header inner and the scroll-body inner both use
          <ContentWrap> so the header eyebrow/title column aligns above the feed
          columns at 1180 with matching 26px gutters. main is the flex sizer;
          the body <ContentWrap> owns the internal scroll. */}
      <main className="flex flex-1 flex-col overflow-hidden bg-surface-canvas">
        {/* Header — KB-02: uppercase eyebrow (live document count) + Newsreader
            serif title, matching the a-knowledge Direction-A reference and the
            Tasks/Audit surfaces. The amber pill surfaces pending dream
            candidates from the same client-side count the feed derives. */}
        <header className="shrink-0 border-b border-border-subtle">
          <ContentWrap>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-text-muted text-xs font-medium uppercase tracking-wide">
                {KB_STRINGS.headerEyebrow(liveEntries.length)}
              </p>
              <h1 className="font-display text-display text-text-primary mt-1 font-medium">
                {KB_STRINGS.pageTitle}
              </h1>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {candidatePendingCount > 0 && (
                <button
                  type="button"
                  onClick={() => setCandidatesView(true)}
                  className="text-xs font-medium text-feedback-warning bg-feedback-warning/10 px-2.5 py-1 rounded-full hover:bg-feedback-warning/20 transition-colors"
                >
                  {KB_STRINGS.pendingCandidatesTag(candidatePendingCount)}
                </button>
              )}
              {COMPOSE_ENABLED && (
                <Button size="sm" onClick={() => setComposeOpen(true)}>
                  {KB_STRINGS.composeButton}
                </Button>
              )}
            </div>
          </div>
          </ContentWrap>
        </header>

        {/* Content — capped + scrolled inside <ContentWrap>. The min-h-0 flex-1
            sizer gives the wrap a definite height so the feed scrolls internally
            and the header stays pinned; per-branch p-4 gutters are dropped since
            the wrap now owns the 26px padding (avoids doubling to 42px). */}
        <div className="min-h-0 flex-1">
          <ContentWrap>
        {loading ? (
          <LoadingSkeleton />
        ) : (isSearching ? searchQuery.isError : listQuery.isError) ? (
          <div className="text-center space-y-3">
            <p className="text-feedback-danger text-sm">
              Could not load Knowledge
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ['kb-list'] });
                queryClient.invalidateQueries({ queryKey: ['kb-search'] });
                queryClient.invalidateQueries({ queryKey: ['dreams-list'] });
              }}
            >
              {KB_STRINGS.retry}
            </Button>
          </div>
        ) : (
          <>
            {/* Candidate cards + live pending-count probes. Mounted whenever
                not searching so the header pill and the rail 'Candidates (N)'
                row track live per-dream pending status; the cards themselves
                render ONLY in the dedicated Candidates view (visible=…). In the
                default/folder feed the rows return null → the list is empty and
                collapses (no padding), so candidates never touch the feed. */}
            {!isSearching && dreamsWithCandidates.length > 0 && (
              <ul aria-label="Candidate entries" className="space-y-2">
                {dreamsWithCandidates.map((d) => (
                  <DreamCandidateRow
                    key={d.dream_id}
                    dreamId={d.dream_id}
                    visible={showCandidatesView}
                    onSelect={handleCandidateSelect}
                    detailCandidate={detailCandidate}
                    onPendingCountChange={handlePendingCountChange}
                  />
                ))}
              </ul>
            )}

            {showCandidatesView ? (
              candidatePendingCount === 0 ? (
                <EmptyState
                  title={KB_STRINGS.emptyCandidatesTitle}
                  body={KB_STRINGS.emptyCandidatesBody}
                />
              ) : null
            ) : isListEmpty ? (
              isSearching ? (
                <EmptyState
                  title={KB_STRINGS.emptySearchTitle}
                  body={KB_STRINGS.emptySearchBody}
                />
              ) : (
                <EmptyState
                  title={KB_STRINGS.emptyListTitle}
                  body={KB_STRINGS.emptyListBody}
                />
              )
            ) : (
              <ul className="space-y-2">
                {/* Live KB documents only — candidates never render here. */}
                {liveEntries.map((entry) => (
                  <li key={entry.slug}>
                    <KbEntryCard
                      entry={entry}
                      to={routes.detail(entry.slug)}
                      active={openSlug === entry.slug}
                      density={density}
                      viewCount={
                        statsLoaded
                          ? (viewCountBySlug.get(entry.slug) ?? 0)
                          : undefined
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
          </ContentWrap>
        </div>
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
          onCandidateResolved={handleCandidateResolved}
        />
      )}
      {composeOpen && (
        <ComposeKbEntryDialog onClose={() => setComposeOpen(false)} />
      )}
    </div>
  );
}

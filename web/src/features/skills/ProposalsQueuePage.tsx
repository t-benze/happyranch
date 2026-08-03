/**
 * ProposalsQueuePage — Founder Proposal Queue (THR-055 Slice 3A).
 *
 * READ-ONLY queue backed by the server-authoritative
 * GET /skill-lifecycle/proposals endpoint. Filter/query changes issue a
 * server query; total/count/ordering come from the response — never
 * re-sorted, re-counted, or client-side-filtered.
 *
 * Only the documented server-supported query params are rendered:
 * status, validation_outcome, search, proposer, submitted_after,
 * submitted_before, page, page_size. No "Any assignment" or "Any use case"
 * selectors (these are explicitly deferred per the visual-contract delta
 * documented in the THR-055 spec).
 *
 * No action buttons, no lifecycle mutation, no local mutation. Rows
 * deep-link to /orgs/:slug/skills/proposals/:versionId.
 */
import { useState, useEffect } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Filter,
  Info,
  Search,
  Shield,
  X,
} from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useProposalsQueue, type ProposalQueueItem } from '@/hooks/skills';

/** Supported server-authoritative filter keys — no other params are sent. */
const SUPPORTED_PARAMS = [
  'status',
  'validation_outcome',
  'search',
  'proposer',
  'submitted_after',
  'submitted_before',
  'page',
  'page_size',
] as const;

const DEFAULT_PAGE_SIZE = 20;

/**
 * Read URL search params and return only the server-supported keys.
 * Never forwards unsupported params to the API.
 *
 * page_size is forwarded when provided and validated as a positive
 * integer; otherwise the default (20) is sent. The server response's
 * `page` and `page_size` are authoritative for all pagination labels.
 */
function useQueueParams() {
  const [searchParams] = useSearchParams();
  const page = Number(searchParams.get('page')) || 1;
  let pageSize = Number(searchParams.get('page_size'));
  if (
    !Number.isFinite(pageSize) ||
    !Number.isInteger(pageSize) ||
    !Number.isSafeInteger(pageSize) ||
    pageSize < 1
  )
    pageSize = DEFAULT_PAGE_SIZE;

  const params: Record<string, string | number | undefined> = {
    page,
    page_size: pageSize,
  };
  for (const key of SUPPORTED_PARAMS) {
    const val = searchParams.get(key);
    if (val && key !== 'page' && key !== 'page_size') {
      params[key] = val;
    }
  }
  return params;
}

/**
 * Derive human-readable active-filter badges from the URL params.
 * Only shows non-default, non-pagination params. Returns an array for
 * rendering; onRemove is a stable callback that calls setSearchParams.
 * Must be inlined in the component body to bind the hook's setter.
 */
function buildActiveFilters(
  searchParams: URLSearchParams,
  setSearchParams: (next: URLSearchParams, opts?: { replace?: boolean }) => void,
): { label: string; onRemove: () => void }[] {
  const out: { label: string; onRemove: () => void }[] = [];
  for (const key of SUPPORTED_PARAMS as unknown as string[]) {
    if (key === 'page' || key === 'page_size') continue;
    const val = searchParams.get(key);
    if (val) {
      out.push({
        label: `${key.replace(/_/g, ' ')}: ${val}`,
        onRemove: () => {
          const next = new URLSearchParams(searchParams);
          next.delete(key);
          next.delete('page');
          setSearchParams(next, { replace: true });
        },
      });
    }
  }
  return out;
}

/** Status label for a proposal row — maps lifecycle status to display text. */
function statusLabel(status: string): { text: string; variant: 'default' | 'info' | 'success' | 'warning' | 'danger' } {
  switch (status) {
    case 'proposed': return { text: 'Proposed', variant: 'info' };
    case 'draft': return { text: 'Draft', variant: 'info' };
    case 'validated': return { text: 'Validated', variant: 'success' };
    case 'in_review': return { text: 'In Review', variant: 'warning' };
    case 'approved': return { text: 'Approved', variant: 'success' };
    case 'published': return { text: 'Published', variant: 'success' };
    case 'rejected': return { text: 'Rejected', variant: 'danger' };
    default: return { text: status, variant: 'default' };
  }
}

function StatusBadge({
  status,
}: {
  status: Pick<ProposalQueueItem, 'status'>['status'];
}): JSX.Element {
  const { text, variant } = statusLabel(status);
  const cls = {
    default: 'bg-bg-subtle text-fg-muted border-border-default',
    info: 'bg-info-soft text-info border-info/20',
    success: 'bg-success-soft text-success border-success/20',
    warning: 'bg-warning-soft text-warning border-warning/20',
    danger: 'bg-danger-soft text-danger border-danger/20',
  }[variant];
  return (
    <span
      className={`text-2xs inline-flex items-center rounded-full border px-2 py-0.5 font-bold tracking-wide uppercase ${cls}`}
    >
      {text}
    </span>
  );
}

// ── Quick-filter chips (server-supported status values) ──────────────────

const STATUS_FILTERS = [
  { value: '', label: 'All' },
  { value: 'proposed', label: 'Proposed' },
  { value: 'draft', label: 'Draft' },
  { value: 'validated', label: 'Validated' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'published', label: 'Published' },
  { value: 'rejected', label: 'Rejected' },
];

// ── Page component ────────────────────────────────────────────────────────

export function ProposalsQueuePage(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const queueParams = useQueueParams();
  const query = useProposalsQueue(queueParams);

  // Local state for the search input (sync to URL on submit)
  const [searchInput, setSearchInput] = useState(searchParams.get('search') ?? '');

  // Sync searchInput when URL search param changes externally
  useEffect(() => {
    setSearchInput(searchParams.get('search') ?? '');
  }, [searchParams]);

  const page = query.data?.page ?? (Number(searchParams.get('page')) || 1);
  const pageSize = query.data?.page_size ?? DEFAULT_PAGE_SIZE;
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const filters = buildActiveFilters(searchParams, setSearchParams);
  const activeStatus = searchParams.get('status') ?? '';

  function applyFilter(key: string, value: string): void {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.delete('page');
    setSearchParams(next, { replace: true });
  }

  function handleSearch(): void {
    const next = new URLSearchParams(searchParams);
    if (searchInput.trim()) {
      next.set('search', searchInput.trim());
    } else {
      next.delete('search');
    }
    next.delete('page');
    setSearchParams(next, { replace: true });
  }

  function clearAllFilters(): void {
    setSearchParams({}, { replace: true });
    setSearchInput('');
  }

  const items = query.data?.items ?? [];

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-6xl flex-col overflow-hidden px-4 py-5 md:px-7 md:py-6">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-fg-subtle text-overline mb-1 tracking-wider uppercase">
            Skills · Proposals
          </div>
          <h2 className="text-h2 text-fg">Proposal Queue</h2>
          <p className="text-fg-muted text-body-sm mt-1">
            Review and manage skill proposals submitted by agents.
          </p>
        </div>
      </div>

      {/* Founder-only guidance panel */}
      <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mb-5 flex items-start gap-2.5 rounded-md border px-3 py-2.5">
        <Shield size={15} aria-hidden="true" className="text-fg-subtle mt-0.5 shrink-0" />
        <span>
          <b className="text-fg font-semibold">Founder-only.</b>{' '}
          This queue shows all agent-submitted proposals in read-only form.
          Lifecycle action UI (claim, validate, review, publish, assign,
          rollback) is deferred to a future slice. Proposals are immutable —
          a new version requires a fresh submission.
        </span>
      </div>

      {/* Filter bar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* Status quick-filter chips */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Filter size={14} aria-hidden="true" className="text-fg-subtle shrink-0" />
          {STATUS_FILTERS.map((f) => {
            const on = f.value === activeStatus || (!f.value && !activeStatus);
            return (
              <button
                key={f.value || '__all'}
                type="button"
                aria-pressed={on}
                onClick={() => applyFilter('status', f.value)}
                className={`text-body-sm rounded-full border px-2.5 py-1 font-semibold transition-colors ${
                  on
                    ? 'bg-accent-soft text-accent-text border-transparent'
                    : 'border-border-default text-fg-muted bg-surface-raised hover:bg-bg-subtle'
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        {/* Search input */}
        <div className="ml-auto flex items-center gap-1.5">
          <div className="border-border-default bg-surface-raised flex items-center rounded-md border px-2 py-1">
            <Search size={14} aria-hidden="true" className="text-fg-subtle shrink-0" />
            <input
              type="text"
              aria-label="Search proposals"
              placeholder="Search…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSearch();
              }}
              className="text-body-sm text-fg placeholder:text-fg-muted w-48 border-none bg-transparent px-1.5 py-1 outline-none"
            />
            {searchInput && (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => {
                  setSearchInput('');
                  const next = new URLSearchParams(searchParams);
                  next.delete('search');
                  next.delete('page');
                  setSearchParams(next, { replace: true });
                }}
                className="text-fg-muted hover:text-fg rounded p-0.5"
              >
                <X size={12} />
              </button>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleSearch}
            className="text-body-sm"
          >
            Search
          </Button>
        </div>
      </div>

      {/* Active filter badges */}
      {filters.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {filters.map((f) => (
            <span
              key={f.label}
              className="bg-accent-soft text-accent-text inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
            >
              {f.label}
              <button
                type="button"
                aria-label={`Remove filter ${f.label}`}
                onClick={f.onRemove}
                className="hover:bg-accent/20 rounded-full p-0.5"
              >
                <X size={11} />
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={clearAllFilters}
            className="text-fg-muted hover:text-fg text-xs font-semibold"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Loading state */}
      {query.isLoading && (
        <div className="flex flex-col gap-3" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="border-border-subtle bg-surface-subtle h-16 animate-pulse rounded-md border"
            />
          ))}
        </div>
      )}

      {/* Error state with retry */}
      {query.isError && (
        <EmptyState
          icon={<AlertTriangle size={28} />}
          title="Could not load proposals"
          body="The proposal queue is unavailable right now."
          cta={{
            label: 'Retry',
            onClick: () => {
              (query as unknown as { refetch: () => void }).refetch();
            },
          }}
        />
      )}

      {/* Empty state (no error, not loading) */}
      {!query.isLoading && !query.isError && items.length === 0 && (
        <EmptyState
          icon={<Info size={28} />}
          title="No proposals yet"
          body={
            filters.length > 0
              ? 'No proposals match your current filters. Try adjusting or clearing them.'
              : 'No agent-submitted proposals have been received yet. Use `happyranch skills propose` from an agent session to submit one.'
          }
        />
      )}

      {/* Table/list of proposals */}
      {!query.isLoading && !query.isError && items.length > 0 && (
        <>
          {/* Header row + count */}
          <div className="text-fg-subtle text-body-sm mb-2 flex items-center gap-2">
            <span className="font-semibold">
              {total} {total === 1 ? 'proposal' : 'proposals'}
            </span>
            <span className="text-xs">
              (page {page} of {totalPages}, {pageSize} per page)
            </span>
          </div>

          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <ProposalRow key={`${item.skill_id}-${item.version_id}`} item={item} />
            ))}
          </ul>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-3">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() =>
                  setSearchParams(
                    (prev) => {
                      const next = new URLSearchParams(prev);
                      next.set('page', String(page - 1));
                      next.set('page_size', String(pageSize));
                      return next;
                    },
                    { replace: true },
                  )
                }
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Previous
              </Button>
              <span className="text-fg-muted text-body-sm">
                Page {page} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() =>
                  setSearchParams(
                    (prev) => {
                      const next = new URLSearchParams(prev);
                      next.set('page', String(page + 1));
                      next.set('page_size', String(pageSize));
                      return next;
                    },
                    { replace: true },
                  )
                }
              >
                Next
                <ChevronRight size={14} aria-hidden="true" />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Proposal Row ──────────────────────────────────────────────────────────

function ProposalRow({ item }: { item: ProposalQueueItem }): JSX.Element {
  const { slug } = useParams<{ slug: string }>();

  return (
    <li>
      <Link
        to={`/orgs/${slug ?? ''}/skills/proposals/${item.version_id}`}
        className="border-border-default bg-surface-raised hover:bg-surface-hover focus-visible:ring-accent flex flex-col gap-2 rounded-md border p-3.5 transition-colors focus:outline-none focus-visible:ring-2 sm:flex-row sm:items-center sm:gap-4"
        aria-label={`View proposal ${item.name} version ${item.version}`}
      >
        {/* Left: status badge + name */}
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <span className="text-mono-xs text-fg-subtle">v{item.version}</span>
            {item.claimed_by && (
              <span className="text-fg-muted text-2xs">
                claimed by {item.claimed_by}
              </span>
            )}
          </div>
          <h3 className="text-fg font-mono text-sm leading-snug font-semibold">
            {item.name}
          </h3>
          {item.slug && (
            <p className="text-fg-muted text-body-sm mt-0.5 line-clamp-1 max-w-2xl">
              {item.slug}
            </p>
          )}
        </div>

        {/* Right: proposer, validation, timestamp */}
        <div className="text-fg-subtle flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {item.proposer_agent && (
            <span>
              by{' '}
              <span className="text-fg-muted font-semibold">
                {item.proposer_agent}
              </span>
            </span>
          )}
          {item.latest_validator_version && (
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" />
              {item.latest_validator_version}
              {item.latest_validator_key && (
                <span className="text-fg-subtle">({item.latest_validator_key})</span>
              )}
            </span>
          )}
          {item.created_at && (
            <time
              dateTime={item.created_at}
              className="text-fg-subtle hidden sm:inline"
            >
              {new Date(item.created_at).toLocaleDateString()}
            </time>
          )}
        </div>
      </Link>
    </li>
  );
}

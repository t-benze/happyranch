/**
 * Audit feature shell. Renders FilterSidebar + SubTabBar; the active
 * tab is mounted via React Router's <Outlet />.
 *
 * Filter state lives in the URL search params via `audit-filters.ts`.
 */
import { useMemo } from 'react';
import { Outlet, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { SubTabBar } from '@/design-system/primitives/SubTabBar';
import {
  decodeFilters,
  encodeFilters,
  type AuditFilters,
} from './audit-filters';

const SINCE_OPTIONS: FilterGroup['options'] = [
  { value: '24h', label: 'Today' },
  { value: '7d', label: 'This week' },
  { value: 'all', label: 'All time' },
];

// Founder-facing audit action vocabulary. Not exhaustive — the Activity tab
// will render every action the daemon returns; this list controls the
// quick-pick chips on the sidebar.
const ACTIONS = [
  'completion_report',
  'review_verdict',
  'escalation',
  'escalation_resolved',
  'session_start',
  'session_end',
  'task_cancelled',
];

function useActiveTab(slug: string | undefined): {
  active: string;
  base: string;
} {
  const location = useLocation();
  const base = `/orgs/${slug ?? ''}/audit`;
  if (location.pathname.startsWith(`${base}/escalations`)) {
    return { active: 'escalations', base };
  }
  if (location.pathname.startsWith(`${base}/traces`)) {
    return { active: 'traces', base };
  }
  return { active: 'activity', base };
}

export function AuditPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);
  const { active, base } = useActiveTab(slug);

  const groups: FilterGroup[] = [];
  // Agent filter is set via deep-links today; v1 leaves the chip group empty
  // (the sidebar still renders the "All" reset and the active-filter banner
  // above the tab bar makes the chosen agent obvious).
  if (active === 'activity') {
    groups.push({
      key: 'action',
      label: 'Type',
      options: ACTIONS.map((a) => ({ value: a, label: a })),
    });
  }
  groups.push({
    key: 'since',
    label: 'Date',
    options: SINCE_OPTIONS,
  });

  const sidebarValue: Record<string, string | null> = {
    action: filters.action,
    since: filters.since,
  };

  const onSidebarChange = (next: Record<string, string | null>) => {
    const merged: AuditFilters = {
      ...filters,
      action: next.action ?? null,
      since: (next.since as AuditFilters['since']) ?? null,
    };
    setSearchParams(encodeFilters(merged), { replace: true });
  };

  const search = encodeFilters(filters);
  const suffix = search ? `?${search}` : '';

  // Traces' canonical URL carries the selected task as a path segment, not a
  // query param. When the founder is on Activity with a `?task_id=` deep link
  // and clicks the Traces tab, promote that param into the path so the URL
  // (and the back button) match the picker-click form.
  const tracesTo = (() => {
    if (!filters.task_id) return base + '/traces' + suffix;
    const withoutTaskId = encodeFilters({ ...filters, task_id: null });
    const tail = withoutTaskId ? `?${withoutTaskId}` : '';
    return `${base}/traces/${filters.task_id}${tail}`;
  })();

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={sidebarValue} onChange={onSidebarChange} />
      <div className="flex flex-1 flex-col">
        {filters.agent || filters.task_id ? (
          <ActiveFilterBanner
            filters={filters}
            onClear={(key) => {
              const next: AuditFilters = { ...filters, [key]: null };
              setSearchParams(encodeFilters(next), { replace: true });
            }}
          />
        ) : null}
        <SubTabBar
          tabs={[
            { value: 'activity', label: 'Activity', to: base + suffix },
            { value: 'escalations', label: 'Escalations', to: base + '/escalations' + suffix },
            { value: 'traces', label: 'Traces', to: tracesTo },
          ]}
          active={active}
        />
        <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ActiveFilterBanner({
  filters,
  onClear,
}: {
  filters: AuditFilters;
  onClear: (key: 'agent' | 'task_id') => void;
}): JSX.Element {
  return (
    <div className="bg-surface-sunken border-border-subtle flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
      <span className="text-fg-muted">Filters:</span>
      {filters.agent && (
        <FilterChip label={`agent: ${filters.agent}`} onClear={() => onClear('agent')} />
      )}
      {filters.task_id && (
        <FilterChip label={`task: ${filters.task_id}`} onClear={() => onClear('task_id')} />
      )}
    </div>
  );
}

function FilterChip({ label, onClear }: { label: string; onClear: () => void }): JSX.Element {
  return (
    <span className="bg-accent-muted text-fg inline-flex items-center gap-1 rounded px-2 py-0.5">
      {label}
      <button
        type="button"
        aria-label={`Clear ${label}`}
        className="text-fg-muted hover:text-fg"
        onClick={onClear}
      >
        ✕
      </button>
    </span>
  );
}

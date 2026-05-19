import { useDeferredValue, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Input } from '@/design-system/primitives/Input';
import { Button } from '@/design-system/primitives/Button';
import { useDensity } from '@/hooks/density';
import { useKBList, useKBSearch, useKbRoutes } from '@/hooks/kb';
import { KbEntryCard } from './KbEntryCard';
import { KbEntryDetailPane } from './KbEntryDetailPane';
import { ComposeKbEntryDialog } from './ComposeKbEntryDialog';
import { KB_STRINGS } from './strings';

const COMPOSE_ENABLED = import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true';

export function KbPage(): JSX.Element {
  // KB slugs contain forward slashes (e.g. `policy/refund-thresholds`), so the
  // detail child route uses a splat (`kb/*`) and we read the matched tail here.
  const params = useParams<{ '*'?: string }>();
  const openSlug = params['*'] && params['*'].length > 0 ? params['*'] : undefined;
  const [filters, setFilters] = useState<Record<string, string | null>>({
    type: null,
    tag: null,
  });
  const [searchInput, setSearchInput] = useState('');
  const [composeOpen, setComposeOpen] = useState(false);
  const deferredQ = useDeferredValue(searchInput.trim());
  const { density } = useDensity();
  const routes = useKbRoutes();

  const listQuery = useKBList(filters.type ? { type: filters.type } : undefined);
  const searchQuery = useKBSearch(deferredQ);
  const isSearching = deferredQ.length > 0;

  // Memoize so the `?? []` fallback (a fresh array literal each render) doesn't
  // re-trigger the three useMemos below on every parent re-render.
  const rawEntries = useMemo(
    () =>
      isSearching
        ? (searchQuery.data?.entries ?? [])
        : (listQuery.data?.entries ?? []),
    [isSearching, searchQuery.data?.entries, listQuery.data?.entries],
  );

  const entries = useMemo(() => {
    const tag = filters.tag;
    return tag ? rawEntries.filter((e) => e.tags.includes(tag)) : rawEntries;
  }, [rawEntries, filters.tag]);

  // Sidebar option lists derive from the server-returned set (rawEntries),
  // BEFORE the client-side tag filter — so toggling a tag does not collapse
  // the Tag rail. Same shape as TasksPage's team filter.
  const types = useMemo(() => {
    const set = new Set<string>();
    rawEntries.forEach((e) => set.add(e.type));
    return [...set].sort();
  }, [rawEntries]);
  const tags = useMemo(() => {
    const set = new Set<string>();
    rawEntries.forEach((e) => e.tags.forEach((t) => set.add(t)));
    return [...set].sort();
  }, [rawEntries]);

  const groups: FilterGroup[] = [
    {
      key: 'type',
      label: KB_STRINGS.filterTypes,
      options: types.map((t) => ({ value: t, label: t })),
    },
    {
      key: 'tag',
      label: KB_STRINGS.filterTags,
      options: tags.map((t) => ({ value: t, label: t })),
    },
  ];

  const loading = isSearching ? searchQuery.isLoading : listQuery.isLoading;

  return (
    <div className="flex h-full">
      <aside aria-label="KB filters" className="border-border-subtle bg-surface-sunken w-60 shrink-0 overflow-y-auto border-r p-3">
        <div className="mb-3">
          <Input
            aria-label="Search KB entries"
            placeholder={KB_STRINGS.searchPlaceholder}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      </aside>
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="text-fg text-lg font-semibold">{KB_STRINGS.pageTitle}</h1>
          {COMPOSE_ENABLED && (
            <Button size="sm" onClick={() => setComposeOpen(true)}>
              {KB_STRINGS.composeButton}
            </Button>
          )}
        </div>
        {loading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : entries.length === 0 ? (
          <EmptyState
            title={isSearching ? KB_STRINGS.emptySearchTitle : KB_STRINGS.emptyListTitle}
            body={isSearching ? KB_STRINGS.emptySearchBody : KB_STRINGS.emptyListBody}
          />
        ) : (
          <ul className="space-y-2">
            {entries.map((e) => (
              <li key={e.slug}>
                <KbEntryCard
                  entry={e}
                  to={routes.detail(e.slug)}
                  active={openSlug === e.slug}
                  density={density}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openSlug && <KbEntryDetailPane entrySlug={openSlug} />}
      {composeOpen && <ComposeKbEntryDialog onClose={() => setComposeOpen(false)} />}
    </div>
  );
}

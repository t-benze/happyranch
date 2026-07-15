/**
 * SkillsPage — Skills Catalog (THR-092 Slice 1 of 6).
 *
 * Operator-facing catalog of the skills their agents can be shown as
 * guidance. Skills are guidance visibility only — they never grant tools,
 * commands, or permissions, and the catalog carries NO permission / approve /
 * admit / materialize-now controls. System-contract skills render read-only.
 *
 * Filters are Bundled / Custom ONLY, mapped to the daemon `?filter=` param
 * via `useSkillsCatalog`. Validation state is a per-skill label, never a
 * catalog filter (product_lead handoff §1).
 *
 * Responsive: the source rail collapses to Bundled/Custom chips below `md`
 * so the skill list stays on-canvas at mobile widths (handoff §9). The global
 * AppShell nav is desktop-scoped and reworking its mobile collapse is a
 * separate shell-level task (see MEM-004) — out of this slice's scope.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Activity, Info, Package, Plus, Sparkles, TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useSkillsCatalog } from '@/hooks/skills';
import { SkillCard } from './SkillCard';
import { needsAttentionCount, type CatalogFilter } from './skills-catalog';

// Bundled and Custom are the ONLY filter controls (product_lead handoff §1).
// `'all'` stays as the internal default sentinel — the UNSELECTED state — and
// is never surfaced as a facet button; it maps to no `?filter=` param.
const FACETS: { value: CatalogFilter; label: string; icon: typeof Package }[] = [
  { value: 'Bundled', label: 'Bundled', icon: Package },
  { value: 'Custom', label: 'Custom', icon: Sparkles },
];

export function SkillsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [filter, setFilter] = useState<CatalogFilter>('all');
  const query = useSkillsCatalog(filter === 'all' ? undefined : { filter });
  const items = query.data?.items ?? [];
  // No facet matches the unfiltered default → header reads "All skills".
  const headingLabel =
    FACETS.find((f) => f.value === filter)?.label ?? 'All skills';
  const attention = needsAttentionCount(items);

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col md:flex-row">
      {/* Source rail — desktop only */}
      <aside className="border-border-default hidden shrink-0 overflow-y-auto border-r p-4 md:block md:w-56">
        <div className="text-fg-subtle text-overline mb-3 px-2.5 tracking-wider uppercase">
          Source
        </div>
        <nav className="flex flex-col gap-0.5" aria-label="Skill source filter">
          {FACETS.map((f) => {
            const on = f.value === filter;
            const Icon = f.icon;
            return (
              <button
                key={f.value}
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(on ? 'all' : f.value)}
                className={`text-body-sm flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left font-medium transition-colors ${
                  on
                    ? 'bg-accent-soft text-accent-text'
                    : 'text-fg-muted hover:bg-bg-subtle hover:text-fg'
                }`}
              >
                <Icon size={15} aria-hidden="true" className="shrink-0 opacity-85" />
                {f.label}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Main column */}
      <div className="min-w-0 overflow-y-auto px-4 py-5 md:flex-1 md:px-7 md:py-6">
        {/* Mobile filter chips — replace the rail below md */}
        <div className="mb-4 flex flex-wrap gap-2 md:hidden" aria-label="Skill source filter">
          {FACETS.map((f) => {
            const on = f.value === filter;
            return (
              <button
                key={f.value}
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(on ? 'all' : f.value)}
                className={`text-body-sm rounded-full border px-3 py-1.5 font-semibold transition-colors ${
                  on
                    ? 'bg-accent-soft text-accent-text border-transparent'
                    : 'border-border-default text-fg-muted bg-surface-raised'
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-fg-subtle text-overline mb-1 tracking-wider uppercase">
              {headingLabel} · {items.length}
            </div>
            <h2 className="text-h2 text-fg">Guidance your agents can use</h2>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            {attention > 0 && (
              <span className="text-attention-text bg-attention-soft inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold">
                <TriangleAlert size={12} aria-hidden="true" />
                {attention} {attention === 1 ? 'needs' : 'need'} attention
              </span>
            )}
            {/* Runtime Validation entry point — mirrors the mockup's Skills
                topbar link (the read-only event list). One nav entry into the
                Slice-6 surface. */}
            <Link
              to={`/orgs/${slug ?? ''}/skills/validation`}
              className="border-border-default text-fg-muted hover:bg-bg-subtle hover:text-fg text-body-sm inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
            >
              <Activity size={15} aria-hidden="true" />
              Runtime Validation
            </Link>
            <Link
              to={`/orgs/${slug ?? ''}/skills/new`}
              className="bg-accent-soft text-accent-text hover:bg-accent-soft/80 text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold"
            >
              <Plus size={15} aria-hidden="true" />
              Add custom skill
            </Link>
          </div>
        </div>

        {/* Guidance-only global warning */}
        <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mb-5 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
          <Info size={15} aria-hidden="true" className="text-fg-subtle shrink-0" />
          <span>
            <b className="text-fg font-semibold">Guidance visibility only.</b>{' '}
            Skills shape what an agent is told — they never grant tools,
            commands, or permissions.
          </span>
        </div>

        {query.isLoading ? (
          <ul className="flex flex-col gap-3" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <li
                key={i}
                className="border-border-subtle bg-surface-subtle h-24 animate-pulse rounded-md border"
              />
            ))}
          </ul>
        ) : query.isError ? (
          <EmptyState
            icon={<TriangleAlert size={28} />}
            title="Could not load skills"
            body="The skills catalog is unavailable right now. Try again shortly."
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Package size={28} />}
            title="No skills here yet"
            body={
              filter === 'Custom'
                ? 'No custom skills yet. Custom skills you add will appear here.'
                : 'No skills match this source.'
            }
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <li key={item.skill_id}>
                <Link
                  to={`/orgs/${slug ?? ''}/skills/${item.skill_id}`}
                  className="focus-visible:ring-accent block rounded-md focus:outline-none focus-visible:ring-2"
                  aria-label={`View ${item.name}`}
                >
                  <SkillCard item={item} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

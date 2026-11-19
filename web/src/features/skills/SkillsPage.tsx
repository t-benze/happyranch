/**
 * SkillsPage — Skills Catalog (THR-092 Slice 1 of 6).
 *
 * Operator-facing catalog of the skills their agents can be shown as
 * guidance. Skills are guidance visibility only — they never grant tools,
 * commands, or permissions, and the catalog carries NO permission / approve /
 * admit / materialize-now controls. System-contract skills render read-only.
 *
 * The general/Bundled view uses the unfiltered general catalog through
 * `useSkillsCatalog()`. Custom exclusively uses the canonical B2 custom-skills
 * catalog through `useCustomSkillsCatalog()` and never legacy `?filter=Custom`.
 * Validation state is a per-skill label, never a catalog filter (product_lead handoff §1).
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
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { TopbarActions } from '@/design-system/layouts/AppShell/TopbarSlots';
import { Button } from '@/design-system/primitives/Button';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { isCustomSkillForbidden, useCustomSkillsCatalog } from '@/hooks/custom-skills';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { useSkillsCatalog } from '@/hooks/skills';
import { CustomSkillCard } from './CustomSkillCard';
import { SkillCard } from './SkillCard';
import { needsAttentionCount, type CatalogFilter } from './skills-catalog';
import { classifySkillError, renderSkillError } from './strings';

// Bundled and Custom are the ONLY filter controls (product_lead handoff §1).
// `'all'` stays as the internal default sentinel — the UNSELECTED state — and
// is never surfaced as a facet button; it maps to no `?filter=` param.
const FACETS: { value: CatalogFilter; labelKey: MessageKey; icon: typeof Package }[] = [
  { value: 'Bundled', labelKey: 'skills.catalog.facet.bundled', icon: Package },
  { value: 'Custom', labelKey: 'skills.catalog.facet.custom', icon: Sparkles },
];

export function SkillsPage(): JSX.Element {
  const { t } = useTranslation();
  const { slug } = useParams<{ slug: string }>();
  const [filter, setFilter] = useState<CatalogFilter>('all');
  const [removed, setRemoved] = useState(false);
  const customSelected = filter === 'Custom';
  const catalogQuery = useSkillsCatalog();
  const customQuery = useCustomSkillsCatalog(customSelected, removed ? 'removed' : 'current');
  const items = catalogQuery.data?.items ?? [];
  const customSkills = customQuery.data?.skills ?? [];
  // No facet matches the unfiltered default → header reads "All skills".
  const facet = FACETS.find((f) => f.value === filter);
  const headingLabel = t(facet ? facet.labelKey : 'skills.catalog.allSkills');
  const attention = needsAttentionCount(items);

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-6xl flex-col overflow-hidden md:flex-row">
      {/* Source rail — desktop only */}
      <aside className="border-border-default hidden shrink-0 overflow-y-auto border-r p-4 md:block md:w-56">
        <div className="text-text-muted text-overline mb-3 px-2.5 tracking-wider uppercase">
          {t('skills.catalog.source')}
        </div>
        <nav className="flex flex-col gap-0.5" aria-label={t('skills.catalog.sourceFilter')}>
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
                    : 'text-text-secondary hover:bg-surface-sunken hover:text-text-primary'
                }`}
              >
                <Icon size={15} aria-hidden="true" className="shrink-0 opacity-85" />
                {t(f.labelKey)}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Main column */}
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-12 md:py-8">
        {/* Mobile filter chips — replace the rail below md */}
        <div className="mb-4 flex flex-wrap gap-2 md:hidden" aria-label={t('skills.catalog.sourceFilter')}>
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
                    : 'border-border-default text-text-secondary bg-surface-raised'
                }`}
              >
                {t(f.labelKey)}
              </button>
            );
          })}
        </div>

        <PageTitle
          className="mb-4"
          eyebrow={t('skills.catalog.overline', {
            label: headingLabel,
            count: customSelected ? customSkills.length : items.length,
          })}
          title={t('skills.catalog.heading')}
          actions={
          <div className="flex flex-wrap items-center gap-2.5">
            {attention > 0 && (
              <span className="text-attention-text bg-attention-soft inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold">
                <TriangleAlert size={12} aria-hidden="true" />
                {t('skills.catalog.needsAttention', { count: attention })}
              </span>
            )}
            {/* Runtime Validation entry point — mirrors the mockup's Skills
                topbar link (the read-only event list). One nav entry into the
                Slice-6 surface. */}
            <Link
              to={`/orgs/${slug ?? ''}/skills/validation`}
              className="border-border-default text-text-secondary hover:bg-surface-sunken hover:text-text-primary text-body-sm inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
            >
              <Activity size={15} aria-hidden="true" />
              {t('skills.catalog.runtimeValidation')}
            </Link>
          </div>
          }
        />
        {/* THR-376 r2 A1b: "Add custom skill" is the page primary action — a
            solid primary btn-sm in the AppBar (review: not a soft chip). */}
        {(!customSelected || !removed) && (
          <TopbarActions>
            <Button asChild size="sm">
              <Link to={`/orgs/${slug ?? ''}/skills/custom/new`}>
                <Plus aria-hidden="true" />
                {t('skills.addCustom')}
              </Link>
            </Button>
          </TopbarActions>
        )}

        {/* Guidance-only global warning */}
        <div className="border-border-default bg-surface-sunken text-text-secondary text-body-sm mb-6 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
          <Info size={15} aria-hidden="true" className="text-text-muted shrink-0" />
          <span>
            <b className="text-text-primary font-semibold">{t('skills.guidanceOnly')}</b>{' '}
            {t('skills.catalog.guidanceBody')}
          </span>
        </div>

        {customSelected && (
          <SegmentedControl
            className="mb-4"
            aria-label={t('skills.view.label')}
            value={removed ? 'removed' : 'current'}
            onValueChange={(v) => setRemoved(v === 'removed')}
            options={[
              { value: 'current', label: t('skills.view.current') },
              { value: 'removed', label: t('skills.view.removed') },
            ]}
          />
        )}

        {customSelected && isCustomSkillForbidden(customQuery.error) ? (
          <EmptyState
            icon={<TriangleAlert size={28} />}
            title={t('skills.founderRequired.title')}
            body={t('skills.founderRequired.body')}
          />
        ) : (customSelected ? customQuery.isLoading : catalogQuery.isLoading) ? (
          <ul className="flex flex-col gap-3" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <li
                key={i}
                className="border-border-subtle bg-surface-subtle h-24 animate-pulse rounded-md border"
              />
            ))}
          </ul>
        ) : (customSelected ? customQuery.isError : catalogQuery.isError) ? (
          <EmptyState
            icon={<TriangleAlert size={28} />}
            title={t(customSelected ? 'skills.catalog.customErrorTitle' : 'skills.catalog.errorTitle')}
            body={renderSkillError(
              customSelected
                ? classifySkillError(customQuery.error, 'skills.catalog.customErrorBody')
                : classifySkillError(catalogQuery.error, 'skills.catalog.errorBody'),
              t,
            )}
          />
        ) : (customSelected ? customSkills.length : items.length) === 0 ? (
          <EmptyState
            icon={<Package size={28} />}
            title={t('skills.catalog.emptyTitle')}
            body={t(
              filter === 'Custom'
                ? removed ? 'skills.catalog.emptyRemoved' : 'skills.catalog.emptyCustom'
                : 'skills.catalog.emptySource',
            )}
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {customSelected ? customSkills.map((skill) => (
              <li key={skill.id ?? skill.skill_id}>
                <CustomSkillCard skill={skill} slug={slug ?? ''} />
              </li>
            )) : items.map((item) => (
              <li key={item.skill_id}>
                <Link
                  to={`/orgs/${slug ?? ''}/skills/${item.skill_id}`}
                  className="focus-visible:ring-accent-default block rounded-md focus:outline-none focus-visible:ring-2"
                  aria-label={t('skills.catalog.viewSkill', { name: item.name })}
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

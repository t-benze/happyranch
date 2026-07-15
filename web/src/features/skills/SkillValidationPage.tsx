/**
 * SkillValidationPage — Runtime Validation event list (THR-092 Slice 6 of 6).
 *
 * A read-only record of what happened when skills were technically checked or
 * prepared for a session: validation passes/failures, materialization issues,
 * and context-applied contract-predicate errors. The surface TITLE comes from
 * the endpoint's `label` field ("Runtime Validation") — it is NEVER called
 * "Audit" (spec v3 §8, product_lead handoff).
 *
 * READ-ONLY: there are NO assignment / approve / admit / permission controls of
 * any kind here. Copy discipline is guidance-visibility throughout — the pure
 * mappers in `skills-validation.ts` translate every daemon severity / reason
 * code / source into product language so no raw enum jargon or forbidden token
 * (materializ… / admit / permission / approve / grant / pending / active) is
 * ever rendered.
 *
 * Responsive (handoff §9): the five filters sit inline on `md`+, and collapse
 * into a "Filters" disclosure drawer below `md` so the event list stays
 * on-canvas at mobile widths. The global AppShell nav collapse is a separate
 * shell-level concern (MEM-004), out of this slice's scope.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Activity,
  CheckCircle2,
  Info,
  SlidersHorizontal,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useSkillValidation } from '@/hooks/skills';
import {
  agentOptions,
  buildValidationQuery,
  EMPTY_FILTERS,
  SEVERITY_OPTIONS,
  SOURCE_OPTIONS,
  TIME_OPTIONS,
  skillOptions,
  toValidationRow,
  type FilterOption,
  type SeverityFilter,
  type SourceFilter,
  type TimeFilter,
  type ValidationFilters,
  type ValidationRow,
  type ValidationTone,
} from './skills-validation';

const TONE_STYLE: Record<ValidationTone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  attention: 'text-attention-text bg-attention-soft',
  neutral: 'text-fg-muted border border-border-default bg-transparent',
};

function SeverityBadge({ row }: { row: ValidationRow }): JSX.Element {
  const { text, tone } = row.severity;
  const Icon = tone === 'positive' ? CheckCircle2 : tone === 'attention' ? TriangleAlert : Info;
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${TONE_STYLE[tone]}`}
    >
      <Icon size={11} aria-hidden="true" className="shrink-0" />
      {text}
    </span>
  );
}

/** One filter <select> with an accessible label. `idPrefix` keeps the desktop
 *  and mobile-drawer renders from colliding on element ids. */
function FilterSelect({
  idPrefix,
  name,
  label,
  value,
  options,
  onChange,
}: {
  idPrefix: string;
  name: string;
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
}): JSX.Element {
  const id = `${idPrefix}-${name}`;
  return (
    <label htmlFor={id} className="flex min-w-0 flex-col gap-1">
      <span className="text-fg-subtle text-overline tracking-wider uppercase">
        {label}
      </span>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="border-border-default bg-surface-raised text-fg text-body-sm focus-visible:ring-accent w-full min-w-0 rounded-md border px-2.5 py-1.5 focus:outline-none focus-visible:ring-2"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function FilterControls({
  idPrefix,
  filters,
  setFilters,
  skillOpts,
  agentOpts,
}: {
  idPrefix: string;
  filters: ValidationFilters;
  setFilters: (f: ValidationFilters) => void;
  skillOpts: FilterOption[];
  agentOpts: FilterOption[];
}): JSX.Element {
  return (
    <>
      <FilterSelect
        idPrefix={idPrefix}
        name="skill"
        label="Skill"
        value={filters.skill}
        options={[{ value: '', label: 'All skills' }, ...skillOpts]}
        onChange={(v) => setFilters({ ...filters, skill: v })}
      />
      <FilterSelect
        idPrefix={idPrefix}
        name="agent"
        label="Agent"
        value={filters.agent}
        options={[{ value: '', label: 'All agents' }, ...agentOpts]}
        onChange={(v) => setFilters({ ...filters, agent: v })}
      />
      <FilterSelect
        idPrefix={idPrefix}
        name="source"
        label="Source"
        value={filters.source}
        options={SOURCE_OPTIONS}
        onChange={(v) => setFilters({ ...filters, source: v as SourceFilter })}
      />
      <FilterSelect
        idPrefix={idPrefix}
        name="time"
        label="Time"
        value={filters.time}
        options={TIME_OPTIONS}
        onChange={(v) => setFilters({ ...filters, time: v as TimeFilter })}
      />
      <FilterSelect
        idPrefix={idPrefix}
        name="severity"
        label="Result"
        value={filters.severity}
        options={SEVERITY_OPTIONS}
        onChange={(v) =>
          setFilters({ ...filters, severity: v as SeverityFilter })
        }
      />
    </>
  );
}

function EventRow({ row, slug }: { row: ValidationRow; slug: string }): JSX.Element {
  const OkIcon = row.ok ? CheckCircle2 : XCircle;
  const okTint = row.ok
    ? 'bg-tier-green-tint text-status-open'
    : 'bg-attention-soft text-attention-text';
  return (
    <article
      className="border-border-default bg-surface-raised flex gap-3.5 rounded-md border p-4"
      data-event-id={row.id}
      data-severity={row.severity.text}
    >
      <div
        aria-hidden="true"
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${okTint}`}
      >
        <OkIcon size={18} />
        <span className="sr-only">{row.okLabel}</span>
      </div>

      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <SeverityBadge row={row} />
          <span className="text-2xs bg-info-soft text-info inline-flex items-center rounded-full px-2 py-0.5 font-bold tracking-wide uppercase">
            {row.sourceLabel}
          </span>
          <span className="text-mono-sm text-fg-subtle">v{row.version}</span>
          <time
            className="text-fg-subtle text-xs"
            title={row.time.absolute}
          >
            {row.time.relative}
          </time>
        </div>

        <h3 className="text-fg font-mono text-sm leading-snug font-semibold break-words">
          <Link
            to={`/orgs/${slug}/skills/${row.skillId}`}
            className="hover:text-accent-text focus-visible:ring-accent rounded focus:outline-none focus-visible:ring-2"
          >
            {row.skillName}
          </Link>
        </h3>
        <p className="text-fg-muted text-body-sm mt-1 break-words">
          {row.agentLabel}
        </p>

        {(row.reasonLines.length > 0 || row.findings.length > 0) && (
          <ul className="text-fg-muted text-body-sm mt-2.5 flex flex-col gap-1">
            {row.reasonLines.map((line, i) => (
              <li key={`r${i}`} className="flex gap-2 break-words">
                <span aria-hidden="true" className="text-fg-subtle shrink-0">
                  ·
                </span>
                <span className="min-w-0">{line}</span>
              </li>
            ))}
            {row.findings.map((line, i) => (
              <li key={`f${i}`} className="flex gap-2 break-words">
                <span aria-hidden="true" className="text-fg-subtle shrink-0">
                  ·
                </span>
                <span className="min-w-0">{line}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}

export function SkillValidationPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [filters, setFilters] = useState<ValidationFilters>(EMPTY_FILTERS);

  // Recompute the query params only when the filters change — capturing `now`
  // here (not on every render) keeps the query object stable so React Query
  // does not refetch in a loop when a time window is selected.
  const query = useMemo(() => buildValidationQuery(filters, Date.now()), [filters]);
  const isFiltered = query !== undefined;

  // Baseline (unfiltered) query populates the skill/agent filter options so
  // they never collapse to the currently-filtered subset. When no filter is
  // active it shares a key with the list query → one fetch.
  const optionsQuery = useSkillValidation();
  const listQuery = useSkillValidation(query);

  const nowMs = Date.now();
  const baseEvents = optionsQuery.data?.events ?? [];
  const skillOpts = skillOptions(baseEvents);
  const agentOpts = agentOptions(baseEvents);

  const events = listQuery.data?.events ?? [];
  const rows = events.map((e) => toValidationRow(e, nowMs));
  const title =
    listQuery.data?.label ?? optionsQuery.data?.label ?? 'Runtime Validation';

  return (
    <div className="mx-auto flex h-full w-full max-w-5xl flex-col overflow-y-auto px-4 py-5 md:px-7 md:py-6">
      <header className="mb-4">
        <div className="text-fg-subtle text-overline mb-1 flex items-center gap-1.5 tracking-wider uppercase">
          <Activity size={13} aria-hidden="true" />
          Skills
        </div>
        <h1 className="text-h2 text-fg">{title}</h1>
        <p className="text-fg-muted text-body-sm mt-1 max-w-2xl">
          A read-only record of what happened when skills were checked or
          prepared for a session.
        </p>
      </header>

      {/* Guidance-only strip — mirrors the catalog note-strip. */}
      <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mb-5 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
        <Info size={15} aria-hidden="true" className="text-fg-subtle shrink-0" />
        <span>
          <b className="text-fg font-semibold">Guidance visibility only.</b>{' '}
          These events record how skills were checked or shown — nothing here
          changes what an agent can do.
        </span>
      </div>

      {/* Desktop filter bar */}
      <div
        className="mb-5 hidden flex-wrap items-end gap-3 md:flex"
        aria-label="Runtime validation filters"
      >
        <FilterControls
          idPrefix="rv-desktop"
          filters={filters}
          setFilters={setFilters}
          skillOpts={skillOpts}
          agentOpts={agentOpts}
        />
        {isFiltered && (
          <button
            type="button"
            onClick={() => setFilters(EMPTY_FILTERS)}
            className="text-fg-muted hover:text-fg text-body-sm py-1.5 font-medium underline underline-offset-2"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Mobile filter drawer — keeps the list on-canvas below md. */}
      <details className="border-border-default bg-surface-raised mb-5 rounded-md border md:hidden">
        <summary className="text-fg text-body-sm flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 font-semibold">
          <SlidersHorizontal size={15} aria-hidden="true" className="shrink-0" />
          Filters
          {isFiltered && (
            <span className="bg-accent-soft text-accent-text ml-auto rounded-full px-2 py-0.5 text-xs font-semibold">
              On
            </span>
          )}
        </summary>
        <div
          className="border-border-default flex flex-col gap-3 border-t px-3 py-3"
          aria-label="Runtime validation filters"
        >
          <FilterControls
            idPrefix="rv-mobile"
            filters={filters}
            setFilters={setFilters}
            skillOpts={skillOpts}
            agentOpts={agentOpts}
          />
          {isFiltered && (
            <button
              type="button"
              onClick={() => setFilters(EMPTY_FILTERS)}
              className="text-fg-muted hover:text-fg text-body-sm self-start font-medium underline underline-offset-2"
            >
              Clear filters
            </button>
          )}
        </div>
      </details>

      {listQuery.isLoading ? (
        <ul className="flex flex-col gap-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <li
              key={i}
              className="border-border-subtle bg-surface-subtle h-24 animate-pulse rounded-md border"
            />
          ))}
        </ul>
      ) : listQuery.isError ? (
        <EmptyState
          icon={<TriangleAlert size={28} />}
          title="Could not load runtime validation"
          body="These events are unavailable right now. Try again shortly."
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Activity size={28} />}
          title={isFiltered ? 'No events match these filters' : 'No runtime validation events yet'}
          body={
            isFiltered
              ? 'Try widening the time window or clearing a filter.'
              : 'When your skills are checked or prepared for a session, those events will show up here.'
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {rows.map((row) => (
            <li key={row.id}>
              <EventRow row={row} slug={slug ?? ''} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Shared presentational pieces for Usage v1: the neutral delta line, the
 * section shell with its loading / error / stale states, and the horizontally
 * scrolling table frame with a frozen identity column.
 *
 * Deltas are rendered strictly from the daemon's `UsageDelta` — this module
 * never computes a change or a percentage. Styling is neutral: a delta never
 * uses success/danger colour, and its direction is always spelled out in text.
 */
import type { ReactNode } from 'react';
import { Button } from '@/design-system/primitives/Button';
import type { UsageDelta } from './useUsageData';
import { formatPercentDelta, signed, withheldExplanation } from './usageFormat';

export const DASH = '—';

/* ------------------------------------------------------------------ */
/*  Delta line                                                         */
/* ------------------------------------------------------------------ */

export interface DeltaLineProps {
  delta: UsageDelta | undefined;
  /** Formats an absolute (native-unit) magnitude, e.g. a count or a duration. */
  formatAbsolute: (magnitude: number) => string;
  /** The previous period's displayed value, for assistive text. */
  previous: string;
  /** Both periods' coverage, shown when the delta is withheld. */
  coverage: string;
  /**
   * Row-level suppression is stated once on the row, so the cell shows only
   * the dash.
   */
  rowWithheld?: boolean;
}

export function DeltaLine({
  delta,
  formatAbsolute,
  previous,
  coverage,
  rowWithheld = false,
}: DeltaLineProps): JSX.Element {
  const prior = <span className="sr-only"> (previous 7 days: {previous})</span>;
  if (rowWithheld || !delta || delta.kind === 'withheld' || delta.value === null && delta.kind !== 'no_change') {
    return (
      <span className="text-2xs text-text-muted mt-1 block font-sans">
        <span data-delta="withheld" className="font-mono">{DASH}</span>
        {rowWithheld ? (
          <span className="sr-only"> Comparison withheld for this row.</span>
        ) : (
          <>
            <span className="block">{withheldExplanation(delta?.withheld_reason ?? null)}</span>
            <span className="block">{coverage}</span>
          </>
        )}
      </span>
    );
  }
  let text: string;
  // The daemon reports an unchanged non-zero Efficiency run count as an
  // absolute movement of 0; a zero movement always reads "No change" (PRD §4).
  switch (delta.kind === 'absolute' && delta.value === 0 ? 'no_change' : delta.kind) {
    case 'no_change':
      text = 'No change';
      break;
    case 'new_from_zero':
      text = 'New from 0';
      break;
    case 'percent':
      text = formatPercentDelta(delta.value as number);
      break;
    default:
      text = signed(formatAbsolute(Math.abs(delta.value as number)), (delta.value as number) < 0);
  }
  return (
    <span className="text-2xs text-text-muted mt-1 block font-mono tabular-nums">
      <span data-delta={delta.kind}>{text}</span>
      {prior}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Section shell                                                      */
/* ------------------------------------------------------------------ */

export interface SectionProps {
  id: string;
  title: string;
  question: string;
  /** "Data through … · generated …" once a response exists. */
  meta: string | null;
  /** Announced politely on change. */
  status: string;
  controls?: ReactNode;
  children: ReactNode;
}

export function UsageSection({
  id,
  title,
  question,
  meta,
  status,
  controls,
  children,
}: SectionProps): JSX.Element {
  const headingId = `${id}-heading`;
  return (
    <section aria-labelledby={headingId} className="mb-7">
      <h2 id={headingId} className="font-display text-display text-text-primary">
        {title}
      </h2>
      <p className="text-body text-text-secondary mt-1">{question}</p>
      {meta && <p className="text-caption text-text-muted mt-1">{meta}</p>}
      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>
      {controls}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function SectionError({
  view,
  onRetry,
  retrying,
}: {
  view: string;
  onRetry: () => void;
  retrying: boolean;
}): JSX.Element {
  return (
    <div className="border-feedback-danger/30 bg-feedback-danger/5 rounded-lg border p-4">
      <p className="text-body text-text-primary font-medium">Couldn’t load {view}.</p>
      <p className="text-caption text-text-secondary mt-1">
        The {view.toLowerCase()} view failed to load. The controls above still work.
      </p>
      <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry} disabled={retrying}>
        Retry
      </Button>
    </div>
  );
}

export function StaleNotice({
  dataThrough,
  onRetry,
  retrying,
}: {
  dataThrough: string;
  onRetry: () => void;
  retrying: boolean;
}): JSX.Element {
  return (
    <div className="border-feedback-warning/40 bg-attention-soft mb-3 flex flex-wrap items-center gap-3 rounded-lg border p-3">
      <div className="min-w-0 flex-1">
        <p className="text-body text-text-primary font-medium">
          Stale: showing figures from an earlier load. The latest refresh failed.
        </p>
        <p className="text-caption text-text-secondary">Data through {dataThrough}</p>
      </div>
      <Button variant="secondary" size="sm" onClick={onRetry} disabled={retrying}>
        Retry
      </Button>
    </div>
  );
}

/** Table-shaped loading skeleton: real headers, no figures. */
export function TableSkeleton({ columns, rows = 4 }: { columns: string[]; rows?: number }): JSX.Element {
  return (
    <div
      data-testid="usage-skeleton"
      aria-busy="true"
      className="bg-surface border-border-default shadow-pasture-sm overflow-hidden rounded-lg border"
    >
      <div className="border-border-default flex gap-4 border-b px-4 py-3">
        {columns.map((c) => (
          <span key={c} className="text-2xs text-text-muted flex-1 font-semibold tracking-wider uppercase">
            {c}
          </span>
        ))}
      </div>
      <div className="animate-pulse space-y-3 px-4 py-4">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="bg-surface-sunken h-5 rounded" />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Scrollable table frame                                             */
/* ------------------------------------------------------------------ */

export function ScrollTable({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border">
      <p className="text-caption text-text-muted border-border-default border-b px-4 py-2 md:hidden">
        Scroll sideways to see every column.
      </p>
      <div
        role="region"
        aria-label={`${label}, scrolls sideways`}
        // Keyboard users can scroll the region once focused.
        tabIndex={0}
        className="focus-visible:ring-accent-ring overflow-x-auto rounded-lg focus-visible:ring-2 focus-visible:outline-none"
      >
        {children}
      </div>
    </div>
  );
}

export const TH_CLASS =
  'text-2xs text-text-muted border-border-default border-b px-4 py-3 font-semibold tracking-wider uppercase align-bottom';

export const IDENTITY_CLASS =
  'sticky left-0 z-10 bg-surface px-4 py-3 text-left align-top font-normal';

export const CELL_CLASS = 'px-4 py-3 text-right align-top font-mono tabular-nums';

export function FootnoteMark({ n }: { n: number }): JSX.Element {
  return (
    <sup aria-hidden="true" className="ml-1">
      {n}
    </sup>
  );
}

export function Footnotes({ children }: { children: ReactNode }): JSX.Element {
  return <div className="text-caption text-text-muted mt-3 max-w-content-form space-y-1">{children}</div>;
}

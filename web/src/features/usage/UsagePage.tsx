/**
 * UsagePage — Usage v1 (THR-272 PR4; product contract TASK-9165).
 *
 * One page, one shared period: the fixed "Last 7 days" window resolved by the
 * daemon, an optional Compare toggle (off by default) against the adjacent
 * previous 7 days, then two sections:
 *
 *   - Workload   — CLI-neutral lifecycle counts per agent (GET /usage/workload)
 *   - Efficiency — one CLI × one model cohort by run type (GET /usage/efficiency)
 *
 * Windows, coverage, medians and deltas are rendered exactly as the daemon
 * returns them; the page computes no window, join, percentage or baseline.
 * Missing usage is never presented as zero, and nothing is aggregated across
 * CLIs or models. Export, cost and blended token totals are out of scope.
 */
import { useState } from 'react';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { EfficiencySection } from './EfficiencySection';
import { WorkloadSection } from './WorkloadSection';
import { useEfficiencyOptions, useWorkload } from './useUsageData';
import { formatWindow } from './usageFormat';

function CompareSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="text-caption text-text-primary focus-visible:ring-accent-ring inline-flex items-center gap-2 rounded-sm px-1 py-1 font-semibold focus-visible:ring-2 focus-visible:outline-none"
    >
      <span
        aria-hidden="true"
        className={`inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-raised border-border border'
        }`}
      >
        <span
          className={`bg-surface inline-block h-3.5 w-3.5 rounded-full shadow transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </span>
      Compare with previous 7 days
    </button>
  );
}

function PeriodLabel({ compare }: { compare: boolean }): JSX.Element {
  // The window always comes from a daemon response; until one arrives only the
  // fixed period name is shown. Both queries are shared with the sections.
  const workload = useWorkload(compare).data;
  const options = useEfficiencyOptions(compare).data;
  const source = workload ?? options;
  if (!source) return <>Last 7 days</>;
  return (
    <>
      Last 7 days · {formatWindow(source.current_window)} ({source.timezone})
      {compare && source.previous_window && (
        <span className="block">
          Compared with {formatWindow(source.previous_window)}
        </span>
      )}
    </>
  );
}

export function UsagePage(): JSX.Element {
  const [compare, setCompare] = useState(false);
  return (
    <ContentWrap>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <PageHeader title="Usage" meta={<PeriodLabel compare={compare} />} />
        </div>
        <CompareSwitch checked={compare} onChange={setCompare} />
      </header>
      <WorkloadSection compare={compare} />
      <EfficiencySection compare={compare} />
    </ContentWrap>
  );
}

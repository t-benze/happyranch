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
import { TopbarStatus } from '@/design-system/layouts/AppShell/TopbarSlots';
import { EfficiencySection } from './EfficiencySection';
import { WorkloadSection } from './WorkloadSection';
import { useEfficiencyOptions, useWorkload } from './useUsageData';
import { useUsagePresentation } from './strings';

function CompareSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}): JSX.Element {
  const { t } = useUsagePresentation();
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
        className={`inline-flex h-6 w-9 shrink-0 items-center rounded-full transition-colors ${
          checked ? 'bg-accent-default' : 'bg-surface-raised border-border-default border'
        }`}
      >
        <span
          className={`bg-surface-raised inline-block h-3.5 w-3.5 rounded-full shadow-pasture-sm transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </span>
      {t('usage.compare')}
    </button>
  );
}

function PeriodLabel({ compare }: { compare: boolean }): JSX.Element {
  const { t, formatWindow } = useUsagePresentation();
  // The window always comes from a daemon response; until one arrives only the
  // fixed period name is shown. Both queries are shared with the sections.
  const workload = useWorkload(compare).data;
  const options = useEfficiencyOptions(compare).data;
  const source = workload ?? options;
  if (!source) return <>{t('usage.period')}</>;
  return (
    <>
      {t('usage.window', { window: formatWindow(source.current_window), timezone: source.timezone })}
      {compare && source.previous_window && (
        <span className="block">
          {t('usage.previousWindow', { window: formatWindow(source.previous_window) })}
        </span>
      )}
    </>
  );
}

export function UsagePage(): JSX.Element {
  const { t } = useUsagePresentation();
  const [compare, setCompare] = useState(false);
  return (
    <ContentWrap>
      {/* THR-376 r2 U1/A2: the AppBar already names the page, so the visible
          h1 is dropped (kept for assistive tech) and the window label plus the
          Compare toggle move into the AppBar status slot; the page starts at
          the first section. */}
      <h1 className="sr-only">{t('usage.title')}</h1>
      <TopbarStatus>
        <span className="text-caption text-text-muted min-w-0 leading-tight">
          <PeriodLabel compare={compare} />
        </span>
        <CompareSwitch checked={compare} onChange={setCompare} />
      </TopbarStatus>
      <WorkloadSection compare={compare} />
      <EfficiencySection compare={compare} />
    </ContentWrap>
  );
}

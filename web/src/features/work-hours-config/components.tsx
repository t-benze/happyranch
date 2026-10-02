/**
 * Small presentational helpers shared across the Work-Hours Config screens.
 * Props-in/JSX-out; no data fetching.
 */
import { AlertTriangle } from 'lucide-react';
import { SubTabBar } from '@/design-system/primitives/SubTabBar';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { Provenance } from './merge';

/**
 * Sub-nav strip for the Work Hours surface: Overview (config roster) vs Wakes
 * (read-only wake-execution list, folded in from the retired Schedule surface).
 * The Wakes view is an in-page tab driven by the `?view=wakes` query param —
 * no sibling route, so it never collides with the `work-hours/:agent` detail
 * route. Parents derive `active` from the URL.
 */
export function WorkHoursTabs({
  slug,
  active,
}: {
  slug: string | undefined;
  active: 'overview' | 'wakes';
}): JSX.Element {
  const { t } = useTranslation();
  const base = `/orgs/${slug ?? ''}/work-hours`;
  return (
    <SubTabBar
      active={active}
      tabs={[
        { value: 'overview', label: t('workHours.tabs.overview'), to: base },
        { value: 'wakes', label: t('workHours.tabs.wakes'), to: `${base}?view=wakes` },
      ]}
    />
  );
}

const PROVENANCE_LABEL: Record<Provenance, MessageKey> = {
  org: 'workHours.provenance.org',
  team: 'workHours.provenance.team',
  agent: 'workHours.provenance.agent',
  unset: 'workHours.provenance.unset',
};

const PROVENANCE_STYLE: Record<Provenance, string> = {
  org: 'bg-tier-green-tint text-status-open',
  team: 'bg-tier-yellow-tint text-status-archiving',
  agent: 'bg-accent-soft text-accent-text',
  unset: 'bg-surface-sunken text-text-muted',
};

export function ProvenanceBadge({
  source,
  teamName,
}: {
  source: Provenance;
  teamName?: string | null;
}): JSX.Element {
  const { t } = useTranslation();
  const label =
    source === 'team' && teamName
      ? t('workHours.provenance.teamNamed', { team: teamName })
      : t(PROVENANCE_LABEL[source]);
  return (
    <span
      className={`text-mono-sm inline-flex items-center rounded-full px-1.5 py-0.5 font-semibold ${PROVENANCE_STYLE[source]}`}
    >
      {label}
    </span>
  );
}

export function EligibilityChip({ eligible }: { eligible: boolean }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        eligible
          ? 'bg-tier-green-tint text-status-open'
          : 'bg-surface-sunken text-text-muted'
      }`}
    >
      {eligible ? t('workHours.eligibility.eligible') : t('workHours.eligibility.excluded')}
    </span>
  );
}

export function OnDot({ on }: { on: boolean }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        aria-hidden="true"
        className={`inline-block h-2 w-2 rounded-full ${
          on ? 'bg-tier-green' : 'bg-surface-sunken'
        }`}
      />
      <span className="text-text-muted">{on ? t('workHours.onDot.on') : t('workHours.onDot.off')}</span>
    </span>
  );
}

export function NoRoutineTasksFlag(): JSX.Element {
  const { t } = useTranslation();
  return (
    <span className="text-feedback-danger inline-flex items-center gap-1 text-xs">
      <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
      {t('workHours.noRoutineTasks')}
    </span>
  );
}

/** Config-broken-on-disk recovery banner (spec §5.1). */
export function RecoveryBanner({ reason }: { reason: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="border-tier-red bg-feedback-danger/10 text-tier-red flex items-start gap-2 rounded border p-3 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-medium">{t('workHours.recovery.title')}</p>
        <p className="mt-0.5">{reason}</p>
        <p className="mt-0.5">{t('workHours.recovery.hint')}</p>
      </div>
    </div>
  );
}

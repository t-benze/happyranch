/**
 * DashboardLayout — four-card grid for the Live Status page (PR 12).
 *
 * Slot-based. Each slot is wrapped in a card frame (border + raised surface +
 * padded body) so feature code only supplies the body JSX. Two-column grid on
 * `lg` screens, single-column on small viewports. The umbrella spec scopes
 * this app to desktop, but the responsive collapse keeps narrow windows
 * usable without forking layouts.
 *
 * Spec: `docs/superpowers/specs/2026-05-19-web-dashboard-design.md` §4.
 */
import type { ReactNode } from 'react';

interface DashboardCardProps {
  label: string;
  children: ReactNode;
  /** When true the card spans both columns on `lg`. */
  wide?: boolean;
}

function DashboardCard({ label, children, wide }: DashboardCardProps): JSX.Element {
  return (
    <section
      className={[
        'border-border-subtle bg-surface-raised rounded-lg border p-4',
        wide ? 'lg:col-span-2' : '',
      ].join(' ')}
      aria-label={label}
    >
      <h2 className="text-overline text-text-muted mb-3 tracking-wide uppercase">
        {label}
      </h2>
      {children}
    </section>
  );
}

export interface DashboardLayoutProps {
  health: ReactNode;
  pending: ReactNode;
  activeByTeam: ReactNode;
  blocked: ReactNode;
}

export function DashboardLayout({
  health,
  pending,
  activeByTeam,
  blocked,
}: DashboardLayoutProps): JSX.Element {
  return (
    <div className="bg-surface-canvas h-full overflow-y-auto p-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DashboardCard label="System health">{health}</DashboardCard>
        <DashboardCard label="Pending your action">{pending}</DashboardCard>
        <DashboardCard label="Active tasks by team" wide>
          {activeByTeam}
        </DashboardCard>
        <DashboardCard label="Blocked tasks" wide>
          {blocked}
        </DashboardCard>
      </div>
    </div>
  );
}

export const meta = {
  name: 'DashboardLayout',
  layer: 'layout',
  import: '@/design-system/layouts/DashboardLayout',
  variants: {},
  consumes: [],
  example:
    "<DashboardLayout health={<div/>} pending={<div/>} activeByTeam={<div/>} blocked={<div/>} />",
} as const;

/**
 * Placeholder shell. Future home of the audit + escalation + trace
 * surfaces. Three sub-tabs planned per `web/UI_SPEC.md` §10 (Activity /
 * Escalations / Traces); founder-dashboard data lands here.
 */
import { EmptyState } from '@/design-system/patterns/EmptyState';

export function AuditPage(): JSX.Element {
  return (
    <EmptyState
      title="Audit"
      body="Activity log, escalation history, and execution traces. Coming soon."
    />
  );
}

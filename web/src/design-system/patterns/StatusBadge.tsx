/**
 * StatusBadge — pill for thread OR task status. Per DESIGN.md
 * `components.badge.variants.status_*`.
 *
 * For task status `blocked`, the optional `blockKind` modifier renders
 * "blocked (escalated)" vs "blocked (delegated)" with escalated using the
 * red escalated token.
 *
 * Pure prop-driven.
 */

export type ThreadStatus = 'open' | 'archived';
export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'resolved_superseded';
export type BlockKind = 'delegated' | 'escalated';

interface StatusBadgeProps {
  status: ThreadStatus | TaskStatus;
  blockKind?: BlockKind | null;
}

// Reuse tokens to keep the palette tight. The mapping mirrors semantic
// kinship: in_progress→open (green), completed→archived (grey),
// failed→abandoned tint (red), resolved_superseded→archived (grey, a clean
// non-failure terminal like completed).
const STATUS_CLASS: Record<ThreadStatus | TaskStatus, string> = {
  open: 'bg-tier-green-tint text-status-open',
  archived: 'border border-border-subtle bg-transparent text-status-archived',
  pending: 'bg-tier-yellow-tint text-status-archiving',
  in_progress: 'bg-tier-green-tint text-status-open',
  blocked: 'bg-tier-yellow-tint text-status-blocked',
  completed: 'border border-border-subtle bg-transparent text-status-archived',
  failed: 'bg-tier-red-tint text-status-abandoned',
  resolved_superseded: 'border border-border-subtle bg-transparent text-status-archived',
};

function label(status: ThreadStatus | TaskStatus, blockKind?: BlockKind | null): string {
  if (status === 'blocked' && blockKind) return `blocked (${blockKind})`;
  if (status === 'resolved_superseded') return 'resolved (superseded)';
  return status;
}

export function StatusBadge({ status, blockKind }: StatusBadgeProps): JSX.Element {
  const cls =
    status === 'blocked' && blockKind === 'escalated'
      ? 'bg-tier-red-tint text-status-escalated'
      : STATUS_CLASS[status];
  return (
    <span
      className={`text-mono-sm inline-flex items-center rounded-sm px-2 py-px font-mono font-semibold ${cls}`}
    >
      {label(status, blockKind)}
    </span>
  );
}

export const meta = {
  name: "StatusBadge",
  layer: "pattern",
  import: "@/design-system/patterns/StatusBadge",
  variants: {
    status: [
      "open",
      "archived",
      "pending",
      "in_progress",
      "blocked",
      "completed",
      "failed",
      "resolved_superseded",
    ],
  },
  consumes: ["components.badge"],
  example: "<StatusBadge status='open' />",
} as const;

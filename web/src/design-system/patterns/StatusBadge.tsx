/**
 * StatusBadge — pill for thread OR task status. Direction-A Pasture style
 * matching ds.css .tag pattern: rounded-pill, inline-flex, led dot for
 * active states, tinted fills.
 *
 * Task vocabulary follows THR-037 Change B (Path B, stored source-of-truth):
 * `blocked` is gone. `escalated` is a first-class red status; `cancelled` is a
 * muted terminal. A parent waiting on its own children/jobs stays an ACTIVE
 * (green) `in_progress` badge with a DERIVED muted qualifier ("· waiting on
 * subtasks" / "· waiting on jobs") from the `blockKind` discriminant — the
 * waiting nuance is a modifier, NOT a separate top-level status.
 *
 * Pure prop-driven.
 */

export type ThreadStatus = 'open' | 'archived';
export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'escalated'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'resolved_superseded'
  // DEPRECATED (Path B transition). Retained so legacy status='blocked'
  // rows paired with block_kind='escalated' still render a badge. Remove
  // in a later cleanup phase.
  | 'blocked';
export type BlockKind = 'delegated' | 'blocked_on_job' | 'escalated';

interface StatusBadgeProps {
  status: ThreadStatus | TaskStatus;
  blockKind?: BlockKind | null;
}

// ds.css .tag pattern: rounded-pill, 11px/600, tinted bg + text, optional led dot.
// .tag.ok = green (open/in_progress/completed)
// .tag.warn = amber (pending)
// .tag.bad = red (failed/escalated)
// .tag.mute = grey (cancelled/resolved_superseded/archived)
const STATUS_STYLE: Record<ThreadStatus | TaskStatus, string> = {
  open: 'text-status-open bg-tier-green-tint',
  archived: 'text-status-archived border border-border-default bg-transparent',
  pending: 'text-status-archiving bg-tier-yellow-tint',
  in_progress: 'text-status-open bg-tier-green-tint',
  escalated: 'text-status-escalated bg-tier-red-tint',
  blocked: 'text-status-escalated bg-tier-red-tint',
  completed: 'text-status-open bg-tier-green-tint',
  failed: 'text-status-abandoned bg-tier-red-tint',
  cancelled: 'text-status-archived border border-border-default bg-transparent',
  resolved_superseded: 'text-status-archived border border-border-default bg-transparent',
};

/**
 * Derived waiting qualifier (THR-037 §F.1): a parked `in_progress` task names
 * what it is internally waiting on. Returns null for a running task (no
 * blockKind) or any non-in_progress status.
 */
function waitingQualifier(
  status: ThreadStatus | TaskStatus,
  blockKind?: BlockKind | null,
): string | null {
  if (status !== 'in_progress' || !blockKind) return null;
  if (blockKind === 'delegated') return '· waiting on subtasks';
  if (blockKind === 'blocked_on_job') return '· waiting on jobs';
  return null;
}

function label(status: ThreadStatus | TaskStatus): string {
  if (status === 'resolved_superseded') return 'resolved (superseded)';
  return status;
}

export function StatusBadge({ status, blockKind }: StatusBadgeProps): JSX.Element {
  const cls = STATUS_STYLE[status];
  const qualifier = waitingQualifier(status, blockKind);
  // Led dot for live/active states only (matches ds.css .tag led).
  const showDot =
    status === 'in_progress' || status === 'completed' || status === 'pending';
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-semibold tabular-nums ${cls}`}
    >
      {showDot && (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />
      )}
      {label(status)}
      {qualifier && (
        <span className="font-normal opacity-70">{qualifier}</span>
      )}
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
      "escalated",
      "completed",
      "failed",
      "cancelled",
      "resolved_superseded",
      "blocked",
    ],
  },
  consumes: ["components.badge"],
  example: "<StatusBadge status='open' />",
} as const;

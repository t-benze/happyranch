/**
 * StatusBadge — pill for thread OR task status. Direction-A Pasture style
 * matching ds.css .tag pattern: rounded-pill, inline-flex, led dot for
 * active states, tinted fills.
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
export type BlockKind = 'delegated' | 'escalated' | 'blocked_on_job';

interface StatusBadgeProps {
  status: ThreadStatus | TaskStatus;
  blockKind?: BlockKind | null;
}

// ds.css .tag pattern: rounded-pill, 11px/600, tinted bg + text, optional led dot.
// .tag.ok = green (open/in_progress/completed)
// .tag.run = blue (in_progress alternate — we use green via .ok)
// .tag.warn = amber (pending/blocked)
// .tag.bad = red (failed/escalated)
// .tag.mute = grey (resolved_superseded/archived)
const STATUS_STYLE: Record<ThreadStatus | TaskStatus, string> = {
  open: 'text-status-open bg-tier-green-tint',
  archived: 'text-status-archived border border-border-default bg-transparent',
  pending: 'text-status-archiving bg-tier-yellow-tint',
  in_progress: 'text-status-open bg-tier-green-tint',
  blocked: 'text-status-blocked bg-tier-yellow-tint',
  completed: 'text-status-open bg-tier-green-tint',
  failed: 'text-status-abandoned bg-tier-red-tint',
  resolved_superseded: 'text-status-archived border border-border-default bg-transparent',
};

function label(status: ThreadStatus | TaskStatus, blockKind?: BlockKind | null): string {
  if (status === 'blocked' && blockKind) return `blocked (${blockKind})`;
  if (status === 'resolved_superseded') return 'resolved (superseded)';
  return status;
}

export function StatusBadge({ status, blockKind }: StatusBadgeProps): JSX.Element {
  const escalated = status === 'blocked' && blockKind === 'escalated';
  const cls = escalated
    ? 'text-status-escalated bg-tier-red-tint'
    : STATUS_STYLE[status];
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-semibold tabular-nums ${cls}`}
    >
      {!escalated && (status === 'in_progress' || status === 'completed' || status === 'pending') && (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />
      )}
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

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

import { TONE_CLASS } from './semanticTone';

export type ThreadStatus = 'open' | 'archived';
export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'escalated'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'superseded'
  // DEPRECATED (Path B transition). Retained so legacy status='blocked'
  // rows paired with block_kind='escalated' still render a badge. Remove
  // in a later cleanup phase.
  | 'blocked';
export type BlockKind = 'delegated' | 'blocked_on_job' | 'escalated';

interface StatusBadgeProps {
  status: ThreadStatus | TaskStatus;
  blockKind?: BlockKind | null;
}

// Tinted pill tones read from the shared semantic colour vocabulary
// (semanticTone.TONE_CLASS) so this DS badge matches every other status/type
// badge once Batches 2–3 wire the feature surfaces onto the map.
// THR-099 fix: thread `open` moves GREEN → BLUE (info) per the design
// vocabulary (open=blue / archived=grey). `pending` (amber) and `failed`
// (abandoned-red) keep their own distinct tokens — they are not tones in the
// shared set — so they stay literal.
const STATUS_STYLE: Record<ThreadStatus | TaskStatus, string> = {
  open: TONE_CLASS.info,
  archived: TONE_CLASS.neutral,
  pending: 'text-status-archiving bg-tier-yellow-tint',
  in_progress: TONE_CLASS.positive,
  escalated: TONE_CLASS.danger,
  blocked: TONE_CLASS.danger,
  completed: TONE_CLASS.positive,
  failed: 'text-status-abandoned bg-tier-red-tint',
  cancelled: TONE_CLASS.neutral,
  superseded: TONE_CLASS.neutral,
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
      "superseded",
      "blocked",
    ],
  },
  consumes: ["components.badge"],
  example: "<StatusBadge status='open' />",
} as const;

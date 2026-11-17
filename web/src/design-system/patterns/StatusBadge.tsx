/**
 * StatusBadge — pill for thread OR task status. Direction-A Pasture style
 * matching ds.css .tag pattern: rounded-pill, inline-flex, led dot for
 * active states, tinted fills.
 *
 * Task vocabulary follows THR-037 Change B (Path B, stored source-of-truth):
 * `blocked` is gone. `escalated` is a first-class status (Pasture attention
 * amber, THR-376); `cancelled` is a muted terminal. A parked in_progress
 * badge receives observed child text from its feature caller. The ordinary job default remains supported.
 *
 * Pure prop-driven.
 */

import { TONE_CLASS, type Tone } from './semanticTone';

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
  /**
   * Optional localized waiting-qualifier text (without the leading `· `).
   * Null suppresses text. Undefined retains only the ordinary job default.
   */
  waitingQualifier?: string | null;
  waitingLabels?: Partial<Record<'delegated' | 'blocked_on_job', string>>;
}

// Every status resolves through the shared semantic tone vocabulary
// (semanticTone.TONE_CLASS) so the badge reads the same on every surface
// (THR-376, Pasture): open/in_progress = info blue, escalated (and its legacy
// `blocked` alias) and pending = attention amber, completed = positive green,
// failed = danger red, archived/cancelled/superseded = neutral grey.
const STATUS_TONE: Record<ThreadStatus | TaskStatus, Tone> = {
  open: 'info',
  archived: 'neutral',
  pending: 'attention',
  in_progress: 'info',
  escalated: 'attention',
  blocked: 'attention',
  completed: 'positive',
  failed: 'danger',
  cancelled: 'neutral',
  superseded: 'neutral',
};

/**
 * Derived waiting qualifier (THR-037 §F.1): a parked `in_progress` task names
 * what it is internally waiting on. Returns null for a running task (no
 * blockKind) or any non-in_progress status.
 */
function waitingQualifier(
  status: ThreadStatus | TaskStatus,
  blockKind?: BlockKind | null,
  labels?: StatusBadgeProps['waitingLabels'],
  resolved?: string | null,
): string | null {
  if (status !== 'in_progress' || !blockKind) return null;
  if (blockKind !== 'delegated' && blockKind !== 'blocked_on_job') return null;
  if (resolved !== undefined) return resolved ? `· ${resolved}` : null;
  if (blockKind === 'blocked_on_job') return `· ${labels?.blocked_on_job ?? 'waiting on jobs'}`;
  return null;
}
function label(status: ThreadStatus | TaskStatus): string {
  return status;
}

export function StatusBadge({ status, blockKind, waitingLabels, waitingQualifier: resolved }: StatusBadgeProps): JSX.Element {
  const cls = TONE_CLASS[STATUS_TONE[status]];
  const qualifier = waitingQualifier(status, blockKind, waitingLabels, resolved);
  // Led dot for live/active states only (matches ds.css .tag led).
  const showDot =
    status === 'in_progress' || status === 'completed' || status === 'pending';
  return (
    <span
      className={`font-sans text-overline inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-0.5 font-semibold tabular-nums ${cls}`}
    >
      {showDot && (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />
      )}
      {label(status)}
      {qualifier && (
        <span className="min-w-0 break-words font-normal opacity-70">{qualifier}</span>
      )}
    </span>
  );
}

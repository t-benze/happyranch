/**
 * Pair `escalation` rows with their corresponding `escalation_resolved` rows
 * in audit-log chronological order. A single task can cycle through multiple
 * escalate / resolve pairs, so resolutions must be matched FIFO against the
 * still-open escalations for that task — pairing only by `task_id` causes a
 * second escalation to inherit the first resolution's timestamp (and produce
 * a negative Δ to resolution).
 */
import type { AuditEntry } from '@/lib/api/types';

export interface FoldedEscalation {
  raised_at: string;
  resolved_at: string | null;
  agent: string | null;
  task_id: string | null;
}

interface OpenEscalation {
  index: number;
  task_id: string | null;
  raised_at: string;
  agent: string | null;
}

export function foldEscalations(entries: AuditEntry[]): FoldedEscalation[] {
  // Walk chronologically. We rely on (timestamp, id) being a stable
  // chronological order even when timestamps tie.
  const sorted = [...entries].sort((a, b) => {
    if (a.timestamp !== b.timestamp) {
      return a.timestamp < b.timestamp ? -1 : 1;
    }
    return a.id - b.id;
  });

  const open: OpenEscalation[] = [];
  const folded: FoldedEscalation[] = [];

  for (const e of sorted) {
    if (e.action === 'escalation') {
      const row: FoldedEscalation = {
        raised_at: e.timestamp,
        resolved_at: null,
        agent: e.agent,
        task_id: e.task_id,
      };
      folded.push(row);
      open.push({
        index: folded.length - 1,
        task_id: e.task_id,
        raised_at: e.timestamp,
        agent: e.agent,
      });
      continue;
    }
    if (e.action === 'escalation_resolved') {
      // Find the oldest still-open escalation for this task_id and close it.
      // No task_id ⇒ unmatchable; skip (stray events).
      if (e.task_id == null) continue;
      const matchIdx = open.findIndex((o) => o.task_id === e.task_id);
      if (matchIdx === -1) continue;
      const match = open[matchIdx];
      folded[match.index].resolved_at = e.timestamp;
      open.splice(matchIdx, 1);
    }
  }

  // Newest escalation first.
  folded.sort((a, b) => (a.raised_at < b.raised_at ? 1 : -1));
  return folded;
}

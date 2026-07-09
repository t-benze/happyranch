/**
 * EscalationInboxRow — inline approve-and-resolve expander for the
 * "Waiting on you" panel.
 *
 * Click to expand → rationale textarea autofocuses → ⌘↵ submits or Esc
 * collapses. Sends { decision: 'approve', rationale } to the existing
 * /tasks/{id}/resolve-escalation route. Reject-with-rationale lives on
 * the Tasks page's ResolveEscalationDialog; the dashboard is approve-only
 * because that's the common founder action.
 *
 * KB promotion is deferred — see spec §4.6.
 */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { DashboardEscalationRow } from '@/lib/api/types';
import { useResolveEscalation } from '@/hooks/tasks';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';

/** An escalation is "stale" once it has waited a full day (THR-061 slice 1).
 *  Purely client-derived from the real `age_seconds` field — no new metric. */
const STALE_THRESHOLD_SECONDS = 86_400;

/**
 * Per-flavor chip tint (THR-061 slice 1). Maps each DERIVED `flavor`
 * ("needs-decision" | "exhausted" | "over-budget") to an EXISTING Pasture
 * status tint so the founder can tell WHY a task is waiting at a glance.
 * Existing design-system tokens only — no new hex (hex gate). Unknown
 * flavors fall back to the neutral escalated tint the chip shipped with.
 */
function flavorChipClass(flavor: string): string {
  switch (flavor) {
    case 'needs-decision':
      return 'bg-attention-soft text-attention-text';
    case 'over-budget':
      return 'bg-tier-yellow-tint text-status-archiving';
    case 'exhausted':
      return 'bg-tier-red-tint text-status-abandoned';
    default:
      return 'bg-tier-red-tint text-status-escalated';
  }
}

interface EscalationInboxRowProps {
  row: DashboardEscalationRow;
  expanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  slug: string;
}

function relativeAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function EscalationInboxRow({
  row,
  expanded,
  onExpand,
  onCollapse,
  slug,
}: EscalationInboxRowProps): JSX.Element {
  const [rationale, setRationale] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const resolve = useResolveEscalation(row.task_id);
  const isStale = row.age_seconds >= STALE_THRESHOLD_SECONDS;

  useEffect(() => {
    if (expanded) {
      const t = setTimeout(() => taRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [expanded]);

  async function submit() {
    await resolve.mutateAsync({ decision: 'approve', rationale });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Escape') {
      onCollapse();
      e.preventDefault();
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      void submit();
      e.preventDefault();
    }
  }

  return (
    <div
      className="border-border-subtle rounded-md border p-3"
      onClick={() => !expanded && onExpand()}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-text-primary font-mono text-xs font-medium">
          {row.agent}
        </span>
        <span className="text-text-muted font-mono text-xs">·</span>
        <span className="text-text-muted font-mono text-xs">
          <Link
            to={`/orgs/${slug}/tasks/${row.task_id}`}
            className="text-id-task hover:underline"
          >
            {row.task_id}
          </Link>
          {' · '}{row.team}{' · '}
          <span
            className={isStale ? 'text-status-escalated font-medium' : undefined}
          >
            {relativeAge(row.age_seconds)}
          </span>
        </span>
        {isStale && (
          <span
            className="border-tier-red text-status-escalated rounded border px-1 text-xs font-medium uppercase"
            title="Waiting 24h+"
          >
            stale
          </span>
        )}
        {row.flavor && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${flavorChipClass(
              row.flavor,
            )}`}
          >
            {row.flavor}
          </span>
        )}
      </div>
      <p className="text-text-primary mt-1 text-sm">{row.question}</p>

      {expanded && (
        <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
          <Textarea
            ref={taRef}
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={`Your response to ${row.agent}… (⌘↵ to send)`}
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onCollapse} type="button">
              Cancel
            </Button>
            <Button
              onClick={() => void submit()}
              disabled={resolve.isPending}
              type="button"
            >
              {resolve.isPending ? 'Resolving…' : 'Approve & resolve'}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

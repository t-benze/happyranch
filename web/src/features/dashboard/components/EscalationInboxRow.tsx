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
import type { DashboardEscalationRow } from '@/lib/api/types';
import { useResolveEscalation } from '@/hooks/tasks';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';

interface EscalationInboxRowProps {
  row: DashboardEscalationRow;
  expanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
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
}: EscalationInboxRowProps): JSX.Element {
  const [rationale, setRationale] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const resolve = useResolveEscalation(row.task_id);

  useEffect(() => {
    if (expanded) {
      const t = setTimeout(() => taRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [expanded]);

  async function submit() {
    if (!rationale.trim()) return;
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
      <div className="flex items-baseline gap-2">
        <span className="text-text-primary font-mono text-xs font-medium">
          {row.agent}
        </span>
        <span className="text-text-muted font-mono text-xs">·</span>
        <span className="text-text-muted font-mono text-xs">
          {row.task_id} · {row.team} · {relativeAge(row.age_seconds)}
        </span>
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
              disabled={resolve.isPending || !rationale.trim()}
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

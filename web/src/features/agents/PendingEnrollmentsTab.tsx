/**
 * PendingEnrollmentsTab — lists agents in `_pending/`, each with an
 * approve / reject action.
 *
 * Approve is a one-click POST. Reject opens a small inline dialog asking
 * for an optional reason (mirrors the CLI's `grassland reject-agent` UX).
 *
 * The list only renders when `status=pending`; approved enrollments
 * live in the Active tab's main scorecard table.
 */
import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useApproveAgent, useEnrollmentsList, useRejectAgent } from '@/hooks/agents';

export function PendingEnrollmentsTab(): JSX.Element {
  const enrollments = useEnrollmentsList({ status: 'pending' });
  const approve = useApproveAgent();
  const reject = useRejectAgent();
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [pendingName, setPendingName] = useState<string | null>(null);

  if (enrollments.isLoading) {
    return <p className="text-fg-muted p-4 text-sm">Loading…</p>;
  }
  if (enrollments.isError) {
    return (
      <p className="text-tier-red p-4 text-sm">
        Failed to load pending enrollments.
      </p>
    );
  }

  const rows = enrollments.data?.enrollments ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No pending enrollments"
        body="Managers can enroll new agents via grassland talk + manage-agent."
      />
    );
  }

  const onApprove = async (name: string) => {
    setPendingName(name);
    try {
      await approve.mutateAsync(name);
    } finally {
      setPendingName(null);
    }
  };

  const onReject = async () => {
    if (!rejecting) return;
    setPendingName(rejecting);
    try {
      await reject.mutateAsync({
        agentName: rejecting,
        body: reason.trim() ? { reason: reason.trim() } : undefined,
      });
      setRejecting(null);
      setReason('');
    } finally {
      setPendingName(null);
    }
  };

  return (
    <>
      <ul className="space-y-2">
        {rows.map((e) => (
          <li
            key={e.name}
            className="border-border-subtle bg-surface-raised rounded-lg border p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <AgentChip name={e.name} role={e.role} />
                <p className="text-fg-muted mt-1 text-xs">
                  team: {e.team} · executor: {e.executor}
                  {e.enrolled_by && <> · enrolled by {e.enrolled_by}</>}
                </p>
                {e.description && (
                  <p className="text-fg mt-2 text-sm">{e.description}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  disabled={pendingName === e.name}
                  onClick={() => onApprove(e.name)}
                >
                  {pendingName === e.name && approve.isPending ? 'Approving…' : 'Approve'}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={pendingName === e.name}
                  onClick={() => {
                    setRejecting(e.name);
                    setReason('');
                  }}
                >
                  Reject
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {rejecting && (
        <Dialog open onOpenChange={(o) => !o && setRejecting(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Reject {rejecting}?</DialogTitle>
            </DialogHeader>
            <p className="text-fg-muted text-sm">
              The pending enrollment file will be removed and the agent dropped
              from teams.yaml. The reason is logged in the audit trail (optional).
            </p>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Reason (optional)"
            />
            <DialogFooter>
              <Button variant="ghost" onClick={() => setRejecting(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={reject.isPending}
                onClick={onReject}
              >
                {reject.isPending ? 'Rejecting…' : 'Reject'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

/**
 * PendingEnrollmentsTab — lists agents in `_pending/`, each with an
 * approve / reject action.
 *
 * Approve is a one-click POST. Reject opens a small inline dialog asking
 * for an optional reason (mirrors the CLI's `happyranch reject-agent` UX).
 *
 * The list only renders when `status=pending`; approved enrollments
 * live in the Active tab's main roster table.
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
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useApproveAgent, useEnrollmentsList, useRejectAgent } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import { classifyAgentError, renderAgentError } from './strings';

export function PendingEnrollmentsTab(): JSX.Element {
  const { t } = useTranslation();
  const enrollments = useEnrollmentsList({ status: 'pending' });
  const approve = useApproveAgent();
  const reject = useRejectAgent();
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [pendingName, setPendingName] = useState<string | null>(null);

  if (enrollments.isLoading) {
    return <p className="text-text-muted p-4 text-sm">{t('agents.common.loading')}</p>;
  }
  if (enrollments.isError) {
    return (
      <p className="text-tier-red p-4 text-sm">
        {renderAgentError(classifyAgentError(enrollments.error, 'agents.pending.error'), t)}
      </p>
    );
  }

  const rows = enrollments.data?.enrollments ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        title={t('agents.pending.emptyTitle')}
        body={t('agents.pending.emptyBody')}
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
            className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-display text-text-primary text-sm font-medium">
                    {e.name}
                  </span>
                  <span
                    aria-hidden="true"
                    className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                      e.role === 'manager'
                        ? 'bg-agent-manager'
                        : 'bg-agent-worker'
                    }`}
                  />
                </div>
                <p className="text-text-muted mt-1 text-xs">
                  {t('agents.meta.team', { team: e.team })} · {t('agents.meta.executor', { executor: e.executor })}
                  {e.enrolled_by && <> · {t('agents.pending.enrolledBy', { name: e.enrolled_by })}</>}
                </p>
                {e.description && (
                  <p className="text-text-secondary mt-2 text-sm">{e.description}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  disabled={pendingName === e.name}
                  onClick={() => onApprove(e.name)}
                >
                  {pendingName === e.name && approve.isPending ? t('agents.pending.approving') : t('agents.pending.approve')}
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
                  {t('agents.pending.reject')}
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {rejecting && (
        <Dialog open onOpenChange={(o) => !o && setRejecting(null)}>
          <DialogContent closeLabel={t('common.close')}>
            <DialogHeader>
              <DialogTitle>{t('agents.pending.rejectTitle', { name: rejecting })}</DialogTitle>
            </DialogHeader>
            <p className="text-fg-muted text-sm">
              {t('agents.pending.rejectBody')}
            </p>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder={t('agents.pending.reasonPlaceholder')}
            />
            <DialogFooter>
              <Button variant="ghost" onClick={() => setRejecting(null)}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="destructive"
                disabled={reject.isPending}
                onClick={onReject}
              >
                {reject.isPending ? t('agents.pending.rejecting') : t('agents.pending.reject')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

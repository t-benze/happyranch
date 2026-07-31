/**
 * PendingAdaptersSection — the Settings ▸ Executors founder-only pending
 * adapter approvals area (THR-107 seq220).
 *
 * Rendered ABOVE Custom CLIs (CustomProfilesSection). Lists only PENDING
 * adapters with Approve/Reject controls that require explicit confirm/cancel
 * naming the exact SHA-256 snapshot. Approve transitions PENDING → APPROVED
 * and then shows the existing Bind <profile> recovery action. Reject
 * atomically removes the PENDING entry.
 *
 * HONESTY FENCE: only fields the API returns are rendered. The server is the
 * single source of truth for eligibility and snapshot validity.
 * Onboarding NEVER renders this section — it is Settings-only.
 */
import { useState, useEffect, useCallback } from 'react';
import { Check, XCircle, Puzzle, Link, Trash2, Loader2 } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  ADAPTERS_KEY,
  useAdapters,
  useApproveAdapter,
  useBindAdapterProfile,
  useRejectAdapter,
  type AdapterEntry,
} from '@/hooks/adapters';
import { useQueryClient } from '@tanstack/react-query';

/** Extract a human-readable message from an ApiError or any thrown value. */
function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail && typeof err.detail === 'object' && 'msg' in err.detail) {
      return String((err.detail as { msg: unknown }).msg);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

/** Build the 6-field exact snapshot body for approval. */
function buildApproveBody(adapter: AdapterEntry) {
  return {
    executable: adapter.executable,
    executable_hash: adapter.executable_hash,
    version: adapter.version,
    capabilities: adapter.capabilities,
    contract_version: adapter.contract_version,
    workspace_adapter: adapter.workspace_adapter,
  };
}

/** Build the 6-field exact snapshot body for rejection. */
function buildRejectBody(adapter: AdapterEntry) {
  return buildApproveBody(adapter);
}

/** Short hash display — first 12 chars of sha256. */
function shortHash(hash: string): string {
  return hash.slice(0, 12) + '\u2026';
}

/* ── Bind completion states (mirrors ConnectFlow RecoveryBindCard pattern) ── */

type BindCardState =
  | 'ready'
  | 'binding'
  | 'verifying'
  | 'connected'
  | 'error';

/* ── Single pending adapter row ── */

function PendingAdapterRow({ adapter }: { adapter: AdapterEntry }): JSX.Element {
  const [approveConfirming, setApproveConfirming] = useState(false);
  const [rejectConfirming, setRejectConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bindState, setBindState] = useState<BindCardState>('ready');
  const [bindError, setBindError] = useState('');
  const [verifyTries, setVerifyTries] = useState(0);

  const approve = useApproveAdapter();
  const reject = useRejectAdapter();
  const bindProfile = useBindAdapterProfile();
  const qc = useQueryClient();

  // After approval succeeds, force a refetch so we pick up the new status.
  const refetchAdapters = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
  }, [qc]);

  const onApprove = async (): Promise<void> => {
    setError(null);
    try {
      await approve.mutateAsync({
        id: adapter.id,
        body: buildApproveBody(adapter),
      });
      refetchAdapters();
      // Reset approve state — the adapter will re-render as APPROVED with
      // bind-ready action.
      setApproveConfirming(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        refetchAdapters();
      } else {
        setError(errMessage(err, 'Could not approve this adapter.'));
      }
    }
  };

  const onReject = async (): Promise<void> => {
    setError(null);
    try {
      await reject.mutateAsync({
        id: adapter.id,
        body: buildRejectBody(adapter),
      });
      refetchAdapters();
      // Reset reject state — the adapter row will be removed by the parent
      // filtering on next render.
      setRejectConfirming(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        refetchAdapters();
      } else {
        setError(errMessage(err, 'Could not reject this adapter.'));
      }
    }
  };

  // Bind the approved adapter to its intended profile.
  const onBind = async (): Promise<void> => {
    if (!adapter.intended_profile_name) return;
    setBindState('binding');
    setBindError('');
    try {
      await bindProfile.mutateAsync({
        id: adapter.id,
        body: { profile_name: adapter.intended_profile_name },
      });
      setBindState('verifying');
      setVerifyTries(0);
    } catch (e: unknown) {
      setBindError(errMessage(e, 'Bind failed. Retry or contact the founder.'));
      setBindState('error');
    }
  };

  // After bind success, poll the adapter list endpoint to verify the
  // server now reports eligibility === 'already_bound'.
  useEffect(() => {
    if (bindState !== 'verifying') return;
    const MAX_TRIES = 6;
    const INTERVAL_MS = 1500;

    if (verifyTries >= MAX_TRIES) {
      setBindError(
        'Bind succeeded but server verification timed out. Refresh the page — the profile may already be connected.',
      );
      setBindState('error');
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (cancelled) return;
      try {
        refetchAdapters();
        const { adapters } = await import('@/lib/api');
        const entry = await adapters.getAdapter(adapter.id);
        if (!cancelled) {
          if (entry && entry.eligibility === 'already_bound') {
            setBindState('connected');
          } else {
            setVerifyTries((t) => t + 1);
          }
        }
      } catch {
        if (!cancelled) setVerifyTries((t) => t + 1);
      }
    }, INTERVAL_MS);

    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [bindState, verifyTries, adapter.id, refetchAdapters]);

  // If bind is connected, show connected state
  if (bindState === 'connected') {
    return (
      <div
        className="border-feedback-success/30 bg-feedback-success/5 rounded-lg border p-4"
        data-testid={`pending-adapter-row-${adapter.id}`}
      >
        <div className="flex items-center gap-2">
          <Check className="text-feedback-success h-4 w-4" />
          <p className="text-text-primary text-sm font-medium">
            <span className="font-mono">{adapter.intended_profile_name ?? adapter.name}</span> connected
          </p>
        </div>
        <p className="text-text-muted mt-1 text-xs">
          Profile bound to adapter{' '}
          <span className="font-mono">{adapter.id}</span>
        </p>
      </div>
    );
  }

  // If adapter is APPROVED (after successful approve), show bind action.
  if (adapter.status === 'approved' && adapter.intended_profile_name) {
    return (
      <div
        className="border-feedback-success/20 bg-surface rounded-lg border p-4"
        data-testid={`pending-adapter-row-${adapter.id}`}
      >
        <div className="flex items-center gap-2 mb-2">
          <Check className="text-feedback-success h-4 w-4" />
          <span className="text-text-primary font-mono text-sm font-medium">{adapter.id}</span>
          <span className="text-mono-sm bg-tier-green-tint text-status-open inline-flex items-center rounded-full px-2 py-0.5 font-semibold">
            approved
          </span>
        </div>
        <p className="text-text-secondary text-sm">
          Approved — bind profile <span className="font-mono">{adapter.intended_profile_name}</span> to connect.
        </p>

        <div className="mt-3 flex items-center gap-2">
          {bindState === 'error' ? (
            <>
              <Button
                type="button"
                variant="default"
                onClick={() => { void onBind(); }}
                data-testid={`adapter-bind-${adapter.id}`}
              >
                <Link aria-hidden="true" size={14} />
                Retry Bind
              </Button>
              <p className="text-feedback-danger text-xs" role="alert" data-testid={`adapter-bind-error-${adapter.id}`}>
                {bindError}
              </p>
            </>
          ) : (
            <Button
              type="button"
              variant="default"
              onClick={() => { void onBind(); }}
              disabled={bindState !== 'ready'}
              data-testid={`adapter-bind-${adapter.id}`}
            >
              {bindState === 'binding' ? (
                <>
                  <Loader2 aria-hidden="true" className="mr-1 h-3 w-3 animate-spin" />
                  Binding…
                </>
              ) : bindState === 'verifying' ? (
                <>
                  <Loader2 aria-hidden="true" className="mr-1 h-3 w-3 animate-spin" />
                  Verifying…
                </>
              ) : (
                <>
                  <Link aria-hidden="true" size={14} />
                  Bind{' '}
                  <span className="font-mono">{adapter.intended_profile_name}</span>
                </>
              )}
            </Button>
          )}
        </div>
        {bindState === 'verifying' && (
          <p className="text-text-muted mt-2 text-xs">
            Confirming profile binding with the server…
          </p>
        )}
        {bindError && bindState === 'error' && (
          <p
            className="text-feedback-danger mt-2 text-xs"
            role="alert"
            data-testid={`adapter-bind-error-${adapter.id}`}
          >
            {bindError}
          </p>
        )}
      </div>
    );
  }

  // Default: PENDING adapter with approve/reject controls.
  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`pending-adapter-row-${adapter.id}`}
    >
      {/* Header: id + status pill */}
      <div className="flex items-center gap-2 mb-2">
        <Puzzle size={16} aria-hidden className="text-text-secondary shrink-0" />
        <span className="text-text-primary font-mono text-sm font-medium">{adapter.id}</span>
        <span
          className="text-mono-sm bg-surface-sunken text-text-muted inline-flex items-center rounded-full px-2 py-0.5 font-semibold"
          data-testid={`adapter-status-${adapter.id}`}
        >
          {adapter.status}
        </span>
      </div>

      {/* Material fields — truthful, from the API payload */}
      <div className="mt-2 space-y-1">
        <p className="text-text-secondary text-sm">
          Name:{' '}
          <span className="text-text-primary font-medium">{adapter.name}</span>
        </p>
        {adapter.intended_profile_name && (
          <p className="text-text-secondary text-sm">
            Intended profile:{' '}
            <span className="text-text-primary font-medium" data-testid={`adapter-intended-profile-${adapter.id}`}>
              {adapter.intended_profile_name}
            </span>
          </p>
        )}
        <p className="text-text-secondary text-sm">
          Executable:{' '}
          <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
            {adapter.executable}
          </code>
        </p>
        <p className="text-text-muted text-xs break-all">
          SHA-256:{' '}
          <code
            className="font-mono"
            data-testid={`adapter-hash-${adapter.id}`}
          >
            {adapter.executable_hash}
          </code>
        </p>
        <p className="text-text-muted text-xs">
          Version:{' '}
          <span className="font-mono">{adapter.version}</span>
        </p>
        <p className="text-text-muted text-xs">
          Workspace adapter:{' '}
          <span className="font-mono">{adapter.workspace_adapter}</span>
        </p>
        {adapter.capabilities.length > 0 && (
          <p className="text-text-muted text-xs">
            Capabilities:{' '}
            <span className="font-mono">
              {adapter.capabilities.join(', ')}
            </span>
          </p>
        )}
        <p className="text-text-muted text-xs">
          Contract version:{' '}
          <span className="font-mono">{adapter.contract_version}</span>
        </p>
      </div>

      {/* Approve / Reject actions */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {/* Approve */}
        {approveConfirming ? (
          <>
            <p className="w-full text-text-secondary text-xs mb-1">
              Confirm approval of adapter{' '}
              <code className="font-mono bg-surface-sunken rounded px-1">
                {shortHash(adapter.executable_hash)}
              </code>
            </p>
            <Button
              type="button"
              variant="default"
              onClick={() => { void onApprove(); }}
              disabled={approve.isPending}
              data-testid={`adapter-confirm-approve-${adapter.id}`}
            >
              {approve.isPending ? 'Approving…' : 'Confirm approve'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => { setApproveConfirming(false); setError(null); }}
              disabled={approve.isPending}
            >
              Cancel
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variant="default"
            onClick={() => setApproveConfirming(true)}
            data-testid={`adapter-approve-${adapter.id}`}
          >
            <Check aria-hidden="true" size={14} />
            Approve
          </Button>
        )}

        {/* Reject */}
        {rejectConfirming ? (
          <>
            <p className="w-full text-text-secondary text-xs mb-1">
              Confirm rejection of adapter{' '}
              <code className="font-mono bg-surface-sunken rounded px-1">
                {shortHash(adapter.executable_hash)}
              </code>
            </p>
            <Button
              type="button"
              variant="destructive"
              onClick={() => { void onReject(); }}
              disabled={reject.isPending}
              data-testid={`adapter-confirm-reject-${adapter.id}`}
            >
              {reject.isPending ? 'Rejecting…' : 'Confirm reject'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => { setRejectConfirming(false); setError(null); }}
              disabled={reject.isPending}
            >
              Cancel
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variant="secondary"
            onClick={() => setRejectConfirming(true)}
            data-testid={`adapter-reject-${adapter.id}`}
          >
            <Trash2 aria-hidden="true" size={14} />
            Reject
          </Button>
        )}
      </div>

      {/* Error display */}
      {error && (
        <p
          className="text-feedback-danger mt-2 flex items-center gap-1.5 text-sm"
          role="alert"
          data-testid={`adapter-action-error-${adapter.id}`}
        >
          <XCircle size={14} aria-hidden />
          {error}
        </p>
      )}
    </div>
  );
}

/* ── Section component ── */

export function PendingAdaptersSection(): JSX.Element {
  const query = useAdapters();
  const adapters = query.data ?? [];
  // Show PENDING adapters (awaiting approval) AND approved adapters eligible
  // for binding. The bind action is shown by PendingAdapterRow for approved
  // adapters with an intended_profile_name.
  const pending = adapters.filter(
    (a) => a.status === 'pending' || (a.status === 'approved' && a.eligibility === 'ready_to_bind'),
  );

  return (
    <section className="space-y-3" data-testid="pending-adapters-section">
      <div>
        <h3 className="text-text-primary text-sm font-semibold">Pending Adapter Approvals</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Adapters awaiting founder approval. Approve to make them available for
          profile binding or reject to remove them.
        </p>
      </div>

      {query.isLoading && (
        <p className="text-text-secondary text-sm">Loading adapters…</p>
      )}

      {query.isError && (
        <p className="text-feedback-danger text-sm" role="alert">
          Could not load custom adapters.
          {query.error?.message ? ` ${query.error.message}` : ''}
        </p>
      )}

      {query.data &&
        (pending.length === 0 ? null : (
          <div className="space-y-3" data-testid="pending-adapter-rows">
            {pending.map((adapter) => (
              <PendingAdapterRow key={adapter.id} adapter={adapter} />
            ))}
          </div>
        ))}
    </section>
  );
}

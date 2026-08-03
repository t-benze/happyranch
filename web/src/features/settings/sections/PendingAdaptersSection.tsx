/**
 * PendingAdaptersSection — the Settings ▸ Executors founder-only pending
 * adapter approvals area (THR-107 seq334).
 *
 * Rendered ABOVE Custom CLIs (CustomProfilesSection). Lists ONLY adapters whose
 * status is exactly PENDING. Approved adapters — including already_bound,
 * ready_to_bind, and recovery_ready records — no longer appear here; they are
 * surfaced as Custom CLI rows or CLI-level recovery affordances in
 * CustomProfilesSection instead. This keeps the approval queue as the sole
 * founder approval surface and prevents approved Connected/recovery cards from
 * leaking into the pending queue after refetch.
 *
 * **THR-107 seq237**: For adapters with an ``intended_profile_name``,
 * Approve now atomically approves AND creates/binds the named custom profile
 * in one server transaction. After success we refetch both the adapter list
 * (so the card leaves the pending queue) and the custom-profiles list (so the
 * newly connected CLI appears exactly once under Custom CLIs).
 *
 * HONESTY FENCE: only fields the API returns are rendered. The server is the
 * single source of truth for eligibility and snapshot validity.
 * Onboarding NEVER renders this section — it is Settings-only.
 */
import { useState, useCallback } from 'react';
import { Check, XCircle, Puzzle, Trash2 } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  ADAPTERS_KEY,
  useAdapters,
  useApproveAdapter,
  useRejectAdapter,
  type AdapterEntry,
} from '@/hooks/adapters';
import { RUNTIME_PROFILES_KEY } from '@/hooks/runtime-executors';
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
    // THR-107 seq244: include dependency manifest facts in the action snapshot
    dependency_manifest_version: adapter.dependency_manifest_version,
    dependencies: adapter.dependencies?.length ? adapter.dependencies : null,
  };
}

/** Build the exact snapshot body for rejection (same shape as approval). */
function buildRejectBody(adapter: AdapterEntry) {
  return buildApproveBody(adapter);
}

/* ── Single pending adapter row ── */

function PendingAdapterRow({ adapter }: { adapter: AdapterEntry }): JSX.Element {
  const [approveConfirming, setApproveConfirming] = useState(false);
  const [rejectConfirming, setRejectConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approve = useApproveAdapter();
  const reject = useRejectAdapter();
  const qc = useQueryClient();

  // After approval succeeds, force a refetch of both management queries so the
  // card leaves the pending queue AND the newly connected CLI appears under
  // Custom CLIs (the profiles query carries a 10s staleTime).
  const refetchLists = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    void qc.invalidateQueries({ queryKey: RUNTIME_PROFILES_KEY });
  }, [qc]);

  const onApprove = async (): Promise<void> => {
    setError(null);
    try {
      await approve.mutateAsync({
        id: adapter.id,
        body: buildApproveBody(adapter),
      });
      refetchLists();
      // Reset approve state — the row will be removed by the parent filter on
      // the next render.
      setApproveConfirming(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        refetchLists();
      } else {
        setError(errMessage(err, 'Could not approve this CLI.'));
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
      void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
      // Reset reject state — the adapter row will be removed by the parent
      // filtering on next render.
      setRejectConfirming(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
      } else {
        setError(errMessage(err, 'Could not reject this CLI.'));
      }
    }
  };

  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`pending-adapter-row-${adapter.id}`}
    >
      {/* Header: id + status pill */}
      <div className="mb-2 flex items-center gap-2">
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
          Workspace CLI:{' '}
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
        {/* Approve (seq237: approve and connect for intended-profile adapters) */}
        {approveConfirming ? (
          <>
            <p className="text-text-secondary mb-1 w-full text-xs">
              {adapter.intended_profile_name
                ? `Confirm approval and connection — this will bind profile ${adapter.intended_profile_name}`
                : 'Confirm approval'}{' '}
              <code className="bg-surface-sunken rounded px-1 font-mono">
                {adapter.executable_hash}
              </code>
            </p>
            <Button
              type="button"
              variant="default"
              onClick={() => { void onApprove(); }}
              disabled={approve.isPending}
              data-testid={`adapter-confirm-approve-${adapter.id}`}
            >
              {approve.isPending ? 'Approving…' : adapter.intended_profile_name ? 'Confirm approve & connect' : 'Confirm approve'}
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
            {adapter.intended_profile_name ? 'Approve & connect' : 'Approve'}
          </Button>
        )}

        {/* Reject */}
        {rejectConfirming ? (
          <>
            <p className="text-text-secondary mb-1 w-full text-xs">
              Confirm rejection{' '}
              <code className="bg-surface-sunken rounded px-1 font-mono">
                {adapter.executable_hash}
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
  // seq334: the approval queue contains ONLY adapters whose status is exactly
  // pending. Approved records (already_bound, ready_to_bind, recovery_ready)
  // are surfaced in the Custom CLIs area, not here.
  const pending = adapters.filter((a) => a.status === 'pending');

  return (
    <section className="space-y-3" data-testid="pending-adapters-section">
      <div>
        <h3 className="text-text-primary text-sm font-semibold">Pending CLI approvals</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Custom CLIs awaiting founder approval. Approving a named custom CLI atomically
          approves and connects its profile in one action.
        </p>
      </div>

      {query.isLoading && (
        <p className="text-text-secondary text-sm">Loading pending approvals…</p>
      )}

      {query.isError && (
        <p className="text-feedback-danger text-sm" role="alert">
          Could not load pending approvals.
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

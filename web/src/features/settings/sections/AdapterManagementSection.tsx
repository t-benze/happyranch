/**
 * AdapterManagementSection — the Settings ▸ Executors custom-adapter
 * MANAGEMENT list (THR-107 TASK-3792). Consumes the adapter list
 * (GET /runtime/adapters) and remove (DELETE /runtime/adapters/{id})
 * via the adapters hooks.
 *
 * Renders one row per adapter — id, name, intended profile, executable,
 * hash, status — and gives each APPROVED adapter a guarded remove
 * affordance. PENDING adapters are shown without a remove affordance.
 * The remove affordance is suppressed when a custom runtime profile
 * is bound to the adapter (the server rejects it anyway, and hiding
 * it is more truthful).
 *
 * HONESTY FENCE: only fields the API actually returns are rendered.
 * eligibility (server-authoritative) drives the remove-affordance
 * visibility. No invented fields.
 */
import { useState } from 'react';
import { Trash2, XCircle, Puzzle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  ADAPTERS_KEY,
  useAdapters,
  useRemoveAdapter,
  type AdapterEntry,
  type RemoveAdapterRequest,
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

/** Build the exact snapshot body the server requires for removal. */
function buildRemoveBody(adapter: AdapterEntry): RemoveAdapterRequest {
  return {
    executable: adapter.executable,
    executable_hash: adapter.executable_hash,
    version: adapter.version,
    capabilities: adapter.capabilities,
    contract_version: adapter.contract_version,
    workspace_adapter: adapter.workspace_adapter,
    name: adapter.name,
    intended_profile_name: adapter.intended_profile_name,
  };
}

/** Whether the adapter is eligible for removal (APPROVED + not bound to a profile). */
function canRemove(adapter: AdapterEntry): boolean {
  if (adapter.status !== 'approved') return false;
  // already_bound means a profile references it — server will reject removal
  if (adapter.eligibility === 'already_bound') return false;
  return true;
}

function AdapterRow({ adapter }: { adapter: AdapterEntry }): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = useRemoveAdapter();
  const qc = useQueryClient();
  const removable = canRemove(adapter);

  const onConfirmRemove = async (): Promise<void> => {
    setError(null);
    try {
      await remove.mutateAsync({
        id: adapter.id,
        body: buildRemoveBody(adapter),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Already gone — force refetch and collapse.
        void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
      } else {
        setError(errMessage(err, 'Could not remove this adapter.'));
        return;
      }
    }
    setConfirming(false);
  };

  const statusPill = adapter.status === 'approved'
    ? 'bg-tier-green-tint text-status-open'
    : 'bg-surface-sunken text-text-muted';

  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`adapter-row-${adapter.id}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Puzzle size={16} aria-hidden className="text-text-secondary shrink-0" />
          <span className="text-text-primary font-mono text-sm font-medium">{adapter.id}</span>
          <span
            className={`text-mono-sm inline-flex items-center rounded-full px-2 py-0.5 font-semibold ${statusPill}`}
            data-testid={`adapter-status-${adapter.id}`}
          >
            {adapter.status}
          </span>
        </div>
      </div>

      <div className="mt-2 space-y-1">
        <p className="text-text-secondary text-sm">
          Name:{' '}
          <span className="text-text-primary font-medium">{adapter.name}</span>
        </p>
        <p className="text-text-secondary text-sm">
          Executable:{' '}
          <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
            {adapter.executable}
          </code>
        </p>
        <p className="text-text-muted text-xs break-all">
          Hash:{' '}
          <code className="font-mono">{adapter.executable_hash}</code>
        </p>
        {adapter.intended_profile_name && (
          <p className="text-text-secondary text-sm">
            Intended profile:{' '}
            <span className="text-text-primary font-medium">{adapter.intended_profile_name}</span>
          </p>
        )}
        {adapter.eligibility !== null && (
          <p className="text-text-muted text-xs">
            Eligibility: {adapter.eligibility}
          </p>
        )}
      </div>

      {/* Guarded remove: first click arms a confirm/cancel step. */}
      {removable && (
        <div className="mt-3 flex items-center gap-2">
          {confirming ? (
            <>
              <Button
                type="button"
                variant="destructive"
                onClick={() => void onConfirmRemove()}
                disabled={remove.isPending}
                data-testid={`adapter-confirm-remove-${adapter.id}`}
              >
                {remove.isPending ? 'Removing…' : 'Confirm remove'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setConfirming(false);
                  setError(null);
                }}
                disabled={remove.isPending}
              >
                Cancel
              </Button>
            </>
          ) : (
            <Button
              type="button"
              variant="secondary"
              onClick={() => setConfirming(true)}
              data-testid={`adapter-remove-${adapter.id}`}
            >
              <Trash2 aria-hidden="true" size={14} />
              Remove
            </Button>
          )}
        </div>
      )}

      {error && (
        <p
          className="text-feedback-danger mt-2 flex items-center gap-1.5 text-sm"
          role="alert"
          data-testid={`adapter-remove-error-${adapter.id}`}
        >
          <XCircle size={14} aria-hidden />
          {error}
        </p>
      )}
    </div>
  );
}

export function AdapterManagementSection(): JSX.Element {
  const query = useAdapters();
  const adapters = query.data ?? [];

  return (
    <section className="space-y-3" data-testid="adapter-management-section">
      <div>
        <h3 className="text-text-primary text-sm font-semibold">Custom Adapters</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Registered custom adapter executables. APPROVED adapters without a bound
          profile can be removed — remove the profile first from Custom CLIs if
          it is still referenced.
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
        (adapters.length === 0 ? (
          <p
            className="text-text-muted flex items-center gap-1.5 text-sm"
            data-testid="adapter-list-empty"
          >
            No custom adapters registered.
          </p>
        ) : (
          <div className="space-y-3" data-testid="adapter-rows">
            {adapters.map((adapter) => (
              <AdapterRow key={adapter.id} adapter={adapter} />
            ))}
          </div>
        ))}
    </section>
  );
}

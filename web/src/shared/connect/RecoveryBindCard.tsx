/**
 * RecoveryBindCard — shared bind → server poll → durable connected lifecycle
 * (THR-107 TASK-3784 durable bind, extracted for TASK-3805 fix-forward).
 *
 * This is the SINGLE canonical bind recovery implementation for
 * Settings ▸ Executors PendingAdaptersSection — it invokes the same
 * bind → server poll → durable connected logic.
 *
 * Bind lifecycle:
 *   ready → binding (POST /bind-profile) → verifying (poll every 1.5s)
 *   → connected (server reports eligibility === 'already_bound')
 *   OR error (bind failed OR verification timed out)
 */
import { useEffect, useState } from 'react';
import { Check } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Spinner } from './Spinner';
import type { RecoverableAdapter } from './useRuntimeConnect';

/* ── Bind completion states ── */

export type BindCardState =
  | 'ready'
  | 'binding'
  | 'verifying'     // bind API returned success — now refetching server state
  | 'connected'     // server confirmed profile exists with correct adapter id
  | 'error';

/* ── Recovery bind card — bind then refetch to verify server state ── */

export function RecoveryBindCard({
  adapter,
  onBindSuccess,
}: {
  adapter: RecoverableAdapter;
  onBindSuccess: (name: string, executable: string) => void;
}): JSX.Element {
  const [state, setState] = useState<BindCardState>('ready');
  const [error, setError] = useState('');
  const [verifyTries, setVerifyTries] = useState(0);

  const bind = async (): Promise<void> => {
    setState('binding');
    setError('');
    try {
      const { adapters } = await import('@/lib/api');
      await adapters.bindAdapterProfile(adapter.adapterId, {
        profile_name: adapter.profileName,
      });
      // Bind API returned success — now refetch to verify server state.
      // Do NOT call onBindSuccess until server confirms the profile
      // exists with command_adapter_id custom-adapter:<exact adapter id>.
      setState('verifying');
      setVerifyTries(0);
    } catch (e: unknown) {
      const msg =
        e instanceof Error
          ? e.message
          : 'Bind failed. Retry or contact the founder.';
      setError(msg);
      setState('error');
    }
  };

  // After bind success, poll the adapter list endpoint to verify the
  // server now reports eligibility === 'already_bound' for this adapter.
  useEffect(() => {
    if (state !== 'verifying') return;
    const MAX_TRIES = 6;
    const INTERVAL_MS = 1500;

    if (verifyTries >= MAX_TRIES) {
      setError('Bind succeeded but server verification timed out. Refresh the page — the profile may already be connected.');
      setState('error');
      return;
    }

    const timer = window.setTimeout(async () => {
      try {
        const { adapters } = await import('@/lib/api');
        const adapterList = await adapters.listAdapters();
        const entry = adapterList.find((a) => a.id === adapter.adapterId);
        if (entry && entry.eligibility === 'already_bound') {
          // Server confirmed: profile exists and is bound to THIS adapter.
          setState('connected');
          onBindSuccess(adapter.profileName, adapter.executable);
        } else {
          setVerifyTries((t) => t + 1);
        }
      } catch {
        setVerifyTries((t) => t + 1);
      }
    }, INTERVAL_MS);

    return () => window.clearTimeout(timer);
  }, [state, verifyTries, adapter.adapterId, adapter.profileName, adapter.executable, onBindSuccess]);

  if (state === 'connected') {
    return (
      <div className="border-feedback-success/30 bg-feedback-success/5 rounded-lg border p-4">
        <div className="flex items-center gap-2">
          <Check className="text-feedback-success h-4 w-4" />
          <p className="text-text-primary text-sm font-medium">
            <span className="font-mono">{adapter.profileName}</span> connected
          </p>
        </div>
        <p className="text-text-muted mt-1 text-xs">
          Profile bound to adapter <span className="font-mono">{adapter.adapterId}</span>
        </p>
      </div>
    );
  }

  return (
    <div className="border-border-default bg-surface rounded-lg border p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-text-primary text-sm font-medium">
            <span className="font-mono">{adapter.profileName}</span>
          </p>
          <p className="text-text-muted mt-0.5 text-xs break-all font-mono">
            {adapter.executable}
          </p>
          <p className="text-text-muted mt-0.5 text-xs">
            Workspace adapter:{' '}
            <span className="font-mono">{adapter.workspaceAdapter}</span>
          </p>
        </div>
        <Button
          onClick={() => { void bind(); }}
          disabled={state !== 'ready'}
          size="sm"
        >
          {state === 'binding' ? (
            <>
              <Spinner className="mr-1 h-3 w-3" />
              Binding…
            </>
          ) : state === 'verifying' ? (
            <>
              <Spinner className="mr-1 h-3 w-3" />
              Verifying…
            </>
          ) : (
            <>
              Bind{' '}
              <span className="font-mono">{adapter.profileName}</span>
            </>
          )}
        </Button>
      </div>
      {state === 'error' && (
        <p className="text-feedback-danger mt-2 text-xs" role="alert">
          {error}
        </p>
      )}
      {state === 'verifying' && (
        <p className="text-text-muted mt-2 text-xs">
          Confirming profile binding with the server…
        </p>
      )}
    </div>
  );
}

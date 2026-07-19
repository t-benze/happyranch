/**
 * CustomProfilesSection — the Settings ▸ Executors custom-profile MANAGEMENT
 * list (THR-107 S4b). Consumes the S4a list/remove backend
 * (GET/DELETE /executors/runtime/profiles) via the runtime-executors hooks.
 *
 * The registered-binary registry (ExecutorBinariesSection) covers the four
 * built-in kinds; this section covers the CUSTOM CLIs a user connected via the
 * shared "Connect a CLI" custom flow. It renders one row per profile — name,
 * executable, and the present/path health signal — and gives each a guarded
 * remove affordance. On removal the profiles query is invalidated so the list
 * updates (same cache-invalidation pattern as the binary registry, S3).
 *
 * HONESTY FENCE (THR-061 §D): only the fields the API actually returns are
 * rendered — name, command (executable), adapter, present, path. No invented
 * status columns. `present` is the server-computed /health/prereqs signal
 * (PATH alone is NOT present), mirrored here as the row's health pill.
 */
import { useState } from 'react';
import { CheckCircle2, Terminal, Trash2, XCircle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  RUNTIME_PROFILES_KEY,
  useRemoveRuntimeProfile,
  useRuntimeProfiles,
  type RuntimeProfileEntry,
} from '@/hooks/runtime-executors';
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

/** Present/path health pill — mirrors ExecutorBinariesSection's ValidityPill.
 *  `present` is the same signal /health/prereqs uses (a registered, valid,
 *  machine-local binary path), so PATH-defined-but-absent reads as NOT present. */
function HealthPill({ present }: { present: boolean }): JSX.Element {
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-semibold tabular-nums ${
        present
          ? 'text-status-open bg-tier-green-tint'
          : 'text-status-archived border-border-default border bg-transparent'
      }`}
      data-testid="profile-health"
      data-present={present}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />
      {present ? 'on this machine' : 'not on this machine'}
    </span>
  );
}

function ProfileRow({ profile }: { profile: RuntimeProfileEntry }): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = useRemoveRuntimeProfile();
  const qc = useQueryClient();

  const onConfirmRemove = async (): Promise<void> => {
    setError(null);
    try {
      await remove.mutateAsync(profile.name);
    } catch (err) {
      // 404 = the name is no longer a custom profile (removed out from under
      // us). The end state we wanted — profile gone — already holds, so treat
      // it as success: force a refetch (the mutation's onSuccess didn't run)
      // and collapse. Any other failure surfaces inline, no opaque error.
      if (err instanceof ApiError && err.status === 404) {
        void qc.invalidateQueries({ queryKey: RUNTIME_PROFILES_KEY });
      } else {
        setError(errMessage(err, 'Could not remove this profile.'));
        return;
      }
    }
    setConfirming(false);
  };

  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`profile-row-${profile.name}`}
      data-present={profile.present}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Terminal size={16} aria-hidden className="text-text-secondary shrink-0" />
          <span className="text-text-primary font-mono text-sm font-medium">{profile.name}</span>
        </div>
        <HealthPill present={profile.present} />
      </div>

      <div className="mt-2">
        {profile.command ? (
          <p className="text-text-secondary text-sm">
            Executable:{' '}
            <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
              {profile.command}
            </code>
            {profile.adapter ? (
              <span className="text-text-muted"> · adapter {profile.adapter}</span>
            ) : null}
          </p>
        ) : (
          <p className="text-text-muted text-sm">No executable recorded for this profile.</p>
        )}
        {profile.present && profile.path ? (
          <p className="text-text-secondary mt-1 text-sm">
            Path:{' '}
            <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
              {profile.path}
            </code>
          </p>
        ) : null}
      </div>

      {/* Guarded remove: first click arms a confirm/cancel step (S3 has no
          confirm-before-destructive primitive to reuse). */}
      <div className="mt-3 flex items-center gap-2">
        {confirming ? (
          <>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void onConfirmRemove()}
              disabled={remove.isPending}
              data-testid={`profile-confirm-remove-${profile.name}`}
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
            data-testid={`profile-remove-${profile.name}`}
          >
            <Trash2 aria-hidden="true" size={14} />
            Remove
          </Button>
        )}
      </div>

      {error && (
        <p
          className="text-feedback-danger mt-2 flex items-center gap-1.5 text-sm"
          role="alert"
          data-testid={`profile-remove-error-${profile.name}`}
        >
          <XCircle size={14} aria-hidden />
          {error}
        </p>
      )}
    </div>
  );
}

export function CustomProfilesSection(): JSX.Element {
  const query = useRuntimeProfiles();
  const profiles = query.data?.profiles ?? [];

  return (
    <section className="space-y-3" data-testid="custom-profiles-section">
      <div>
        <h3 className="text-text-primary text-sm font-semibold">Custom CLIs</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Custom executor profiles you connected. Removing one deletes it from
          the machine-global runtime store.
        </p>
      </div>

      {query.isLoading && (
        <p className="text-text-secondary text-sm">Loading custom CLIs…</p>
      )}

      {query.isError && (
        <p className="text-feedback-danger text-sm" role="alert">
          Could not load custom executor profiles.
          {query.error?.message ? ` ${query.error.message}` : ''}
        </p>
      )}

      {query.data &&
        (profiles.length === 0 ? (
          <p
            className="text-text-muted flex items-center gap-1.5 text-sm"
            data-testid="custom-profiles-empty"
          >
            <CheckCircle2 size={14} aria-hidden className="shrink-0" />
            No custom CLIs registered — connect one with{' '}
            <span className="font-medium">Connect a CLI</span> below.
          </p>
        ) : (
          <div className="space-y-3" data-testid="custom-profile-rows">
            {profiles.map((profile) => (
              <ProfileRow key={profile.name} profile={profile} />
            ))}
          </div>
        ))}
    </section>
  );
}

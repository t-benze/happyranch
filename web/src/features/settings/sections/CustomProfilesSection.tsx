/**
 * CustomProfilesSection — the Settings ▸ Executors custom-profile MANAGEMENT
 * list (THR-107 S4b + seq334).
 *
 * Consumes the S4a list/remove backend (GET/DELETE /executors/runtime/profiles)
 * via the runtime-executors hooks. Adapter-backed profiles join the approved
 * adapter executable from GET /runtime/adapters by exact id, so a
 * command_adapter_id: custom-adapter:<id> profile with a null profile.command
 * still truthfully shows its Executable.
 *
 * Approved-unbound adapters that require recovery (ready_to_bind or
 * recovery_ready eligibility) are surfaced as CLI-level recovery affordances
 * inside this section, not in a separate adapter list or the pending queue.
 * The recovery UI reuses the existing bind mutation; Settings remains the
 * approval authority and onboarding remains status-only.
 *
 * HONESTY FENCE (THR-061 §D): only the fields the API actually returns are
 * rendered — name, command (executable), adapter, present, path. No invented
 * status columns. `present`/`path` derive from the machine-local binary
 * registry (executors.json) keyed by the profile name — the same gating as
 * built-ins (THR-107 seq155). No PATH-based fallback is used.
 */
import { useState } from 'react';
import { CheckCircle2, Terminal, Trash2, XCircle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { ApiError } from '@/lib/api';
import {
  ADAPTERS_KEY,
  useAdapters,
  useBindAdapterProfile,
  type AdapterEntry,
} from '@/hooks/adapters';
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

/** Extract the stable adapter id from a profile that backs a custom CLI. */
function adapterIdFromProfile(profile: RuntimeProfileEntry): string | null {
  if (!profile.command_adapter_id || profile.command_adapter_id === 'generic-cli') {
    return null;
  }
  const prefix = 'custom-adapter:';
  return profile.command_adapter_id.startsWith(prefix)
    ? profile.command_adapter_id.slice(prefix.length)
    : null;
}

/** Resolve the adapter entry that backs a given profile, if any. */
function findAdapter(
  profile: RuntimeProfileEntry,
  adapters: AdapterEntry[],
): AdapterEntry | undefined {
  const id = adapterIdFromProfile(profile);
  return id ? adapters.find((a) => a.id === id) : undefined;
}

/** Whether an adapter is approved and requires CLI-level recovery binding. */
function needsRecovery(adapter: AdapterEntry): boolean {
  return (
    adapter.status === 'approved' &&
    (adapter.eligibility === 'ready_to_bind' || adapter.eligibility === 'recovery_ready')
  );
}

/** Present/path health pill — mirrors ExecutorBinariesSection's ValidityPill.
 *  `present`/`path` derive from the machine-local binary registry
 *  (executors.json) keyed by the profile name — the same gating as
 *  built-ins (THR-107 seq155). No PATH-based fallback is used. */
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

function ProfileRow({
  profile,
  adapters,
}: {
  profile: RuntimeProfileEntry;
  adapters: AdapterEntry[];
}): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = useRemoveRuntimeProfile();
  const qc = useQueryClient();
  const adapter = findAdapter(profile, adapters);
  // Adapter-backed CLIs have profile.command === null; their truthful executable
  // lives on the approved adapter entry. Generic CLIs keep their stored command.
  const executable =
    adapter && profile.command === null ? adapter.executable : profile.command;

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
        {executable ? (
          <p className="text-text-secondary text-sm">
            Executable:{' '}
            <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
              {executable}
            </code>
            {/* seq334: adapter-backed rows show only the approved executable; do not
                surface implementation-term “adapter” or binding ids. Generic rows
                keep their existing presentation. */}
            {!adapter && (profile.workspace_adapter_id || profile.adapter) ? (
              <span className="text-text-muted"> · Workspace adapter: {profile.workspace_adapter_id || profile.adapter}</span>
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

/* ── CLI-level recovery affordance for approved-unbound adapters ── */

function RecoveryRow({
  adapter,
  onBound,
}: {
  adapter: AdapterEntry;
  onBound: () => void;
}): JSX.Element {
  const [profileName, setProfileName] = useState(adapter.intended_profile_name ?? '');
  const [error, setError] = useState<string | null>(null);
  const bind = useBindAdapterProfile();

  const doBind = async (): Promise<void> => {
    const name = profileName.trim();
    if (!name) return;
    setError(null);
    try {
      await bind.mutateAsync({ id: adapter.id, body: { profile_name: name } });
      onBound();
    } catch (err) {
      setError(errMessage(err, 'Could not connect this CLI. Retry or contact the founder.'));
    }
  };

  return (
    <div
      className="border-border-default bg-surface rounded-lg border p-4"
      data-testid={`cli-recovery-row-${adapter.id}`}
    >
      <div className="mb-2 flex items-center gap-2">
        <Terminal size={16} aria-hidden className="text-text-secondary shrink-0" />
        <span className="text-text-primary text-sm font-medium">Finish connecting this CLI</span>
      </div>
      <p className="text-text-secondary mb-3 text-sm">
        This custom CLI is approved but not yet connected. Bind it to a profile name so
        agents can launch it.
      </p>
      <div className="space-y-2">
        <Label htmlFor={`recovery-name-${adapter.id}`}>Profile name</Label>
        <Input
          id={`recovery-name-${adapter.id}`}
          value={profileName}
          onChange={(e) => setProfileName(e.target.value)}
          placeholder="e.g. my-custom-cli"
          disabled={bind.isPending}
          data-testid={`cli-recovery-name-${adapter.id}`}
        />
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button
          type="button"
          onClick={() => void doBind()}
          disabled={!profileName.trim() || bind.isPending}
          data-testid={`cli-recovery-bind-${adapter.id}`}
        >
          {bind.isPending ? 'Binding…' : `Bind ${profileName.trim() || '…'}`}
        </Button>
      </div>
      {error && (
        <p
          className="text-feedback-danger mt-2 text-sm"
          role="alert"
          data-testid={`cli-recovery-error-${adapter.id}`}
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function CustomProfilesSection(): JSX.Element {
  const query = useRuntimeProfiles();
  const profiles = query.data?.profiles ?? [];
  const adaptersQuery = useAdapters();
  const adapters = adaptersQuery.data ?? [];
  const qc = useQueryClient();

  // Adapter-backed CLIs already represented in the profiles list should not
  // also render a recovery row.
  const boundAdapterIds = new Set(
    profiles
      .map(adapterIdFromProfile)
      .filter((id): id is string => id !== null),
  );

  const recoveryAdapters = adapters.filter(
    (a) => needsRecovery(a) && !boundAdapterIds.has(a.id),
  );

  const onBound = (): void => {
    void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    void qc.invalidateQueries({ queryKey: RUNTIME_PROFILES_KEY });
  };

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
              <ProfileRow key={profile.name} profile={profile} adapters={adapters} />
            ))}
          </div>
        ))}

      {recoveryAdapters.length > 0 && (
        <div className="space-y-3" data-testid="cli-recovery-rows">
          {recoveryAdapters.map((adapter) => (
            <RecoveryRow key={adapter.id} adapter={adapter} onBound={onBound} />
          ))}
        </div>
      )}
    </section>
  );
}

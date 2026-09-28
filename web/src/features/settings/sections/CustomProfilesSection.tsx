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
import { ApiError } from '@/lib/api';
import { useAdapters, type AdapterEntry } from '@/hooks/adapters';
import {
  RUNTIME_PROFILES_KEY,
  useRemoveRuntimeProfile,
  useRuntimeProfiles,
  type RuntimeProfileEntry,
} from '@/hooks/runtime-executors';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';

/** A failure message held in state: a raw daemon/API diagnostic shown
 *  byte-for-byte, or a product-owned fallback held as a catalog key and
 *  translated at render so a visible message follows a locale switch. */
type HeldError = { raw: string } | { key: MessageKey };

/** Extract the raw diagnostic from an ApiError or any thrown Error; a thrown
 *  value that carries none falls back to the product-owned `fallback` key. */
function errMessage(err: unknown, fallback: MessageKey): HeldError {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return { raw: err.detail };
    if (err.detail && typeof err.detail === 'object' && 'msg' in err.detail) {
      return { raw: String((err.detail as { msg: unknown }).msg) };
    }
    return { raw: err.message };
  }
  if (err instanceof Error) return { raw: err.message };
  return { key: fallback };
}

/** Extract the stable adapter id from a profile that backs a custom CLI. */
function adapterIdFromProfile(profile: RuntimeProfileEntry): string | null {
  if (!profile.command_adapter_id) {
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

/** Present/path health pill — mirrors ExecutorBinariesSection's ValidityPill.
 *  `present`/`path` derive from the machine-local binary registry
 *  (executors.json) keyed by the profile name — the same gating as
 *  built-ins (THR-107 seq155). No PATH-based fallback is used. */
function HealthPill({ present }: { present: boolean }): JSX.Element {
  const { t } = useTranslation();
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
      {present
        ? t('settings.executors.profiles.onMachine')
        : t('settings.executors.profiles.notOnMachine')}
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
  const { t, render } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<HeldError | null>(null);
  const remove = useRemoveRuntimeProfile();
  const qc = useQueryClient();
  const adapter = findAdapter(profile, adapters);
  const executable = adapter?.executable ?? null;

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
        setError(errMessage(err, 'settings.executors.profiles.removeFailed'));
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
            {render('settings.executors.profiles.executable', {
              value: (
                <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
                  {executable}
                </code>
              ),
            })}
            {/* seq334: adapter-backed rows show only the approved executable; do not
                surface implementation-term “adapter” or binding ids. Generic rows
                keep their existing presentation. */}
          </p>
        ) : (
          <p className="text-text-muted text-sm">
            {t('settings.executors.profiles.noExecutable')}
          </p>
        )}
        {profile.present && profile.path ? (
          <p className="text-text-secondary mt-1 text-sm">
            {render('settings.executors.profiles.path', {
              value: (
                <code className="text-text-primary bg-surface-sunken rounded px-1 font-mono text-xs break-all">
                  {profile.path}
                </code>
              ),
            })}
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
              {remove.isPending
                ? t('settings.executors.profiles.removing')
                : t('settings.executors.profiles.confirmRemove')}
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
              {t('common.cancel')}
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
            {t('settings.executors.profiles.remove')}
          </Button>
        )}
      </div>

      {error !== null && (
        <p
          className="text-feedback-danger mt-2 flex items-center gap-1.5 text-sm"
          role="alert"
          data-testid={`profile-remove-error-${profile.name}`}
        >
          <XCircle size={14} aria-hidden />
          {'raw' in error ? error.raw : t(error.key)}
        </p>
      )}
    </div>
  );
}

export function CustomProfilesSection(): JSX.Element {
  const { t, render } = useTranslation();
  const query = useRuntimeProfiles();
  const profiles = query.data?.profiles ?? [];
  const adaptersQuery = useAdapters();
  const adapters = adaptersQuery.data ?? [];

  return (
    <section className="space-y-3" data-testid="custom-profiles-section">
      <div>
        <h3 className="text-text-primary text-sm font-semibold">
          {t('settings.executors.profiles.title')}
        </h3>
        <p className="text-text-secondary mt-1 text-sm">
          {t('settings.executors.profiles.description')}
        </p>
      </div>

      {query.isLoading && (
        <p className="text-text-secondary text-sm">
          {t('settings.executors.profiles.loading')}
        </p>
      )}

      {query.isError && (
        <p className="text-feedback-danger text-sm" role="alert">
          {query.error?.message
            ? t('settings.executors.profiles.loadErrorDetail', {
                detail: query.error.message,
              })
            : t('settings.executors.profiles.loadError')}
        </p>
      )}

      {query.data &&
        (profiles.length === 0 ? (
          <p
            className="text-text-muted flex items-center gap-1.5 text-sm"
            data-testid="custom-profiles-empty"
          >
            <CheckCircle2 size={14} aria-hidden className="shrink-0" />
            {render('settings.executors.profiles.empty', {
              action: (
                <span className="font-medium">{t('settings.executors.connectCli')}</span>
              ),
            })}
          </p>
        ) : (
          <div className="space-y-3" data-testid="custom-profile-rows">
            {profiles.map((profile) => (
              <ProfileRow key={profile.name} profile={profile} adapters={adapters} />
            ))}
          </div>
        ))}
    </section>
  );
}

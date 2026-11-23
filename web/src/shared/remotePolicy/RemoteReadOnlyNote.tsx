/**
 * Remote read-only notices (THR-387 S8-2b; v2.4 §4, C5-style scope copy).
 *
 * - readOnly  → "Read-only on this connection. Change this on {Host}."
 * - refused   → native answered `403 remote_read_only`: the same copy plus
 *               "Your changes are still here and weren't sent." Never the C3
 *               failed-save copy; the draft stays.
 * - refusedAction → the same refusal for a request without a draft (removal,
 *               retire/restore, a toggle): "Nothing was sent."
 * - hostWide  → a host-wide action (create org, runtime, custom CLI
 *               retry/forget) that is unavailable on a remote connection.
 * - unavailable → a page whose reads the Host does not share (Capacity):
 *               named, with reason and recovery, never fake values.
 *
 * Policy and target state live in `@/lib/remotePolicy`; this is presentation.
 */
import { Lock } from 'lucide-react';
import { useTranslation } from '@/hooks/i18n';

export function RemoteReadOnlyNote({
  host,
  variant = 'readOnly',
  className = 'mb-4',
}: {
  host: string;
  variant?: 'readOnly' | 'refused' | 'refusedAction' | 'hostWide';
  className?: string;
}): JSX.Element {
  const { t } = useTranslation();
  const key =
    variant === 'refused'
      ? 'remotePolicy.refused'
      : variant === 'refusedAction'
        ? 'remotePolicy.refusedAction'
        : variant === 'hostWide'
          ? 'remotePolicy.hostWide'
          : 'remotePolicy.readOnly';
  const refusal = variant === 'refused' || variant === 'refusedAction';
  return (
    <p
      role={refusal ? 'alert' : 'note'}
      data-testid="remote-read-only"
      data-variant={variant}
      className={`border-border-default bg-surface-sunken text-text-secondary flex min-w-0 items-start gap-2 rounded-sm border p-3 text-sm break-words ${className}`}
    >
      <Lock size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span className="min-w-0">{t(key, { host })}</span>
    </p>
  );
}

/** A Settings page the remote Host does not share (Capacity). */
export function RemoteUnavailablePage({
  host,
  page,
  scope,
}: {
  host: string;
  /** Localized page name. */
  page: string;
  /** Optional localized scope line (Capacity: all organizations on Host). */
  scope?: string;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <section className="p-6 md:p-8" data-testid="remote-unavailable">
      <div className="border-border-default bg-surface-raised rounded-md border p-5">
        <h3 className="text-text-primary flex min-w-0 items-start gap-2 text-sm font-semibold break-words">
          <Lock size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span className="min-w-0">{t('remotePolicy.unavailable.title', { page })}</span>
        </h3>
        {scope && <p className="text-text-muted mt-1 text-xs break-words">{scope}</p>}
        <p className="text-text-secondary mt-2 text-sm break-words">{t('remotePolicy.unavailable.body', { host, page })}</p>
      </div>
    </section>
  );
}

/**
 * Save result notice for one config owner (THR-387 S6-W2a, C1/C3).
 *
 * - failed  → C3: "Couldn't save {page} on {computer} · {org}. Your changes
 *             are still here and aren't saved." (+ optional server detail)
 * - unknown → the unknown copy and Check result (or the named unavailable
 *             reason when no reconciliation can run here)
 * - pending → "Saving {page} on {computer} · {org}…"
 * - failed by native `403 remote_read_only` (S8-2b) → the remote read-only
 *             copy ("Change this on {Host}"; changes kept, not sent), never C3.
 * Connection-attempt errors never use this notice.
 */
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useRemoteHost } from '@/lib/remotePolicy';
import { RemoteReadOnlyNote } from '@/shared/remotePolicy/RemoteReadOnlyNote';
import { ownerContextParams } from './ownerCopy';
import type { ConfigOwner } from './useConfigOwner';

export function OwnerSaveNotice({
  owner,
  detail,
  className = 'mb-4',
}: {
  owner: ConfigOwner;
  /** Verbatim daemon diagnostic for a failed save, if any. */
  detail?: string | null;
  className?: string;
}): JSX.Element | null {
  const { t } = useTranslation();
  // The refusal may arrive before native's app.stateChanged names the Host.
  const liveHost = useRemoteHost();
  const record = owner.record;
  if (!record) return null;
  const params = ownerContextParams(record, t);
  if (record.status === 'pending') {
    return (
      <p role="status" data-owner-notice="pending" className={`text-text-secondary text-sm break-words ${className}`}>
        {t('owners.save.pending', params)}
      </p>
    );
  }
  if (record.status === 'failed' && owner.remoteRefusal !== null) {
    return <RemoteReadOnlyNote host={liveHost ?? (owner.remoteRefusal || t('remotePolicy.theHost'))} variant="refused" className={className} />;
  }
  if (record.status === 'failed') {
    return (
      <div
        role="alert"
        data-owner-notice="failed"
        className={`border-tier-red bg-feedback-danger/10 text-tier-red rounded-sm border p-3 text-sm break-words ${className}`}
      >
        <p>{t('owners.save.failed', params)}</p>
        {detail && <p className="mt-1 text-xs">{detail}</p>}
      </div>
    );
  }
  if (record.status === 'unknown') {
    return (
      <div
        role="alert"
        data-owner-notice="unknown"
        className={`border-feedback-warning/40 bg-feedback-warning/5 text-text-primary rounded-sm border p-3 text-sm break-words ${className}`}
      >
        <p>{t('owners.save.unknown', params)}</p>
        {record.canCheck ? (
          <Button
            size="sm"
            variant="secondary"
            className="mt-2"
            disabled={owner.checking}
            onClick={() => void owner.check().catch(() => undefined)}
          >
            {t(owner.checking ? 'owners.save.checking' : 'owners.save.checkResult')}
          </Button>
        ) : (
          <p className="text-text-secondary mt-1 text-xs">{t('owners.save.checkUnavailable', params)}</p>
        )}
        {owner.checkFailed && !owner.checking && (
          <p className="text-feedback-danger mt-1 text-xs">{t('owners.save.checkFailed')}</p>
        )}
      </div>
    );
  }
  return null;
}

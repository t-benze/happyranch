import { useHasIdentityProvider, useIdentityPresentation } from '@/hooks/identities';
import { useTranslation } from '@/hooks/i18n';

/** Current enrichment only. Stored IDs, keys and attribution are never rewritten.
 * Bare pattern previews without data providers honestly render the supplied ID. */
export function IdentityName({ canonicalId, className }: { canonicalId: string; className?: string }): JSX.Element {
  const bound = useHasIdentityProvider();
  return bound ? <CurrentIdentityName canonicalId={canonicalId} className={className} /> : className ? <span className={className}>{canonicalId}</span> : <>{canonicalId}</>;
}
function CurrentIdentityName({ canonicalId, className }: { canonicalId: string; className?: string }): JSX.Element {
  const { label, identity, query } = useIdentityPresentation();
  const { t } = useTranslation();
  return <span className={className ?? 'break-words'} title={`${label(canonicalId)} — ${t(query.isLoading ? 'identity.loading' : query.isError ? 'identity.loadError' : identity(canonicalId)?.naming_status === 'ready' ? 'identity.currentLabel' : 'identity.unavailable')}`}>{label(canonicalId)}</span>;
}

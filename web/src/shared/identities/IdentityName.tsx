import { useHasIdentityProvider, useIdentityPresentation } from '@/hooks/identities';
import { useTranslation } from '@/hooks/i18n';

type IdentityNameProps = { canonicalId: string; className?: string; founderNameOnly?: boolean };
/** Current enrichment only. Stored IDs, keys and attribution are never rewritten.
 * Bare pattern previews without data providers honestly render the supplied ID.
 * `founderNameOnly` opts a Founder presentation site into the current name alone
 * (canonical ID when no ready custom name); agents always keep Name · ID. */
export function IdentityName({ canonicalId, className, founderNameOnly }: IdentityNameProps): JSX.Element {
  const bound = useHasIdentityProvider();
  return bound ? <CurrentIdentityName canonicalId={canonicalId} className={className} founderNameOnly={founderNameOnly} /> : className ? <span className={className}>{canonicalId}</span> : <>{canonicalId}</>;
}
function CurrentIdentityName({ canonicalId, className, founderNameOnly }: IdentityNameProps): JSX.Element {
  const { label, name, identity, query } = useIdentityPresentation();
  const { t } = useTranslation();
  const text = founderNameOnly && canonicalId === 'founder' ? name(canonicalId) : label(canonicalId);
  return <span className={className ?? 'break-words'} title={`${label(canonicalId)} — ${t(query.isLoading ? 'identity.loading' : query.isError ? 'identity.loadError' : identity(canonicalId)?.naming_status === 'ready' ? 'identity.currentLabel' : 'identity.unavailable')}`}>{text}</span>;
}

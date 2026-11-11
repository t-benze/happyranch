/**
 * Terminated-agents roster rows (THR-296 S4). The list itself still comes
 * from the existing terminated identity enrollments; selecting a row opens the
 * retained retired-source detail by permanent ID.
 */
import { useIdentityPresentation, identityInitials } from '@/hooks/identities';
import { useDensity } from '@/hooks/density';
import { useTranslation } from '@/hooks/i18n';

export function RetiredRoster({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }): JSX.Element {
  const { t } = useTranslation();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const presentation = useIdentityPresentation();
  const query = presentation.query;
  if (query.isLoading) return <p role="status" className="text-text-muted p-3 text-sm">{t('identity.loading')}</p>;
  if (query.isError) return <p role="alert" className="text-text-muted p-3 text-sm">{t('identity.loadError')}</p>;
  const retired = (query.data?.identities ?? []).filter((i) => i.kind === 'agent' && i.lifecycle === 'terminated');
  if (retired.length === 0) return <p className="text-text-muted p-3 text-sm">{t('identity.terminatedEmpty')}</p>;
  return (
    <>
      <ul className="flex flex-col gap-1 p-2">
        {retired.map((identity) => {
          const id = identity.canonical_id;
          const active = selected === id;
          const name = presentation.name(id);
          return (
            <li key={id}>
              <button
                type="button"
                aria-current={active ? 'true' : undefined}
                onClick={() => onSelect(id)}
                className={`hover:bg-surface-hover relative flex w-full items-center gap-2.5 overflow-hidden rounded-lg px-3 ${rowPad} text-left transition-colors ${
                  active ? 'bg-accent-muted border-accent-muted shadow-pasture-sm border' : 'bg-surface border-surface hover:border-border-default border'
                }`}
              >
                <span aria-hidden="true" className="bg-surface-sunken text-text-secondary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-xs font-semibold">
                  {identityInitials(name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-display text-text-primary block truncate text-sm font-medium">{name}</span>
                  <span className="text-text-muted block text-xs">{t('knowledge.roster.retiredUnknownDate')}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="text-text-muted px-4 pb-3 text-xs leading-relaxed">{t('knowledge.roster.hint')}</p>
    </>
  );
}

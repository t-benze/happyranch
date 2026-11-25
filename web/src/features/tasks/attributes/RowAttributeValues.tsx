/**
 * Current values of the filtered keys on a root row (PR5 `attribute_values`).
 * Sorted keys; the first three inline, the rest behind a keyboard-operable
 * disclosure. `0`, `false` and `""` are present values, never "Not set".
 */
import { useTranslation } from '@/hooks/i18n';
import type { AttributeDefinition, AttributePresence } from '@/lib/api/types';
import type { AttributeValues } from '@/lib/api/types';
import { attributeLabel } from './clauseText';

const INLINE = 3;

function ValueText({ presence }: { presence: AttributePresence }): JSX.Element {
  const { t } = useTranslation();
  if (!presence.present) return <span className="text-text-muted italic">{t('tasks.filters.attr.notSet')}</span>;
  const { value } = presence;
  if (typeof value === 'boolean') return <span className="text-text-primary">{t(value ? 'tasks.filters.attr.true' : 'tasks.filters.attr.false')}</span>;
  if (value === '') return <span className="text-text-muted">{t('tasks.filters.attr.emptyString')}</span>;
  return <bdi className="text-text-primary break-all" dir="auto">{String(value)}</bdi>;
}

export function RowAttributeValues({ values, defs }: {
  values: AttributeValues | undefined;
  defs: ReadonlyMap<string, AttributeDefinition>;
}): JSX.Element | null {
  const { t } = useTranslation();
  const keys = Object.keys(values ?? {}).sort();
  if (!values || keys.length === 0) return null;
  const one = (key: string) => (
    <span key={key} className="min-w-0 max-w-full">
      <span className="text-text-muted">{attributeLabel(key, defs)}</span>{' '}
      <ValueText presence={values[key]} />
    </span>
  );
  return (
    <div className="text-text-secondary flex min-w-0 flex-wrap gap-x-2.5 gap-y-1 px-2 pb-1.5 text-xs" data-testid="task-row-attribute-values">
      {keys.slice(0, INLINE).map(one)}
      {keys.length > INLINE && (
        <details className="min-w-0 max-w-full">
          <summary className="text-info cursor-pointer">{t('tasks.filters.attr.showMore', { n: keys.length - INLINE })}</summary>
          <span className="mt-1 flex flex-wrap gap-x-2.5 gap-y-1">{keys.slice(INLINE).map(one)}</span>
        </details>
      )}
    </div>
  );
}

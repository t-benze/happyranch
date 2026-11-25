/**
 * Attribute clause rows inside the Tasks filter form (design v1.2 frame F).
 *
 * Numeric definitions offer ≥ / ≤ / is set / is not set; string and boolean
 * definitions offer is set / is not set. Other prototype operators are hidden
 * (PR5 does not serve them). Discovery loading/failure only affects this
 * picker; applied clauses stay in effect on the list.
 */
import { Plus, RefreshCw, X } from 'lucide-react';
import { Badge } from '@/design-system/primitives/Badge';
import { Button } from '@/design-system/primitives/Button';
import { TONE_CLASS } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import type { AttributeDefinition } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';
import { needsValue, type DraftClause, type DraftError, type DraftOp } from './filterModel';

const OPS: Record<AttributeDefinition['type'], DraftOp[]> = {
  numeric: ['ge', 'le', 'set', 'unset'],
  string: ['set', 'unset'],
  boolean: ['set', 'unset'],
};
const OP_LABEL: Record<DraftOp, MessageKey> = {
  ge: 'tasks.filters.attr.op.ge',
  le: 'tasks.filters.attr.op.le',
  set: 'tasks.filters.attr.op.set',
  unset: 'tasks.filters.attr.op.unset',
};
const ERROR_KEY: Record<DraftError, MessageKey> = {
  pickAttribute: 'tasks.filters.attr.pickAttribute',
  valueRequired: 'tasks.filters.attr.invalid',
  invalidNumber: 'tasks.filters.attr.invalidNumber',
  duplicate: 'tasks.filters.attr.duplicate',
  conflict: 'tasks.filters.attr.conflict',
  tooMany: 'tasks.filters.attr.tooMany',
};
const INPUT = 'border-border-default bg-surface-raised text-text-primary block w-full min-w-0 rounded-sm border px-3 py-2 text-sm';

export interface DefinitionsState {
  loading: boolean;
  failed: boolean;
  retry: () => void;
  /** Every definition by key (archived included). */
  byKey: ReadonlyMap<string, AttributeDefinition>;
}

export function AttributeFilterClauses({ clauses, errors, onChange, onAdd, onRemove, includeArchived, onIncludeArchived, definitions }: {
  clauses: readonly DraftClause[];
  errors: ReadonlyMap<number, DraftError>;
  onChange: (id: number, patch: Partial<DraftClause>) => void;
  onAdd: () => void;
  onRemove: (id: number) => void;
  includeArchived: boolean;
  onIncludeArchived: (value: boolean) => void;
  definitions: DefinitionsState;
}): JSX.Element {
  const { t } = useTranslation();
  const all = [...definitions.byKey.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const active = all.filter((def) => !def.archived);
  const archived = all.filter((def) => def.archived);
  const ready = !definitions.loading && !definitions.failed;
  return (
    <div className="w-full min-w-0 space-y-2" data-testid="tasks-attr-clauses">
      <label className="text-text-secondary flex items-center gap-2 text-sm">
        <input type="checkbox" checked={includeArchived} onChange={(event) => onIncludeArchived(event.target.checked)} />
        {t('tasks.filters.attr.includeArchived')}
      </label>
      {definitions.loading && <p className="text-text-muted text-sm" role="status">{t('tasks.filters.attr.defsLoading')}</p>}
      {definitions.failed && (
        <div role="alert" className="border-feedback-danger bg-danger-soft flex flex-wrap items-center justify-between gap-2 rounded-md border p-3">
          <p className="text-text-primary min-w-0 text-sm">{t('tasks.filters.attr.defsError')}</p>
          <Button type="button" size="sm" variant="outline" onClick={definitions.retry}>
            <RefreshCw size={14} aria-hidden /> {t('tasks.filters.attr.defsRetry')}
          </Button>
        </div>
      )}
      {ready && active.length === 0 && (
        <p className="border-border-default bg-info-soft text-text-primary rounded-md border p-3 text-sm">{t('tasks.filters.attr.noActiveDefs')}</p>
      )}
      <ol className="space-y-2">
        {clauses.map((clause, index) => {
          const def = definitions.byKey.get(clause.key);
          const ops = def ? OPS[def.type] : (['ge', 'le', 'set', 'unset'] as DraftOp[]);
          const error = errors.get(clause.id);
          const name = def?.name ?? (clause.key || t('tasks.filters.attr.attribute'));
          const errorId = `tasks-attr-clause-${clause.id}-error`;
          const showArchivedGroup = (includeArchived || !!def?.archived) && archived.length > 0;
          const known = !!def || !clause.key;
          return (
            <li key={clause.id} className="tasks-attr-clause">
              <span className="tasks-attr-and text-text-muted font-mono text-xs font-semibold">{index > 0 ? t('tasks.filters.attr.and') : ''}</span>
              <div className="tasks-attr-key min-w-0">
                <select aria-label={t('tasks.filters.attr.attribute')} className={INPUT} value={clause.key}
                  aria-invalid={error === 'pickAttribute' || undefined} aria-describedby={error ? errorId : undefined}
                  onChange={(event) => {
                    const next = definitions.byKey.get(event.target.value);
                    const op = next && !OPS[next.type].includes(clause.op) ? 'set' : clause.op;
                    onChange(clause.id, { key: event.target.value, op });
                  }}>
                  <option value="">{t('tasks.filters.attr.attribute')}…</option>
                  {!known && <option value={clause.key}>{clause.key}</option>}
                  {active.map((d) => <option key={d.key} value={d.key}>{`${d.name} (${d.key})`}</option>)}
                  {showArchivedGroup && (
                    <optgroup label={t('tasks.filters.attr.archived')}>
                      {archived.map((d) => <option key={d.key} value={d.key}>{`${d.name} (${d.key}) — ${t('tasks.filters.attr.archived')}`}</option>)}
                    </optgroup>
                  )}
                </select>
                {def?.archived && <div className="mt-1"><Badge toneClass={TONE_CLASS.neutral}>{t('tasks.filters.attr.archived')}</Badge></div>}
              </div>
              <div className="tasks-attr-op min-w-0">
                <select aria-label={t('tasks.filters.attr.operator')} className={INPUT} value={clause.op}
                  onChange={(event) => onChange(clause.id, { op: event.target.value as DraftOp })}>
                  {ops.map((op) => <option key={op} value={op}>{t(OP_LABEL[op])}</option>)}
                </select>
              </div>
              <div className="tasks-attr-value min-w-0">
                {needsValue(clause.op) ? (
                  <input aria-label={t('tasks.filters.attr.value')} className={`${INPUT} font-mono`} inputMode="decimal"
                    value={clause.value} aria-invalid={(!!error && error !== 'pickAttribute') || undefined}
                    aria-describedby={error ? errorId : undefined}
                    onChange={(event) => onChange(clause.id, { value: event.target.value })} />
                ) : <span className="text-text-muted block pt-2 text-sm" aria-hidden>—</span>}
              </div>
              <Button type="button" variant="ghost" size="sm" className="tasks-attr-remove"
                aria-label={t('tasks.filters.attr.removeRow', { n: index + 1 })} onClick={() => onRemove(clause.id)}>
                <X size={14} aria-hidden />
              </Button>
              {error && <p id={errorId} className="tasks-attr-error text-feedback-danger text-xs">{t(ERROR_KEY[error], { name })}</p>}
            </li>
          );
        })}
      </ol>
      <Button type="button" variant="ghost" size="sm" onClick={onAdd}>
        <Plus size={14} aria-hidden /> {t('tasks.filters.attr.addCondition')}
      </Button>
    </div>
  );
}

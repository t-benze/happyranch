/**
 * Applied attribute clauses as removable chips (design v1.2 frame F).
 * Clauses render from the URL even while definitions are loading or failed:
 * an unknown key shows its raw key, and nothing is dropped.
 */
import { X } from 'lucide-react';
import { Badge } from '@/design-system/primitives/Badge';
import { Button } from '@/design-system/primitives/Button';
import { TONE_CLASS } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import type { AttributeDefinition } from '@/lib/api/types';
import type { AttributeFilter } from '@/lib/api/types';
import { clauseKey } from './filterModel';
import { clauseText } from './clauseText';

export function AppliedFilterChips({ attributes, defs, onRemove, onClear }: {
  attributes: readonly AttributeFilter[];
  defs: ReadonlyMap<string, AttributeDefinition>;
  onRemove: (index: number) => void;
  onClear: () => void;
}): JSX.Element | null {
  const { t } = useTranslation();
  if (attributes.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5" role="group" aria-label={t('tasks.filters.attr.chipsLabel')} data-testid="tasks-attr-chips">
      {attributes.map((occurrence, index) => {
        const key = clauseKey(occurrence);
        const text = clauseText(occurrence, defs, t);
        return (
          <span key={`${occurrence[0]}:${occurrence[1]}:${index}`}
            className="border-border-strong bg-surface-raised inline-flex max-w-full min-h-8 flex-wrap items-center gap-1.5 rounded-full border py-0.5 pr-1 pl-3 text-xs">
            {index > 0 && <span className="text-text-muted font-mono text-xs">{t('tasks.filters.attr.and')}</span>}
            <b className="text-text-primary font-semibold break-all">{text}</b>
            <span className="text-text-secondary font-mono text-xs break-all" dir="ltr">{key}</span>
            {defs.get(key)?.archived && <Badge toneClass={TONE_CLASS.neutral}>{t('tasks.filters.attr.archived')}</Badge>}
            <button type="button" aria-label={t('tasks.filters.attr.removeChip', { clause: text })}
              className="text-text-muted hover:bg-surface-hover hover:text-text-primary grid h-6 w-6 place-items-center rounded-full"
              onClick={() => onRemove(index)}>
              <X size={14} aria-hidden />
            </button>
          </span>
        );
      })}
      <Button type="button" variant="ghost" size="sm" onClick={onClear}>{t('tasks.filters.attr.clearAll')}</Button>
    </div>
  );
}

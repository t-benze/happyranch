/** Localized one-line text of an applied `attr_*` occurrence (chips, export summary). */
import type { AttributeDefinition } from '@/lib/api/types';
import type { AttributeFilter } from '@/lib/api/types';
import type { MessageKey, MessageParams } from '@/lib/i18n';
import { clauseBound, clauseKey } from './filterModel';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/** `Name (key)` when the definition is known, else the raw key (discovery pending/failed/unknown). */
export function attributeLabel(key: string, defs: ReadonlyMap<string, AttributeDefinition>): string {
  const def = defs.get(key);
  return def ? `${def.name} (${key})` : key;
}

export function clauseText(occurrence: AttributeFilter, defs: ReadonlyMap<string, AttributeDefinition>, t: Translate): string {
  const key = clauseKey(occurrence);
  const name = defs.get(key)?.name ?? key;
  switch (occurrence[0]) {
    case 'attr_min': return `${name} ${t('tasks.filters.attr.op.ge')} ${clauseBound(occurrence) ?? ''}`.trimEnd();
    case 'attr_max': return `${name} ${t('tasks.filters.attr.op.le')} ${clauseBound(occurrence) ?? ''}`.trimEnd();
    case 'attr_set': return `${name} ${t('tasks.filters.attr.op.set')}`;
    case 'attr_not_set': return `${name} ${t('tasks.filters.attr.op.unset')}`;
  }
}

/**
 * Small shared presentation pieces for Settings › Task attributes (THR-390
 * PR6). Copy comes from the catalog; user data (names, keys, rubric) is
 * rendered verbatim.
 */
import { AlertTriangle, Info, Lock } from 'lucide-react';
import type { ReactNode } from 'react';
import { Badge } from '@/design-system/primitives/Badge';
import { TONE_CLASS } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor, type Locale, type MessageKey } from '@/lib/i18n';
import type { AttributeDefinition } from '@/lib/api/types';

type T = ReturnType<typeof useTranslation>['t'];

export const TYPE_KEY: Record<string, MessageKey> = {
  numeric: 'settings.taskAttributes.type.numeric',
  string: 'settings.taskAttributes.type.string',
  boolean: 'settings.taskAttributes.type.boolean',
};

/** The type word, or the raw type verbatim for a type this build does not know. */
export function typeLabel(t: T, type: string): string {
  const key = TYPE_KEY[type];
  return key ? t(key) : type;
}

/** "Whole number, 1–10" / "Up to 40 characters" / "True or False" from server constraints. */
export function constraintSummary(t: T, d: Pick<AttributeDefinition, 'type' | 'constraints'>): string {
  const c = d.constraints;
  const num = (k: string): number | undefined => (typeof c[k] === 'number' ? (c[k] as number) : undefined);
  if (d.type === 'numeric') {
    const parts = [t(c.integer_only === true ? 'settings.taskAttributes.constraint.wholeNumber' : 'settings.taskAttributes.constraint.number')];
    const min = num('min');
    const max = num('max');
    if (min !== undefined && max !== undefined) parts.push(t('settings.taskAttributes.constraint.numRange', { min, max }));
    else if (min !== undefined) parts.push(t('settings.taskAttributes.constraint.numMin', { min }));
    else if (max !== undefined) parts.push(t('settings.taskAttributes.constraint.numMax', { max }));
    return parts.join(', ');
  }
  if (d.type === 'string') {
    const min = num('min_length');
    const max = num('max_length');
    if (min !== undefined && max !== undefined) return t('settings.taskAttributes.constraint.lenRange', { min, max });
    if (max !== undefined) return t('settings.taskAttributes.constraint.lenMax', { count: max });
    if (min !== undefined) return t('settings.taskAttributes.constraint.lenMin', { count: min });
    return t('settings.taskAttributes.constraint.textAny');
  }
  if (d.type === 'boolean') return t('settings.taskAttributes.constraint.trueFalse');
  return '';
}

/** Copy for a server `refusal_reason.code`. Unknown codes never show the raw code. */
export function refusalKey(code: string | null | undefined): MessageKey {
  switch (code) {
    case 'human_only':
      return 'settings.taskAttributes.refusal.readonly';
    case 'authority_unknown':
      return 'settings.taskAttributes.refusal.authorityUnknown';
    case 'definition_archived':
      return 'settings.taskAttributes.list.actionArchived';
    default:
      return 'settings.taskAttributes.refusal.generic';
  }
}

export function formatWhen(locale: Locale, iso: string | null | undefined): string {
  return iso ? formatDateShapeFor(locale, new Date(iso), 'dateTime') : '';
}

export function StateBadge({ archived }: { archived: boolean }): JSX.Element {
  const { t } = useTranslation();
  return archived
    ? <Badge toneClass={TONE_CLASS.neutral}>{t('settings.taskAttributes.state.archived')}</Badge>
    : <Badge toneClass={TONE_CLASS.positive}>{t('settings.taskAttributes.state.active')}</Badge>;
}

export function UnusedBadge(): JSX.Element {
  const { t } = useTranslation();
  return <Badge toneClass={TONE_CLASS.info}>{t('settings.taskAttributes.list.unused')}</Badge>;
}

export function DupNameBadge({ otherKey }: { otherKey: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <Badge toneClass={TONE_CLASS.attention} icon={<AlertTriangle size={12} aria-hidden="true" />}>
      {t('settings.taskAttributes.list.dupName', { key: otherKey })}
    </Badge>
  );
}

const CALLOUT_TONE = {
  attention: 'border-attention-text/30 bg-attention-soft text-attention-text',
  info: 'border-info/30 bg-info-soft text-info',
  danger: 'border-status-escalated/30 bg-tier-red-tint text-status-escalated',
} as const;

/** A token-styled notice: bold lead, body, optional actions. */
export function Callout({
  tone,
  title,
  children,
  actions,
  role,
  id,
  testId,
}: {
  tone: keyof typeof CALLOUT_TONE;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: 'alert' | 'status';
  id?: string;
  testId?: string;
}): JSX.Element {
  const Icon = tone === 'info' ? Info : AlertTriangle;
  return (
    <div
      id={id}
      role={role}
      data-testid={testId}
      className={`flex min-w-0 flex-col gap-2 rounded-md border px-3 py-2.5 text-xs sm:flex-row sm:items-start ${CALLOUT_TONE[tone]}`}
    >
      <div className="flex min-w-0 flex-1 gap-2">
        <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
        <p className="min-w-0 break-words">
          {title && <strong className="font-semibold">{title}</strong>}
          {title && children ? ' ' : null}
          {children}
        </p>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** A read-only structure row ("Locked") for key, type or constraints. */
export function LockedRow({ label, value, mono = false, overline = false, id }: { label: ReactNode; value: string; mono?: boolean; overline?: boolean; id: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span id={`${id}-label`} className={overline ? 'text-overline text-text-secondary tracking-wider uppercase' : 'text-text-primary text-sm font-semibold'}>{label}</span>
      <div
        role="group"
        aria-labelledby={`${id}-label`}
        className="border-border-default bg-surface-sunken flex min-h-10 min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm"
      >
        <span className={`min-w-0 flex-1 break-all ${mono ? 'font-mono' : ''}`}>{value}</span>
        <span className="text-text-secondary flex shrink-0 items-center gap-1 text-xs">
          <Lock size={12} aria-hidden="true" />
          {t('settings.taskAttributes.editor.lockedField')}
        </span>
      </div>
    </div>
  );
}

export function KeyChip({ value }: { value: string }): JSX.Element {
  return (
    <code className="border-border-default bg-surface-sunken text-text-primary rounded-sm border px-1.5 py-0.5 font-mono text-xs break-all">
      {value}
    </code>
  );
}

export function Overline({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-overline text-text-secondary tracking-wider uppercase">{children}</span>;
}

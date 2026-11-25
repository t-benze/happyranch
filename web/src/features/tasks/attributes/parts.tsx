/**
 * Presentation pieces for Task detail › Attributes (THR-390 PR7). Copy comes
 * from the catalog; authored names, keys, rubric text and values render
 * verbatim. Cross-feature imports are forbidden, so the few Settings-like
 * pieces (callout, constraint summary) are local; the constraint words reuse
 * PR6's existing catalog keys.
 */
import { AlertTriangle, Archive, CheckCircle2, Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { Badge } from '@/design-system/primitives/Badge';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import { identityText, useIdentityPresentation } from '@/hooks/identities';
import { formatDateShapeFor, type Locale } from '@/lib/i18n';
import type { AttributeDefinition, AttributePresence, TaskAttributeRead, TaskAttributesRead } from '@/lib/api/types';
import { PROVENANCE_KEY, refusalKey, showsAgentNote } from './model';

type T = ReturnType<typeof useTranslation>['t'];

export function formatWhen(locale: Locale, iso: string | null | undefined): string {
  return iso ? formatDateShapeFor(locale, new Date(iso), 'dateTime') : '';
}
export function formatUtc(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** The four value renderings: Not set, number, True/False, text / Empty string "". */
export function ValueText({ type, value }: { type: string; value: AttributePresence }): JSX.Element {
  const { t } = useTranslation();
  if (!value.present) return <span className="text-text-muted text-sm font-medium italic" data-value="unset">{t('tasks.attributes.value.notSet')}</span>;
  const v = value.value;
  if (type === 'boolean') {
    return <span className="text-text-primary font-mono text-sm font-semibold">{v === true ? t('tasks.attributes.value.true') : t('tasks.attributes.value.false')}</span>;
  }
  if (typeof v === 'string') {
    if (v === '') {
      return (
        <span className="text-text-secondary text-sm font-medium" data-value="empty">
          {t('tasks.attributes.value.emptyString')}
          <code className="border-border-default bg-surface-sunken text-text-primary ml-1 rounded-md border px-1 font-mono text-xs not-italic">""</code>
        </span>
      );
    }
    return <span className="text-text-primary text-sm font-semibold break-words">{v}</span>;
  }
  return <span className="text-text-primary font-mono text-sm font-semibold tabular-nums">{String(v)}</span>;
}

export const valueString = (t: T, type: string, value: AttributePresence): string => {
  if (!value.present) return t('tasks.attributes.value.notSet');
  if (type === 'boolean') return value.value === true ? t('tasks.attributes.value.true') : t('tasks.attributes.value.false');
  if (value.value === '') return `${t('tasks.attributes.value.emptyString')} ""`;
  return String(value.value);
};

const PROV_TONE: Record<TaskAttributeRead['provenance'], Tone> = {
  supplied_at_admission: 'positive',
  not_supplied_before_dispatch: 'neutral',
  definition_added_later: 'info',
  timing_unknown: 'attention',
};

export function ProvenanceBadge({ provenance }: { provenance: TaskAttributeRead['provenance'] }): JSX.Element {
  const { t } = useTranslation();
  const key = PROVENANCE_KEY[provenance] ?? 'tasks.attributes.prov.unknown';
  return <Badge toneClass={TONE_CLASS[PROV_TONE[provenance] ?? 'attention']} className="whitespace-normal">{t(key)}</Badge>;
}

export function ArchivedBadge({ readOnly = false }: { readOnly?: boolean }): JSX.Element {
  const { t } = useTranslation();
  return (
    <Badge toneClass={TONE_CLASS.neutral} icon={<Archive size={11} aria-hidden="true" />}>
      {readOnly ? t('tasks.attributes.editor.archivedRow') : t('tasks.attributes.archived')}
    </Badge>
  );
}

/**
 * Who label (correction 2). The identity kind and the "(Founder)" suffix come
 * ONLY from the event's `actor_kind`; the actor ID is never compared.
 */
export function useActorLabel(): (actorId: string, actorKind: string) => string {
  const { t } = useTranslation();
  const { identity } = useIdentityPresentation();
  return (actorId, actorKind) => {
    const founderKind = actorKind === 'founder';
    const text = identityText(identity(actorId, founderKind ? 'founder' : 'agent'), actorId);
    return founderKind ? `${text} ${t('tasks.attributes.history.founderSuffix')}` : text;
  };
}

/** "Whole number, 1–10" / "Up to 40 characters" / "True or False" (PR6 catalog words). */
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

const NOTE_TONE = {
  attention: 'border-attention-text/30 bg-attention-soft text-attention-text',
  info: 'border-info/30 bg-info-soft text-info',
  danger: 'border-status-escalated/30 bg-tier-red-tint text-status-escalated',
  positive: 'border-accent-text/30 bg-accent-soft text-accent-text',
} as const;

/** A token-styled notice: optional bold lead, body, optional actions. */
export function Note({
  tone, title, children, actions, role, testId,
}: {
  tone: keyof typeof NOTE_TONE;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: 'alert' | 'status';
  testId?: string;
}): JSX.Element {
  const Icon = tone === 'info' ? Info : tone === 'positive' ? CheckCircle2 : AlertTriangle;
  return (
    <div role={role} data-testid={testId} className={`flex min-w-0 flex-col gap-2 rounded-md border px-3 py-2.5 text-xs sm:flex-row sm:items-start ${NOTE_TONE[tone]}`}>
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

export function KeyChip({ children }: { children: ReactNode }): JSX.Element {
  return <span className="border-border-default bg-surface-sunken text-text-secondary rounded-md border px-1.5 font-mono text-xs">{children}</span>;
}

/** The task-level refusal notice, shared by the card and the editor (same copy at every point). */
export function RefusalNotice({ read }: { read: TaskAttributesRead }): JSX.Element | null {
  const { t } = useTranslation();
  if (read.editability.can_edit) return null;
  const code = read.editability.refusal_reason?.code;
  if (code === 'authority_unknown') {
    return <Note tone="attention" role="status" testId="ta-refusal">{t('tasks.attributes.refusal.authorityUnknown')}</Note>;
  }
  return (
    <Note tone="info" role="status" testId="ta-refusal">
      {t(refusalKey(code))}
      {showsAgentNote(code) ? ` ${t('tasks.attributes.refusal.agentNote')}` : ''}
    </Note>
  );
}


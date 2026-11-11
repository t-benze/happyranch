/**
 * Shared presentational parts for the retired-agent knowledge surfaces
 * (THR-296 S4): coverage list, state badges and UTC timestamps.
 */
import { useState, type ReactNode } from 'react';
import { Check, CircleAlert, CircleDot, CircleX, Circle, ChevronDown } from 'lucide-react';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n';
import type { SourceCoverage, DistillOperation, RetiredKnowledgeSource } from '@/hooks/knowledge';
import { COVERAGE_LABEL, COVERAGE_QUALIFIER, COVERAGE_REASON, blockedKeys, coverageCode } from './knowledgeCopy';

export function useUtc(): (value: string | null | undefined) => string {
  const { locale, t } = useTranslation();
  return (value) => {
    if (!value) return t('knowledge.common.unknownDate');
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return t('knowledge.common.utc', { time: formatDateShapeFor(locale, date, 'dateTime', 'UTC') });
  };
}

export function Pill({ tone, icon, children }: { tone: Tone; icon?: ReactNode; children: ReactNode }): JSX.Element {
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${TONE_CLASS[tone]}`}>
      {icon}
      {children}
    </span>
  );
}

const STATE_TONE: Record<DistillOperation['state'], Tone> = {
  Queued: 'neutral',
  Processing: 'info',
  Ready: 'positive',
  Failed: 'danger',
};

export function OperationStateBadge({ state }: { state: DistillOperation['state'] }): JSX.Element {
  const { t } = useTranslation();
  const icon = state === 'Ready' ? <Check size={12} aria-hidden="true" />
    : state === 'Failed' ? <CircleX size={12} aria-hidden="true" />
      : state === 'Processing' ? <CircleDot size={12} aria-hidden="true" />
        : <Circle size={12} aria-hidden="true" />;
  const key = state === 'Ready' ? 'knowledge.state.ready' : state === 'Failed' ? 'knowledge.state.failed'
    : state === 'Processing' ? 'knowledge.state.processing' : 'knowledge.state.queued';
  return <Pill tone={STATE_TONE[state]} icon={icon}>{t(key)}</Pill>;
}

function CoverageRow({ entry }: { entry: SourceCoverage }): JSX.Element {
  const { t } = useTranslation();
  const label = COVERAGE_LABEL[entry.category];
  const qualifier = COVERAGE_QUALIFIER[entry.category];
  const code = coverageCode(entry);
  const reasonKey = COVERAGE_REASON[code];
  const required = entry.category === 'runtime_memory';
  let badge: JSX.Element;
  if (entry.state === 'included') {
    badge = (
      <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>
        {entry.count === null ? t('knowledge.coverage.includedUnknown') : t('knowledge.coverage.included', { count: entry.count })}
      </Pill>
    );
  } else if (entry.state === 'unavailable') {
    badge = (
      <Pill tone={required ? 'danger' : 'attention'} icon={<CircleAlert size={12} aria-hidden="true" />}>
        {required ? t('knowledge.coverage.blocks') : t('knowledge.coverage.notCaptured')}
      </Pill>
    );
  } else {
    badge = <Pill tone="neutral" icon={<Circle size={12} aria-hidden="true" />}>{t('knowledge.coverage.excluded')}</Pill>;
  }
  return (
    <li className="border-border-subtle flex flex-col gap-1.5 border-b px-4 py-3 last:border-b-0 md:flex-row md:items-start md:gap-4">
      <div className="min-w-0 md:w-56 md:shrink-0">
        <div className="text-text-primary text-sm font-medium">{label ? t(label) : entry.category}</div>
        <div className="text-text-muted mt-0.5 text-xs font-semibold tracking-wide uppercase">
          {qualifier ? t(qualifier) : '—'}
        </div>
      </div>
      <div className="md:shrink-0">{badge}</div>
      <div className="text-text-secondary min-w-0 text-xs leading-relaxed md:flex-1">
        <p>{reasonKey ? t(reasonKey) : entry.reason}</p>
        {entry.state !== 'excluded' && entry.count === null ? (
          <p className="text-text-muted mt-0.5">{t('knowledge.coverage.countUnknown')}</p>
        ) : null}
        {entry.errors.length > 0 ? (
          <p className="text-text-muted mt-0.5 font-mono break-all">{entry.errors.join(', ')}</p>
        ) : null}
      </div>
    </li>
  );
}

const ALWAYS_SHOWN = new Set(['runtime_memory', 'claude_memory', 'other_provider_memory']);

/**
 * `full` lists every category. `summary` lists runtime, Claude and other
 * provider-local memory plus anything not excluded, with the remaining
 * excluded categories behind an explicit disclosure.
 */
export function CoverageList({ coverage, mode, label }: { coverage: SourceCoverage[]; mode: 'full' | 'summary'; label: string }): JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const primary = mode === 'full' ? coverage : coverage.filter((c) => ALWAYS_SHOWN.has(c.category) || c.state !== 'excluded');
  const rest = mode === 'full' ? [] : coverage.filter((c) => !primary.includes(c));
  const shown = expanded ? [...primary, ...rest] : primary;
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm overflow-hidden rounded-lg border">
      <ul aria-label={label}>
        {shown.map((entry) => <CoverageRow key={entry.category} entry={entry} />)}
      </ul>
      {rest.length > 0 ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className="text-accent-text hover:bg-surface-hover border-border-subtle focus-visible:ring-ring flex w-full items-center gap-1.5 border-t px-4 py-2.5 text-left text-xs font-semibold focus-visible:ring-2 focus-visible:outline-none"
        >
          <ChevronDown size={14} aria-hidden="true" className={expanded ? 'rotate-180' : ''} />
          {expanded ? t('knowledge.coverage.hideMore') : t('knowledge.coverage.showMore', { count: rest.length })}
        </button>
      ) : null}
    </div>
  );
}

/** Definition list used by drawer views; rows stack below md. */
export function Facts({ children }: { children: ReactNode }): JSX.Element {
  return <dl className="space-y-2 text-sm">{children}</dl>;
}

export function Fact({ term, children }: { term: string; children: ReactNode }): JSX.Element {
  return (
    <div className="md:flex md:gap-6">
      <dt className="text-text-muted md:w-40 md:shrink-0">{term}</dt>
      <dd className="text-text-primary min-w-0 break-words md:flex-1">{children}</dd>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }): JSX.Element {
  return <h3 className="text-text-muted mt-6 mb-2 text-xs font-semibold tracking-widest uppercase">{children}</h3>;
}

export function Notice({ tone, icon, role, children }: { tone: 'info' | 'danger' | 'positive' | 'neutral'; icon?: ReactNode; role?: 'status' | 'alert'; children: ReactNode }): JSX.Element {
  const cls = tone === 'danger' ? 'border-status-escalated/40 bg-tier-red-tint'
    : tone === 'positive' ? 'border-border-default bg-tier-green-tint'
      : tone === 'info' ? 'border-border-default bg-info-soft' : 'border-border-default bg-surface';
  return (
    <div role={role} className={`text-text-primary flex gap-2.5 rounded-lg border px-4 py-3 text-sm leading-relaxed ${cls}`}>
      {icon ? <span className="text-text-muted mt-0.5 shrink-0">{icon}</span> : null}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** Inline blocking explanation from the source's actual coverage/errors, plus the recorded cause codes. */
export function BlockedReason({ source }: { source: RetiredKnowledgeSource }): JSX.Element {
  const { t } = useTranslation();
  const causes = Array.from(new Set(source.errors));
  return (
    <>
      {blockedKeys(source).map((key) => t(key)).join(' ')}
      {causes.length ? <span className="text-text-muted font-mono text-xs break-all"> ({causes.join(', ')})</span> : null}
    </>
  );
}

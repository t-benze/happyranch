/**
 * Small presentation parts shared by the Connection dialog sections
 * (THR-387 S6-W3). Existing design tokens only; long text wraps (C4).
 */
import { forwardRef, type ReactNode } from 'react';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import type { Tone as BadgeTone } from '@/design-system/patterns/semanticTone';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';
import type { ActionResult, Cleanup } from '@/lib/native/connection';
import type { OutcomeView, StepRow, Tone } from './connectionModel';

/** Sentence joiner: English separates sentences with a space; Chinese does not. */
export function joinSentences(parts: string[], locale: string): string {
  return parts.join(locale === 'zh-CN' ? '' : ' ');
}

/** Long actions wrap at 380 CSS px / 200% text instead of overflowing. */
export const WRAP = 'h-auto max-w-full whitespace-normal text-left break-words';

const BADGE_TONE: Record<Tone, BadgeTone> = {
  ok: 'positive',
  progress: 'info',
  attention: 'attention',
  danger: 'danger',
  neutral: 'neutral',
};

const NOTICE_CLASS: Record<Tone, string> = {
  ok: 'border-border-default bg-accent-soft text-text-primary',
  progress: 'border-border-default bg-info-soft text-text-primary',
  attention: 'border-feedback-warning/40 bg-feedback-warning/5 text-text-primary',
  danger: 'border-tier-red bg-feedback-danger/10 text-tier-red',
  neutral: 'border-border-default bg-surface-raised text-text-primary',
};

export function ToneBadge({ tone, label }: { tone: Tone; label: string }): JSX.Element {
  return <StatusBadge tone={BADGE_TONE[tone]} label={label} dot={tone === 'progress' || tone === 'attention'} />;
}

export function Notice({
  tone,
  children,
  announce = false,
  testId,
}: {
  tone: Tone;
  children: ReactNode;
  /** Action outcomes are announced (status/alert); snapshot notices are not. */
  announce?: boolean;
  testId?: string;
}): JSX.Element {
  const role = announce ? (tone === 'danger' ? 'alert' : 'status') : undefined;
  return (
    <div role={role} data-testid={testId} data-tone={tone} className={`rounded-sm border p-3 text-sm break-words ${NOTICE_CLASS[tone]}`}>
      {children}
    </div>
  );
}

export const Section = forwardRef<
  HTMLHeadingElement,
  { title: string; testId: string; children: ReactNode; aside?: ReactNode }
>(function Section({ title, testId, children, aside }, ref) {
  return (
    <section data-testid={testId} className="border-border-default space-y-3 border-t pt-4 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3
          ref={ref}
          tabIndex={-1}
          className="text-text-secondary focus-visible:ring-accent-default min-w-0 rounded-sm text-xs font-semibold tracking-wide break-words uppercase focus:outline-none focus-visible:ring-2"
        >
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
});

export function Actions({ children }: { children: ReactNode }): JSX.Element {
  return <div className="flex flex-wrap gap-2">{children}</div>;
}

export function Hint({ children }: { children: ReactNode }): JSX.Element {
  return <p className="text-text-secondary text-xs break-words">{children}</p>;
}

export function Steps({ rows }: { rows: StepRow[] }): JSX.Element | null {
  const { t } = useTranslation();
  if (rows.length === 0) return null;
  return (
    <ol className="space-y-1 text-sm" data-testid="connection-steps">
      {rows.map((row) => (
        <li
          key={row.label}
          aria-current={row.state === 'current' ? 'step' : undefined}
          data-step-state={row.state}
          className={`break-words ${row.state === 'todo' ? 'text-text-muted' : row.state === 'unsaved' ? 'text-attention-text' : 'text-text-primary'} ${row.state === 'current' ? 'font-semibold' : ''}`}
        >
          {t(row.label)}
          {row.note && <span className="text-text-secondary font-normal"> · {t(row.note)}</span>}
        </li>
      ))}
    </ol>
  );
}

const CLEANUP_KEY: Record<Cleanup, MessageKey | null> = {
  confirmed: 'connection.host.hConfirmed',
  incomplete: 'connection.host.hIncomplete',
  unknown: 'connection.client.unknownValue',
  notApplicable: null,
};

function Components({ result }: { result: ActionResult }): JSX.Element | null {
  const { t } = useTranslation();
  const rows: [MessageKey, Cleanup][] = [
    ['connection.host.hCompDenial', result.hostDenial],
    ['connection.host.hCompHost', result.hostCleanup],
    ['connection.host.hCompNetwork', result.networkCleanup],
  ];
  const shown = rows.filter(([, value]) => CLEANUP_KEY[value] !== null);
  if (shown.length === 0) return null;
  return (
    <dl className="mt-2 grid gap-1 text-xs">
      {shown.map(([label, value]) => (
        <div key={label} className="flex flex-wrap gap-x-2">
          <dt className="text-text-secondary">{t(label)}</dt>
          <dd>{t(CLEANUP_KEY[value] as MessageKey)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One action outcome: its lines, hints, Check again / Try again, component results. */
export function OutcomeNotice({
  view,
  params,
  result,
  onCheckAgain,
  onRetry,
  retryLabel,
  testId = 'connection-outcome',
}: {
  view: OutcomeView;
  params: MessageParams;
  result?: ActionResult;
  onCheckAgain?: () => void;
  onRetry?: () => void;
  retryLabel?: string;
  testId?: string;
}): JSX.Element {
  const { t, locale } = useTranslation();
  return (
    <Notice tone={view.tone} announce testId={testId}>
      <p>{joinSentences(view.lines.map((line) => t(line.key, params)), locale)}</p>
      {view.hints.map((hint) => (
        <p key={hint} className="mt-1 text-xs">
          {t(hint)}
        </p>
      ))}
      {view.components && result && <Components result={result} />}
      {((view.checkAgain && onCheckAgain) || (view.retry && onRetry)) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {view.retry && onRetry && (
            <Button size="sm" variant="secondary" className={WRAP} onClick={onRetry}>
              {retryLabel}
            </Button>
          )}
          {view.checkAgain && onCheckAgain && (
            <Button size="sm" variant="outline" className={WRAP} onClick={onCheckAgain}>
              {t('connection.client.checkAgain')}
            </Button>
          )}
        </div>
      )}
    </Notice>
  );
}

export function formatTime(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export function formatSeconds(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value));
}

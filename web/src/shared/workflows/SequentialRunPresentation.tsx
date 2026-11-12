/**
 * Shared U8a sequential-run presentation. The Workflows run detail and the
 * task-detail banner render the SAME recorded facts through these helpers, so
 * process, history, finalization and banner cannot disagree.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { SequentialRun } from '@/lib/workflowSequentialView';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import type { RunFacts, Visit } from '@/lib/workflowSequentialView';

const RUN_STATE: Record<SequentialRun['state'], [MessageKey, Tone]> = {
  active: ['seqwf.state.active', 'info'], held: ['seqwf.state.held', 'attention'],
  completed: ['seqwf.state.completed', 'positive'], failed: ['seqwf.state.failed', 'danger'],
  cancelled: ['seqwf.state.cancelled', 'neutral'],
};
export function RunStateBadge({ state }: { state: SequentialRun['state'] }): JSX.Element {
  const { t } = useTranslation();
  const [key, tone] = RUN_STATE[state] ?? ['seqwf.state.unknown', 'neutral'];
  return <span className={`${TONE_CLASS[tone]} inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium`}>{t(key)}</span>;
}

const OUTCOME: Record<string, [MessageKey, Tone]> = {
  produced: ['seqwf.outcome.produced', 'positive'], APPROVE: ['seqwf.outcome.approve', 'positive'],
  REQUEST_CHANGES: ['seqwf.outcome.requestChanges', 'attention'], BLOCK: ['seqwf.outcome.block', 'danger'],
  PASS: ['seqwf.outcome.pass', 'positive'], REVISE: ['seqwf.outcome.revise', 'attention'],
  merged: ['seqwf.outcome.merged', 'positive'], held: ['seqwf.outcome.held', 'attention'], uncertain: ['seqwf.outcome.uncertain', 'attention'],
};
/** A recorded outcome keeps its own label; Revise is never shown as PASS. Unknown tokens stay verbatim. */
export function OutcomeBadge({ outcome }: { outcome: string }): JSX.Element {
  const { t } = useTranslation();
  const known = Object.prototype.hasOwnProperty.call(OUTCOME, outcome) ? OUTCOME[outcome] : null;
  return <span className={`${TONE_CLASS[known?.[1] ?? 'neutral']} inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium`}>{known ? t(known[0]) : outcome}</span>;
}

const VISIT_STATE: Record<string, MessageKey> = {
  queued: 'seqwf.visit.queued', running: 'seqwf.visit.running', waiting: 'seqwf.visit.waiting',
  held: 'seqwf.visit.held', completed: 'seqwf.visit.completed', failed: 'seqwf.visit.failed', cancelled: 'seqwf.visit.cancelled',
};
export function useVisitState(): (state: string) => string {
  const { t } = useTranslation();
  return state => (Object.prototype.hasOwnProperty.call(VISIT_STATE, state) ? t(VISIT_STATE[state]) : state);
}

const CAUSE: Record<string, MessageKey> = {
  sequential_output_access_unavailable: 'seqwf.cause.outputAccess',
  sequential_capability_unavailable: 'seqwf.cause.capability',
  sequential_carrier_invalid: 'seqwf.cause.malformed',
  sequential_carrier_limit_exceeded: 'seqwf.cause.carrierLimit',
  sequential_output_limit_exceeded: 'seqwf.cause.outputLimit',
  sequential_code_change_invalid: 'seqwf.cause.codeChange',
  sequential_repository_subject_changed: 'seqwf.cause.subjectChanged',
  sequential_repository_subject_unverified: 'seqwf.cause.subjectUnverified',
  sequential_authority_denied: 'seqwf.cause.authorityDenied',
  sequential_authority_unverified: 'seqwf.cause.authorityUnverified',
  unsupported_repository_format: 'seqwf.cause.format',
  unsupported_repository_scope: 'seqwf.cause.scope',
  repository_unavailable: 'seqwf.cause.repoUnavailable',
  repository_changed: 'seqwf.cause.repoChanged',
};
const TOKEN: Record<string, MessageKey> = {
  accountable_manager: 'seqwf.owner.accountableManager',
  no_automatic_retry: 'seqwf.action.noRetry',
  resolve_cause_before_editing_no_automatic_retry: 'seqwf.action.resolveBeforeEditing',
};
/** Localized explanation for a recorded token, with the raw token kept beside it. */
export function useRecordedToken(): (token: string) => string {
  const { t } = useTranslation();
  return token => {
    const key = Object.prototype.hasOwnProperty.call(CAUSE, token) ? CAUSE[token] : Object.prototype.hasOwnProperty.call(TOKEN, token) ? TOKEN[token] : null;
    return key ? t(key) : token;
  };
}

/** One headline sentence for a run, from recorded facts only. */
export function RunHeadlineText({ facts, roleOf, taskHref }: {
  facts: RunFacts; roleOf: (node: string) => string; taskHref: (id: string) => string;
}): JSX.Element {
  const { t, render } = useTranslation();
  const label = useRecordedToken();
  const visitState = useVisitState();
  const h = facts.headline;
  const visitRef = (v: Visit) => render('seqwf.headline.visitRef', {
    node: <span className="font-medium">{v.node_id}</span>, ordinal: v.ordinal,
    task: <Link className="text-id-task font-mono hover:underline" to={taskHref(v.task_id)}>{v.task_id}</Link>,
  });
  switch (h.kind) {
    case 'completed': return <span>{t('seqwf.headline.completed')}</span>;
    case 'failed': return <span>{t('seqwf.headline.failed')}</span>;
    case 'cancelled': return <span>{t('seqwf.headline.cancelled')}</span>;
    case 'exhausted': return <span>{render('seqwf.headline.exhausted', { role: roleOf(h.target.id), visit: visitRef(h.visit) })}</span>;
    case 'held': return <span>{h.cause ? render('seqwf.headline.heldCause', { cause: label(h.cause) }) : t('seqwf.headline.heldUndisclosed')}
      {h.visit && <> {visitRef(h.visit)}</>}</span>;
    default: return h.visit
      ? <span>{render('seqwf.headline.activeVisit', { visit: visitRef(h.visit), state: visitState(h.visit.state) })}</span>
      : <span>{t('seqwf.headline.active')}</span>;
  }
}

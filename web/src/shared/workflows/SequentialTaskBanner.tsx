/**
 * U8a source/node task banner. Shows the linked run's OWN recorded state,
 * independent of the task's status. A refused read renders a fixed denied note
 * and discloses no run id, link, request, output or finding. S7b: every @2 run
 * linked to the task is shown; @1 runs keep this original banner, labelled historical.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialRun, useTaskSequentialWorkflows, useWorkflowSetupStatus } from '@/hooks/workflows';
import type { GenericHoldCause, GenericSequentialRun } from '@/lib/api/workflowSequential';
/** S7a @2 projection types for the Workflows feature, which may not import lib/api directly. */
export type { GenericSequentialRun, GenericSequentialRunDetail, SequentialAssignment, SequentialControlRecord, SequentialProjection, SequentialRun } from '@/lib/api/workflowSequential';
import type { SequentialProjectionUnavailable } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';
import { deriveRun, parseDefinition, pluralTaskLinks } from '@/lib/workflowSequentialView';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import { RunHeadlineText, RunStateBadge } from './SequentialRunPresentation';

export function isAccessRefusal(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  return status === 403 || status === 404;
}

/** S7a hooks reject a superseded or foreign-org response with this fixed error; it never carries data. */
export function isLateOrgResponse(error: unknown): boolean {
  return error instanceof Error && error.message === 'late_org_response';
}
/** The closed workflow-sequential-unavailable@2 body returned in place of a projection. */
export function isProjectionUnavailable(value: unknown): value is SequentialProjectionUnavailable {
  return !!value && typeof value === 'object' && (value as { format?: unknown }).format === 'workflow-sequential-unavailable@2';
}

/** Small pill using the shared semantic tones; text is always a localized label or a verbatim token. */
export function Pill({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }): JSX.Element {
  return <span className={`${TONE_CLASS[tone]} inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium`}>{children}</span>;
}

/** S7b history_label=historical_v1: every @1 record carries this explicit label. */
export function HistoricalLabel(): JSX.Element {
  const { t } = useTranslation();
  return <Pill tone="neutral">{t('seqwf.v2.historical')}</Pill>;
}

const UNAVAILABLE: Record<SequentialProjectionUnavailable['code'], MessageKey> = {
  history_unverified: 'seqwf.v2.unavailable.historyUnverified',
  read_budget_exhausted: 'seqwf.v2.unavailable.readBudgetExhausted',
  authority_unverified: 'seqwf.v2.unavailable.authorityUnverified',
  authority_denied: 'seqwf.v2.unavailable.authorityDenied',
};
/** The closed unavailable@2 body: one distinct message per code, never an empty list. */
export function ProjectionUnavailable({ value }: { value: SequentialProjectionUnavailable }): JSX.Element {
  const { t } = useTranslation();
  return <div role="alert" className="border-border-default bg-attention-soft rounded-md border p-4 text-sm">
    <h2 className="text-text-primary font-medium">{t('seqwf.v2.unavailable.title')}</h2>
    <p className="text-text-primary mt-1">{t(UNAVAILABLE[value.code])}</p>
    <p className="text-text-muted mt-1 font-mono text-xs">{value.code}</p>
  </div>;
}

const STATE: Record<GenericSequentialRun['state'], [MessageKey, Tone]> = {
  running: ['seqwf.v2.state.running', 'info'], held: ['seqwf.v2.state.held', 'attention'], finished: ['seqwf.v2.state.finished', 'neutral'],
};
const PROGRESS: Record<NonNullable<GenericSequentialRun['progress']>, MessageKey> = {
  queued: 'seqwf.v2.progress.queued', executing: 'seqwf.v2.progress.executing',
  result_pending_settlement: 'seqwf.v2.progress.resultPendingSettlement', cancelling: 'seqwf.v2.progress.cancelling',
};
const TERMINAL: Record<NonNullable<GenericSequentialRun['terminal_outcome']>, [MessageKey, Tone]> = {
  completed: ['seqwf.v2.outcome.completed', 'positive'], failed: ['seqwf.v2.outcome.failed', 'danger'], cancelled: ['seqwf.v2.outcome.cancelled', 'neutral'],
};
/** Recorded state, separate progress, and terminal outcome only when Finished. Unknown tokens stay verbatim. */
export function GenericRunState({ run }: { run: GenericSequentialRun }): JSX.Element {
  const { t } = useTranslation();
  const state = Object.prototype.hasOwnProperty.call(STATE, run.state) ? STATE[run.state] : null;
  const progress = run.progress && Object.prototype.hasOwnProperty.call(PROGRESS, run.progress) ? PROGRESS[run.progress] : null;
  const terminal = run.state === 'finished' && run.terminal_outcome && Object.prototype.hasOwnProperty.call(TERMINAL, run.terminal_outcome) ? TERMINAL[run.terminal_outcome] : null;
  return <span className="inline-flex flex-wrap items-center gap-1">
    <Pill tone={state?.[1] ?? 'neutral'}>{state ? t(state[0]) : run.state}</Pill>
    {run.progress && <Pill tone="neutral">{t('seqwf.v2.progress', { progress: progress ? t(progress) : run.progress })}</Pill>}
    {run.state === 'finished' && run.terminal_outcome && <Pill tone={terminal?.[1] ?? 'neutral'}>{terminal ? t(terminal[0]) : run.terminal_outcome}</Pill>}
  </span>;
}

const CAUSE: Record<GenericHoldCause, MessageKey> = {
  task_failed_without_result: 'seqwf.v2.cause.taskFailedWithoutResult',
  task_timed_out_without_result: 'seqwf.v2.cause.taskTimedOutWithoutResult',
  task_failed_after_result: 'seqwf.v2.cause.taskFailedAfterResult',
  task_timed_out_after_result: 'seqwf.v2.cause.taskTimedOutAfterResult',
  result_bad_shape: 'seqwf.v2.cause.resultBadShape',
  result_too_large: 'seqwf.v2.cause.resultTooLarge',
  unknown_outcome: 'seqwf.v2.cause.unknownOutcome',
  assignee_unavailable: 'seqwf.v2.cause.assigneeUnavailable',
  revision_limit_exhausted: 'seqwf.v2.cause.revisionLimitExhausted',
  declared_edge: 'seqwf.v2.cause.declaredEdge',
  undeclared_outcome: 'seqwf.v2.cause.undeclaredOutcome',
  limit_exhausted: 'seqwf.v2.cause.limitExhausted',
  settlement_unverified: 'seqwf.v2.cause.settlementUnverified',
  output_evidence_mismatch: 'seqwf.v2.cause.outputEvidenceMismatch',
  exhausted: 'seqwf.v2.cause.exhausted',
  required_input_unavailable: 'seqwf.v2.cause.requiredInputUnavailable',
  terminal_evidence_unavailable: 'seqwf.v2.cause.terminalEvidenceUnavailable',
};
/** Held always names its recorded finite cause and original event/node/visit; nothing is inferred. */
export function HoldFacts({ hold }: { hold: NonNullable<GenericSequentialRun['hold']> }): JSX.Element {
  const { t } = useTranslation();
  const key = Object.prototype.hasOwnProperty.call(CAUSE, hold.cause) ? CAUSE[hold.cause] : null;
  return <dl className="bg-attention-soft grid grid-cols-1 gap-x-3 gap-y-1 rounded-md px-3 py-2 text-sm sm:grid-cols-2 wrap-anywhere">
    <div className="sm:col-span-2"><dt className="text-text-muted text-xs">{t('seqwf.v2.hold.cause')}</dt>
      <dd>{key ? t(key) : hold.cause} <span className="font-mono text-xs">({hold.cause})</span></dd></div>
    <div><dt className="text-text-muted text-xs">{t('seqwf.v2.hold.event')}</dt><dd className="font-mono text-xs">{hold.event_id}</dd></div>
    <div><dt className="text-text-muted text-xs">{t('seqwf.v2.hold.node')}</dt><dd className="font-mono text-xs">{hold.node_id}</dd></div>
    <div><dt className="text-text-muted text-xs">{t('seqwf.v2.hold.visit')}</dt><dd className="font-mono text-xs">{hold.visit_id}</dd></div>
  </dl>;
}

const REL: Record<string, MessageKey> = { source: 'seqwf.banner.relSource', carrier: 'seqwf.banner.relCarrier', visit: 'seqwf.banner.relVisit' };

/**
 * S7b: the plural @2 task-runs projection is the only source. Every run linked to this task is
 * shown in the projection's stable run-ID order; @1 runs keep their original banner under an
 * explicit historical label. A late/foreign-org response shows loading, never earlier data.
 */
export function SequentialTaskBanner({ slug, taskId }: { slug: string; taskId: string }): JSX.Element | null {
  const { t } = useTranslation();
  const link = useTaskSequentialWorkflows(taskId);
  const status = (link.error as { status?: number } | null)?.status;
  if (link.isError && status === 404 && (link.error as { code?: string } | null)?.code === 'task_not_found') return null;
  if (link.isError && isLateOrgResponse(link.error)) return <BannerShell><p role="status">{t('seqwf.v2.lateOrg')}</p></BannerShell>;
  if (link.isError) return <BannerShell>{isAccessRefusal(link.error) ? <Denied /> :
    <div className="flex flex-wrap items-center gap-2" role="alert"><span>{t('seqwf.banner.error')}</span>
      <Button size="sm" variant="outline" onClick={() => void link.refetch()}>{t('workflows.refresh')}</Button></div>}{!isAccessRefusal(link.error) && <WorkflowSetupStatusCard />}</BannerShell>;
  const data = link.data;
  if (!data) return null;
  if (isProjectionUnavailable(data)) return <BannerShell><ProjectionUnavailable value={data} /></BannerShell>;
  if (data.storage === 'absent' || !data.runs.length) return null;
  const relationships = pluralTaskLinks(data);
  const base = `/orgs/${slug}`;
  return <div className="flex flex-col">
    {data.runs.length > 1 && <p className="text-text-muted mt-3 text-xs">{t('seqwf.v2.banner.count', { count: data.runs.length })}</p>}
    {link.refreshFailed && <div role="status" className="bg-attention-soft mt-2 flex flex-wrap items-center gap-2 rounded-md px-2 py-1 text-sm">
      <span>{t('seqwf.stale')}</span>
      <Button size="sm" variant="outline" onClick={() => void link.refetch()}>{t('workflows.refresh')}</Button></div>}
    {data.runs.map((entry, i) => entry.abi === '@1'
      ? <div key={`${i}:${entry.run.id}`}><div className="mt-3 -mb-2"><HistoricalLabel /></div>
        <LinkedRun key={JSON.stringify([slug, taskId, entry.run.id])} slug={slug} taskId={taskId} runId={entry.run.id} linkStale={false} refreshLink={link.refetch} /></div>
      : <GenericLinkedRun key={`${i}:${entry.id}`} run={entry} taskId={taskId} base={base} relationships={relationships[i]?.relationships ?? []} />)}
  </div>;
}

/** One generic @2 run from the accepted task-runs snapshot: state, every relationship of this task, hold cause. */
function GenericLinkedRun({ run, taskId, base, relationships }: { run: GenericSequentialRun; taskId: string; base: string;
  relationships: GenericSequentialRun['tasks'] }): JSX.Element {
  const { t, render } = useTranslation();
  const runLink = <Link className="text-accent-default font-mono hover:underline" to={`${base}/workflows/runs/${encodeURIComponent(run.id)}`}>{run.id}</Link>;
  const task = <span className="font-mono">{taskId}</span>;
  return <BannerShell>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-text-muted">{t('seqwf.banner.runState')}</span>
      <GenericRunState run={run} />
      <span className="text-text-muted">{t('seqwf.versionLink', { identity: run.definition.identity, version: run.definition.version })}</span>
    </div>
    <ul className="mt-1">{relationships.length ? relationships.map((r, i) => <li key={`${i}:${r.relationship}:${r.visit_id ?? ''}`}>
      {r.relationship === 'visit' ? render('seqwf.v2.banner.relVisit', { run: runLink, visit: <span className="font-mono">{r.visit_id ?? '—'}</span> })
        : Object.prototype.hasOwnProperty.call(REL, r.relationship) ? render(REL[r.relationship], { run: runLink, task }) : <>{r.relationship} {runLink}</>}
    </li>) : <li>{render('seqwf.v2.banner.linked', { run: runLink })}</li>}</ul>
    {run.state === 'held' && run.hold && <div className="mt-2"><HoldFacts hold={run.hold} /></div>}
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.banner.independent')}</p>
  </BannerShell>;
}

/** Advisory evidence remains visible alongside a failed workflow read. */
export function WorkflowSetupStatusCard(): JSX.Element {
  const { t } = useTranslation();
  const q = useWorkflowSetupStatus();
  const data = q.data;
  const report = data?.integrity_report;
  const states: Record<string, MessageKey> = {
    unknown: 'seqwf.setupStatus.unknown', not_installed: 'seqwf.setupStatus.notInstalled',
    installed_unverified: 'seqwf.setupStatus.unverified', installed_verified: 'seqwf.setupStatus.verified',
  };
  const outcomes: Record<string, MessageKey> = {
    ok: 'seqwf.setupStatus.ok', failed: 'seqwf.setupStatus.failed', unavailable: 'seqwf.setupStatus.unavailable',
  };
  return <section aria-label={t('seqwf.setupStatus.title')} className="border-border-default bg-surface-raised my-3 rounded-md border p-3 text-sm wrap-anywhere">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-medium">{t('seqwf.setupStatus.title')}</h2>
      <Button size="sm" variant="outline" onClick={() => void q.refetch()}>{t('workflows.refresh')}</Button></div>
    {q.isError ? <p role="alert">{t('seqwf.setupStatus.error')}</p> : !data ? <p role="status">{t('seqwf.loading')}</p> : <>
      <p>{t(states[data.state])}</p>
      <p className="text-text-muted">{t('seqwf.setupStatus.reason', { reason: data.reason })}</p>
      {data.attempt_time && <p>{t('seqwf.setupStatus.attempt', { date: data.attempt_time })}</p>}
      {data.backup_path && <p className="font-mono text-xs">{t('seqwf.setupStatus.backup', { path: data.backup_path })}</p>}
      <div className="border-border-default mt-2 border-t pt-2"><h3 className="font-medium">{t('seqwf.setupStatus.daily')}</h3>
        {report ? <>
          <p>{t(outcomes[report.outcome])}</p>
          <p>{t('seqwf.setupStatus.reason', { reason: report.reason })}</p>
          <p>{t('seqwf.setupStatus.scheduled', { date: report.scheduled_date ?? t('seqwf.setupStatus.noDate') })}</p>
          <p>{t('seqwf.setupStatus.checked', { date: report.checked_at ?? t('seqwf.setupStatus.noDate') })}</p>
          <p className="text-text-muted">{t('seqwf.setupStatus.reporting', { external: report.reporting.external, audit: report.reporting.audit })}</p>
        </> : <p>{t('seqwf.setupStatus.noReport')}</p>}
      </div>
    </>}
    <p className="text-text-muted mt-2 text-xs">{t('seqwf.setupStatus.advisory')}</p>
  </section>;
}

function BannerShell({ children }: { children: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  return <section aria-label={t('seqwf.banner.label')} className="border-border-default bg-surface-raised mt-3 rounded-md border px-3 py-2 text-sm wrap-anywhere">{children}</section>;
}
function Denied(): JSX.Element {
  const { t } = useTranslation();
  return <p role="status">{t('seqwf.banner.denied')}</p>;
}

/**
 * The task-link read only selects the run id. State badge, relationship, version
 * link and headline all come from ONE accepted run-detail snapshot, so they can
 * never disagree; nothing run-specific is shown before that snapshot exists.
 */
function LinkedRun({ slug, taskId, runId, linkStale, refreshLink }: { slug: string; taskId: string; runId: string; linkStale: boolean; refreshLink: () => Promise<unknown> }): JSX.Element {
  const { t, render } = useTranslation();
  const detail = useSequentialRun(runId);
  const accepted = detail.data && detail.data.run.id === runId ? detail.data : null;
  const def = useSequentialDefinition(accepted?.run.definition_id ?? '');
  if ((detail.isError && isAccessRefusal(detail.error)) || (def.isError && isAccessRefusal(def.error))) return <BannerShell><Denied /></BannerShell>;
  if (!accepted) {
    return <BannerShell>{detail.isError
      ? <div className="flex flex-wrap items-center gap-2" role="alert"><span>{t('seqwf.banner.detailError')}</span>
        <Button size="sm" variant="outline" onClick={() => void detail.refetch()}>{t('workflows.refresh')}</Button></div>
      : <p role="status">{t('seqwf.loading')}</p>}</BannerShell>;
  }
  const run = accepted.run;
  const parsed = def.data ? parseDefinition(def.data.definition_json) : null;
  // The headline's next-step/exhaustion explanation needs the admitted definition; say when it is missing.
  const defUnavailable = !parsed && (def.isError || !!def.data);
  const facts = deriveRun(accepted, parsed);
  const roleOf = (node: string) => parsed?.nodes.find(n => n.id === node)?.role ?? node;
  const rel = REL[run.tasks.find(x => x.task_id === taskId)?.relationship ?? ''] ?? null;
  const base = `/orgs/${slug}`;
  return <BannerShell>
    {(detail.refreshFailed || linkStale || (def.refreshFailed && !!parsed)) && <div role="status" className="bg-attention-soft mb-2 flex flex-wrap items-center gap-2 rounded-md px-2 py-1">
      <span>{t('seqwf.stale')}</span>
      <Button size="sm" variant="outline" onClick={() => { void refreshLink(); void detail.refetch(); void def.refetch(); }}>{t('workflows.refresh')}</Button></div>}
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-text-muted">{t('seqwf.banner.runState')}</span>
      <RunStateBadge state={run.state} />
      <span>{rel ? render(rel, {
        run: <Link className="text-accent-default font-mono hover:underline" to={`${base}/workflows/runs/${encodeURIComponent(run.id)}`}>{run.id}</Link>,
        task: <span className="font-mono">{taskId}</span>,
      }) : null}</span>
      <Link className="text-accent-default hover:underline" to={`${base}/workflows/definitions/${encodeURIComponent(run.definition_id)}`}>
        {t('seqwf.versionLink', { identity: run.definition.identity, version: run.definition.version })}
      </Link>
    </div>
    <p className="mt-1"><RunHeadlineText facts={facts} roleOf={roleOf} taskHref={id => `${base}/tasks/${encodeURIComponent(id)}`} /></p>
    {defUnavailable && <div role="alert" className="bg-attention-soft mt-1 flex flex-wrap items-center gap-2 rounded-md px-2 py-1">
      <span>{t('seqwf.banner.defUnavailable')}</span>
      <Button size="sm" variant="outline" onClick={() => void def.refetch()}>{t('workflows.refresh')}</Button></div>}
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.banner.independent')}</p>
  </BannerShell>;
}

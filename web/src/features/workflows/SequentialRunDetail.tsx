/**
 * U8a run detail. Every statement comes from the recorded run projection and
 * its admitted definition version: visits/outcomes/findings verbatim, real
 * task links, "Task not created yet" for nodes without a visit, and no
 * successor claim unless its visit exists. Read-only. S7b: @2 runs render their
 * attributed generic detail; @1 history renders this original view unchanged.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialRunV2 } from '@/hooks/workflows';
import type { GenericSequentialRunDetail, SequentialAssignment, SequentialControlRecord } from '@/shared/workflows/SequentialTaskBanner';
import type { MessageKey } from '@/lib/i18n';
import { deriveRun, genericVisitAssignments, parseDefinition, taskLinks, type DefView, type RunFacts, type SequentialRunDetail as Detail, type Visit } from '@/lib/workflowSequentialView';
import { OutcomeBadge, RunHeadlineText, RunStateBadge, useRecordedToken, useVisitState } from '@/shared/workflows/SequentialRunPresentation';
import { GenericRunState, HistoricalLabel, HoldFacts, isAccessRefusal, isProjectionUnavailable, Pill } from '@/shared/workflows/SequentialTaskBanner';
import { AgentReported, EngineAuthenticated, ReadOnlyNote, RunTaskLinks, StaleNotice, TaskRef, V2ReadState } from './SequentialCommon';
import { reviewDiagnostic } from './strings';

const ASSESSMENT_TYPES = new Set(['independent-code-review@1', 'code-qa@1', 'document-review@1']);
const REVIEW_TYPES = new Set(['independent-code-review@1', 'document-review@1']);

/**
 * THR-376 r2 W1: the setup card follows the run h1 and keeps one tree slot in every read state.
 * S7b: the @2 run projection is the only source. A historical_v1 wrapper renders the unchanged
 * @1 view inside a labelled history wrapper; a generic @2 run renders its attributed detail.
 */
export function SequentialRunDetail({ slug, runId, setupCard }: { slug: string; runId: string; setupCard: ReactNode }): JSX.Element {
  const q = useSequentialRunV2(runId);
  const value = !q.isError && q.data && !isProjectionUnavailable(q.data) ? q.data : null;
  return <>
    <V2ReadState q={q}>{d => <div className="flex flex-wrap items-center gap-2">
      <h1 className="font-display text-h1 text-text-primary font-mono wrap-anywhere">{d.format === 'workflow-sequential-history-view@2' ? d.detail.run.id : d.run.id}</h1>
      {d.format === 'workflow-sequential-history-view@2' ? <><HistoricalLabel /><RunStateBadge state={d.detail.run.state} /></> : <GenericRunState run={d.run} />}
    </div>}</V2ReadState>
    {setupCard}
    {value && (value.format === 'workflow-sequential-history-view@2'
      ? <HistoryWrapper><Loaded slug={slug} detail={value.detail} /></HistoryWrapper>
      : <GenericLoaded slug={slug} detail={value} />)}
  </>;
}

function HistoryWrapper({ children }: { children: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  return <section aria-label={t('seqwf.v2.historical')} className="border-border-default mt-3 rounded-lg border border-dashed p-3">
    <p className="text-text-muted text-xs">{t('seqwf.v2.historicalBody')}</p>
    {children}
  </section>;
}

type Generic = GenericSequentialRunDetail;

function GenericLoaded({ slug, detail }: { slug: string; detail: Generic }): JSX.Element {
  const { t, render } = useTranslation();
  const base = `/orgs/${slug}`;
  const run = detail.run;
  const visitIds = new Set(detail.visits.map(v => v.id));
  const orphanOutputs = detail.outputs.filter(o => !visitIds.has(o.visit_id));
  return <>
    <div className="text-text-muted mt-2 flex flex-col gap-1 text-xs wrap-anywhere">
      <p>{t('seqwf.admittedVersion', { identity: run.definition.identity, version: run.definition.version })}</p>
      <p>{render('seqwf.v2.initiator', {
        task: <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(run.initiator_task_id)}`}>{run.initiator_task_id}</Link>,
        agent: <span className="font-mono">{run.initiator_agent_id}</span>,
      })}</p>
      <p>{t('seqwf.v2.counts', { visits: run.visits, results: run.result_events, stored: run.stored_events, recoveries: run.recoveries })}</p>
    </div>
    <ReadOnlyNote />
    <section aria-labelledby="seqwf-v2-state" className="border-border-default bg-surface-raised mt-4 rounded-lg border p-4 text-sm wrap-anywhere">
      <h2 id="seqwf-v2-state" className="text-h2 text-text-primary">{t('seqwf.currentState')}</h2>
      <div className="mt-2"><GenericRunState run={run} /></div>
      {run.state === 'held' && run.hold && <div className="mt-3"><HoldFacts hold={run.hold} /></div>}
      {run.active_visit_id && <p className="text-text-muted mt-2 text-xs">{t('seqwf.v2.activeVisit', { visit: run.active_visit_id })}</p>}
      <div className="mt-3"><RunTaskLinks tasks={run.tasks} base={base} /></div>
    </section>
    <section aria-labelledby="seqwf-v2-pins" className="mt-4 text-sm wrap-anywhere">
      <h2 id="seqwf-v2-pins" className="text-h2 text-text-primary">{t('seqwf.v2.pins')}</h2>
      <dl className="text-text-muted mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <Fact label="seqwf.definitionDigest" value={run.definition.definition_digest} />
        <Fact label="seqwf.sourceDigest" value={run.definition.source_digest} />
        <Fact label="seqwf.v2.settingsPin" value={t('seqwf.v2.settingsPinValue', { id: run.settings_id, generation: run.settings.generation })} />
        <Fact label="seqwf.v2.settingsDigest" value={run.settings.snapshot_digest} />
      </dl>
      <details className="mt-2">
        <summary className="cursor-pointer text-xs">{t('seqwf.v2.definitionText')}</summary>
        <p className="text-text-muted mt-1 text-xs">{t('seqwf.v2.verbatim')}</p>
        <pre className="border-border-default mt-1 max-h-96 overflow-auto rounded-md border p-2 text-xs whitespace-pre-wrap break-all">{detail.definition_json}</pre>
      </details>
    </section>
    <section aria-labelledby="seqwf-v2-request" className="mt-4 text-sm">
      <h2 id="seqwf-v2-request" className="text-h2 text-text-primary">{t('seqwf.request')}</h2>
      <p className="text-text-muted text-xs">{t('seqwf.authored')}</p>
      <pre className="border-border-default mt-1 rounded-md border p-3 font-sans whitespace-pre-wrap wrap-anywhere">{detail.request}</pre>
      {detail.inputs.filter(i => i.name !== 'request').map(i => <div key={i.name} className="mt-2"><p className="text-text-muted font-mono text-xs">{i.name}</p>
        <pre className="border-border-default rounded-md border p-3 font-sans whitespace-pre-wrap wrap-anywhere">{i.value}</pre></div>)}
    </section>
    <Assignments detail={detail} />
    <section aria-labelledby="seqwf-v2-visits" className="mt-4 text-sm">
      <h2 id="seqwf-v2-visits" className="text-h2 text-text-primary">{t('seqwf.v2.visits')}</h2>
      <p className="text-text-muted text-xs">{t('seqwf.v2.provenanceNote')}</p>
      {detail.visits.length === 0 ? <p className="text-text-muted mt-2">{t('seqwf.v2.visitsEmpty')}</p>
        : <ul className="mt-2 flex flex-col gap-3">{genericVisitAssignments(detail).map(({ visit, assignment }) =>
          <GenericVisit key={visit.id} visit={visit} assignment={assignment} outputs={detail.outputs.filter(o => o.visit_id === visit.id)}
            base={base} active={run.active_visit_id === visit.id} />)}</ul>}
      {orphanOutputs.length > 0 && <div className="mt-3"><p className="text-text-muted text-xs">{t('seqwf.v2.otherOutputs')}</p>
        {orphanOutputs.map(o => <OutputItem key={o.id} o={o} />)}</div>}
    </section>
    <Controls detail={detail} base={base} />
    <NativeCancellations detail={detail} base={base} />
  </>;
}

function Fact({ label, value }: { label: MessageKey; value: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  return <div><dt className="inline">{t(label)} </dt><dd className="text-text-primary inline font-mono break-all">{value}</dd></div>;
}

const PRINCIPAL_SOURCE: Record<SequentialAssignment['principal_source'], MessageKey> = {
  bundled: 'seqwf.v2.source.bundled', org: 'seqwf.v2.source.org', run: 'seqwf.v2.source.run', control: 'seqwf.v2.source.control',
};
const ORIGIN: Record<SequentialAssignment['origins']['model'], MessageKey> = {
  agent_default: 'seqwf.v2.origin.agentDefault', run: 'seqwf.v2.origin.run', clear: 'seqwf.v2.origin.clear',
};
const OVERRIDE_SOURCE: Record<'founder' | 'manager', MessageKey> = { founder: 'seqwf.v2.override.founder', manager: 'seqwf.v2.override.manager' };
function useLabel() {
  const { t } = useTranslation();
  return <K extends string>(map: Record<K, MessageKey>, value: string | null) =>
    value === null ? t('seqwf.v2.notSet') : Object.prototype.hasOwnProperty.call(map, value) ? t(map[value as K]) : value;
}

function Assignments({ detail }: { detail: Generic }): JSX.Element {
  const { t } = useTranslation();
  const label = useLabel();
  return <section aria-labelledby="seqwf-v2-assignments" className="mt-4 text-sm">
    <h2 id="seqwf-v2-assignments" className="text-h2 text-text-primary">{t('seqwf.v2.assignments')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.v2.assignmentsBody')}</p>
    {detail.assignments.length === 0 ? <p className="text-text-muted mt-2">{t('seqwf.v2.assignmentsEmpty')}</p>
      : <ul className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">{detail.assignments.map(a => <li key={a.id} className="border-border-default rounded-md border p-3 wrap-anywhere">
        <p className="font-medium">{t('seqwf.v2.assignmentHeading', { node: a.node_id, role: a.role, generation: a.generation })}</p>
        <p className="text-text-muted font-mono text-xs">{a.id}</p>
        <dl className="mt-2 grid grid-cols-1 gap-y-1 text-xs">
          <div><dt className="text-text-muted inline">{t('seqwf.v2.agent')} </dt><dd className="inline font-mono">{a.principal}</dd></div>
          <div><dt className="text-text-muted inline">{t('seqwf.v2.selectionSource')} </dt><dd className="inline">{label(PRINCIPAL_SOURCE, a.principal_source)}</dd></div>
          {a.predecessor_id && <div><dt className="text-text-muted inline">{t('seqwf.v2.predecessor')} </dt><dd className="inline font-mono">{a.predecessor_id}</dd></div>}
          <div><dt className="text-text-muted inline">{t('seqwf.v2.modelLine')} </dt><dd className="inline">{t('seqwf.v2.tuple', {
            effective: a.effective.model ?? t('seqwf.v2.notSet'), requested: a.requested.model ?? t('seqwf.v2.notSet'), origin: label(ORIGIN, a.origins.model) })}</dd></div>
          <div><dt className="text-text-muted inline">{t('seqwf.v2.effortLine')} </dt><dd className="inline">{t('seqwf.v2.tuple', {
            effective: a.effective.reasoning_effort ?? t('seqwf.v2.notSet'), requested: a.requested.reasoning_effort ?? t('seqwf.v2.notSet'), origin: label(ORIGIN, a.origins.reasoning_effort) })}</dd></div>
          {Object.entries(a.requested).filter(([k]) => k !== 'model' && k !== 'reasoning_effort').map(([k, v]) =>
            <div key={k}><dt className="text-text-muted inline font-mono">{k} </dt><dd className="inline font-mono">{v ?? t('seqwf.v2.notSet')}</dd></div>)}
          <div><dt className="text-text-muted inline">{t('seqwf.v2.override')} </dt><dd className="inline">{t('seqwf.v2.overrideValue', {
            model: a.selection.model_override ?? t('seqwf.v2.notSet'), effort: a.selection.reasoning_effort_override ?? t('seqwf.v2.notSet'),
            source: label(OVERRIDE_SOURCE, a.selection.override_source) })}</dd></div>
          <div><dt className="text-text-muted inline">{t('seqwf.v2.executor')} </dt><dd className="inline font-mono">{`${a.executor.name} · ${a.executor.kind}`}</dd></div>
          <div><dt className="text-text-muted inline">{t('seqwf.v2.snapshotDigest')} </dt><dd className="inline font-mono break-all">{a.snapshot_digest}</dd></div>
        </dl>
      </li>)}</ul>}
  </section>;
}

type GenericVisitRecord = Generic['visits'][number];
function GenericVisit({ visit, assignment, outputs, base, active }: { visit: GenericVisitRecord; assignment: SequentialAssignment | null;
  outputs: Generic['outputs']; base: string; active: boolean }): JSX.Element {
  const { t } = useTranslation();
  const visitState = useVisitState();
  const n = visit.native;
  const rejected = n.result_id !== null && visit.accepted_result_id !== n.result_id;
  return <li aria-current={active ? 'step' : undefined} className={`bg-surface-raised rounded-md border p-3 wrap-anywhere ${active ? 'border-accent-default' : 'border-border-default'}`}>
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-medium">{t('seqwf.v2.visitHeading', { ordinal: visit.ordinal, node: visit.node_id })}</span>
      <span className="text-text-muted text-xs">{visitState(visit.state)}</span>
      {visit.selected && <Pill tone="neutral">{t('seqwf.v2.selected')}</Pill>}
      {active && <span className="text-accent-default text-xs font-medium">{t('seqwf.activeNow')}</span>}
    </div>
    <p className="text-text-muted font-mono text-xs">{visit.id}</p>
    <p className="mt-1 text-xs">{t('seqwf.v2.visitAssignment', { assignment: visit.assignment_id, generation: visit.assignment_generation })}
      {assignment && <span className="text-text-muted"> · {t('seqwf.v2.visitAgent', { agent: assignment.principal })}</span>}</p>
    <div className="border-border-default mt-2 rounded-md border p-2">
      <div className="flex flex-wrap items-center gap-2"><AgentReported /><span className="text-text-muted text-xs">{t('seqwf.v2.agentReportedNote')}</span></div>
      <p className="mt-1 text-xs"><span className="text-text-muted">{t('seqwf.v2.reportedOutcome')} </span>
        <span className="font-mono">{visit.outcome ?? t('seqwf.v2.notSet')}</span></p>
      {visit.findings.length > 0 && <div className="mt-1"><p className="text-text-muted text-xs">{t('seqwf.findings')}</p>
        <ul className="mt-1 flex flex-col gap-1">{visit.findings.map((f, i) => <li key={i} className="border-border-default border-l-2 pl-2 whitespace-pre-wrap">{f}</li>)}</ul></div>}
      {outputs.map(o => <OutputItem key={o.id} o={o} />)}
    </div>
    <div className="border-border-default mt-2 rounded-md border p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2"><EngineAuthenticated /><span className="text-text-muted">{t('seqwf.v2.engineNote')}</span></div>
      <dl className="mt-1 grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
        <div><dt className="text-text-muted inline">{t('seqwf.v2.nativeTask')} </dt><dd className="inline"><Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(n.task_id)}`}>{n.task_id}</Link></dd></div>
        <div><dt className="text-text-muted inline">{t('seqwf.v2.principal')} </dt><dd className="inline font-mono">{n.principal}</dd></div>
        <div><dt className="text-text-muted inline">{t('seqwf.v2.session')} </dt><dd className="inline font-mono break-all">{n.session_id ?? t('seqwf.v2.notSet')}</dd></div>
        <div><dt className="text-text-muted inline">{t('seqwf.v2.nativeResult')} </dt><dd className="inline font-mono">{n.result_id ?? t('seqwf.v2.notSet')}</dd></div>
        <div><dt className="text-text-muted inline">{t('seqwf.v2.terminalDigest')} </dt><dd className="inline font-mono break-all">{n.terminal_digest ?? t('seqwf.v2.notSet')}</dd></div>
        <div><dt className="text-text-muted inline">{t('seqwf.v2.outerDigest')} </dt><dd className="inline font-mono break-all">{n.outer_digest ?? t('seqwf.v2.notSet')}</dd></div>
      </dl>
    </div>
    <p className="mt-2 text-xs"><span className="text-text-muted">{t('seqwf.v2.acceptedResult')} </span>
      <span className="font-mono">{visit.accepted_result_id ?? t('seqwf.v2.noneAccepted')}</span></p>
    {rejected && <p className="bg-attention-soft mt-1 rounded-md px-2 py-1 text-xs">{t('seqwf.v2.rejectedCallback', { result: n.result_id ?? '' })}</p>}
  </li>;
}

function OutputItem({ o }: { o: Generic['outputs'][number] }): JSX.Element {
  const { t } = useTranslation();
  return <details className="mt-2">
    <summary className="cursor-pointer text-xs">{t('seqwf.v2.outputSummary', { slot: o.slot, contract: o.contract })}</summary>
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.v2.outputIds', { task: o.task_id, session: o.session_id, result: o.result_id })}</p>
    <p className="text-text-muted mt-1 font-mono text-xs break-all">{o.content_digest}</p>
    <pre className="border-border-default mt-1 max-h-64 overflow-auto rounded-md border p-2 text-xs whitespace-pre-wrap break-all">{o.content_utf8}</pre>
  </details>;
}

const ACTION: Record<SequentialControlRecord['action'], MessageKey> = {
  retry: 'seqwf.v2.action.retry', rebrief: 'seqwf.v2.action.rebrief', reassign: 'seqwf.v2.action.reassign', cancel: 'seqwf.v2.action.cancel',
};
/** Ordered, recorded control history with original actor and IDs. Display only. */
function Controls({ detail, base }: { detail: Generic; base: string }): JSX.Element {
  const { t, render } = useTranslation();
  const label = useLabel();
  return <section aria-labelledby="seqwf-v2-controls" className="mt-4 text-sm">
    <h2 id="seqwf-v2-controls" className="text-h2 text-text-primary">{t('seqwf.v2.controls')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.v2.controlsBody')}</p>
    {detail.controls.length === 0 ? <p className="text-text-muted mt-2">{t('seqwf.v2.controlsEmpty')}</p>
      : <ol className="mt-2 flex flex-col gap-2">{detail.controls.map(c => <li key={c.id} className="border-border-default rounded-md border p-3 text-xs wrap-anywhere">
        <p className="text-sm font-medium">{t('seqwf.v2.controlHeading', { sequence: c.event_sequence, action: label(ACTION, c.action) })}</p>
        <p className="mt-1">{render('seqwf.v2.controlActor', {
          task: <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(c.actor_task_id)}`}>{c.actor_task_id}</Link>,
          principal: <span className="font-mono">{c.actor_principal}</span>, session: <span className="font-mono">{c.actor_session_id}</span>,
          result: <span className="font-mono">{c.actor_result_id}</span> })}</p>
        <dl className="text-text-muted mt-1 grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
          <Fact label="seqwf.v2.controlId" value={c.id} />
          <Fact label="seqwf.v2.eventId" value={c.event_id} />
          <Fact label="seqwf.v2.operationKey" value={c.operation_key} />
          <Fact label="seqwf.v2.expectedCas" value={c.expected_cas} />
          <Fact label="seqwf.v2.heldEvent" value={c.expected_held_event_id ?? t('seqwf.v2.notSet')} />
          <Fact label="seqwf.v2.newVisit" value={c.new_visit_id ?? t('seqwf.v2.notSet')} />
          <Fact label="seqwf.v2.requestDigest" value={c.request_digest} />
          <Fact label="seqwf.v2.receiptDigest" value={c.receipt_digest} />
        </dl>
      </li>)}</ol>}
  </section>;
}

const CANCEL_KIND: Record<'cancelled' | 'cancellation_finished', MessageKey> = {
  cancelled: 'seqwf.v2.cancelKind.cancelled', cancellation_finished: 'seqwf.v2.cancelKind.finished',
};
const CANCEL_ORIGIN: Record<'founder_cancel' | 'source_terminal', MessageKey> = {
  founder_cancel: 'seqwf.v2.cancelOrigin.founder', source_terminal: 'seqwf.v2.cancelOrigin.sourceTerminal',
};
const TASK_STATUS: Record<Generic['native_cancellations'][number]['source_status'], MessageKey> = {
  pending: 'seqwf.v2.taskStatus.pending', in_progress: 'seqwf.v2.taskStatus.inProgress', escalated: 'seqwf.v2.taskStatus.escalated',
  completed: 'seqwf.v2.taskStatus.completed', failed: 'seqwf.v2.taskStatus.failed', cancelled: 'seqwf.v2.taskStatus.cancelled',
  superseded: 'seqwf.v2.taskStatus.superseded',
};
/** Native source/founder cancellation keeps its distinct origin and has no callback actor. */
function NativeCancellations({ detail, base }: { detail: Generic; base: string }): JSX.Element | null {
  const { t, render } = useTranslation();
  const label = useLabel();
  if (!detail.native_cancellations.length) return null;
  return <section aria-labelledby="seqwf-v2-cancel" className="mt-4 text-sm">
    <h2 id="seqwf-v2-cancel" className="text-h2 text-text-primary">{t('seqwf.v2.cancellations')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.v2.cancellationsBody')}</p>
    <ol className="mt-2 flex flex-col gap-2">{detail.native_cancellations.map(c => <li key={c.event_id} className="border-border-default rounded-md border p-3 text-xs wrap-anywhere">
      <p className="flex flex-wrap items-center gap-2 text-sm font-medium">{t('seqwf.v2.controlHeading', { sequence: c.event_sequence, action: label(CANCEL_KIND, c.kind) })}
        <Pill tone="neutral">{label(CANCEL_ORIGIN, c.origin)}</Pill></p>
      <p className="mt-1">{render('seqwf.v2.cancelSource', {
        task: <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(c.source_task_id)}`}>{c.source_task_id}</Link>,
        status: <span>{label(TASK_STATUS, c.source_status)}</span> })}</p>
      <dl className="text-text-muted mt-1 grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
        <Fact label="seqwf.v2.eventId" value={c.event_id} />
        <Fact label="seqwf.v2.hold.visit" value={c.visit_id} />
        <Fact label="seqwf.v2.heldEvent" value={c.held_event_id ?? t('seqwf.v2.notSet')} />
      </dl>
    </li>)}</ol>
  </section>;
}

function Loaded({ slug, detail }: { slug: string; detail: Detail }): JSX.Element {
  const { t } = useTranslation();
  const defQ = useSequentialDefinition(detail.run.definition_id);
  const def = defQ.data ? parseDefinition(defQ.data.definition_json) : null;
  const facts = deriveRun(detail, def);
  const base = `/orgs/${slug}`;
  const run = detail.run;
  const links = taskLinks(run);
  const roleOf = (node: string) => def?.nodes.find(n => n.id === node)?.role ?? node;
  const token = useRecordedToken();
  return <>
    <div className="text-text-muted mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      <Link className="text-accent-default hover:underline" to={`${base}/workflows/definitions/${encodeURIComponent(run.definition_id)}`}>{t('seqwf.admittedVersion', { identity: run.definition.identity, version: run.definition.version })}</Link>
      <span>{t('seqwf.settingsGeneration', { generation: run.settings.generation })}</span>
      <span>{t('seqwf.counters', { visits: run.counters.visits, epoch: run.counters.epoch, returns: run.counters.content_returns })}</span>
      {links.source && <TaskRef label="seqwf.sourceTask" id={links.source} base={base} />}
      {links.carrier && <TaskRef label="seqwf.carrierTask" id={links.carrier} base={base} />}
    </div>
    <ReadOnlyNote />
    {defQ.refreshFailed && def && <StaleNotice message="seqwf.defStale" error={defQ.refreshError} refetch={defQ.refetch} />}
    <section aria-labelledby="seqwf-state" className="border-border-default bg-surface-raised mt-4 rounded-lg border p-4 text-sm wrap-anywhere">
      <h2 id="seqwf-state" className="text-h2 text-text-primary">{t('seqwf.currentState')}</h2>
      <p className="mt-2"><RunHeadlineText facts={facts} roleOf={roleOf} taskHref={id => `${base}/tasks/${encodeURIComponent(id)}`} /></p>
      {facts.headline.kind === 'exhausted' && <p className="mt-2">{t('seqwf.allowance', {
        returns: detail.reducer_counters.content_returns, returnsLimit: detail.effective_limits.content_returns,
        epoch: detail.reducer_counters.epoch, epochLimit: detail.effective_limits.max_content_epochs,
        visits: detail.reducer_counters.visits, visitsLimit: detail.effective_limits.max_visits,
        events: detail.reducer_counters.events, eventsLimit: detail.effective_limits.max_events,
      })}</p>}
      {facts.headline.kind === 'exhausted' && <p className="text-text-muted mt-1">{t('seqwf.exhaustedAction')}</p>}
      {detail.holds.length > 0 && <ul className="mt-3 flex flex-col gap-2">{detail.holds.map((h, i) => <li key={i} className="bg-attention-soft rounded-md px-3 py-2">
        <dl className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
          <div><dt className="text-text-muted text-xs">{t('seqwf.hold.cause')}</dt><dd>{token(h.cause)} <span className="font-mono text-xs">({h.cause})</span></dd></div>
          <div><dt className="text-text-muted text-xs">{t('seqwf.hold.nextOwner')}</dt><dd>{token(h.next_owner)}</dd></div>
          <div><dt className="text-text-muted text-xs">{t('seqwf.hold.action')}</dt><dd>{token(h.action)}</dd></div>
          {h.visit_id && <div><dt className="text-text-muted text-xs">{t('seqwf.hold.visit')}</dt><dd className="font-mono text-xs">{h.visit_id}</dd></div>}
        </dl></li>)}</ul>}
    </section>
    <section aria-labelledby="seqwf-request" className="mt-4 text-sm">
      <h2 id="seqwf-request" className="text-h2 text-text-primary">{t('seqwf.request')}</h2>
      <p className="text-text-muted text-xs">{t('seqwf.authored')}</p>
      <pre className="border-border-default mt-1 rounded-md border p-3 font-sans whitespace-pre-wrap wrap-anywhere">{detail.request}</pre>
      {detail.inputs.filter(i => i.name !== 'request').map(i => <div key={i.name} className="mt-2"><p className="text-text-muted font-mono text-xs">{i.name}</p>
        <pre className="border-border-default rounded-md border p-3 font-sans whitespace-pre-wrap wrap-anywhere">{i.value}</pre></div>)}
    </section>
    <Bindings detail={detail} def={def} />
    {def ? <>
      <Process slug={slug} detail={detail} def={def} facts={facts} />
      <Finalization detail={detail} def={def} facts={facts} />
    </> : defQ.isError && isAccessRefusal(defQ.error) ? null
      : defQ.isError || defQ.data ? <DefinitionUnavailable error={defQ.isError ? defQ.error : null} refetch={defQ.refetch} />
      : <p role="status" className="text-text-muted mt-4">{t('seqwf.loading')}</p>}
  </>;
}

/**
 * The admitted definition could not be read (non-refusal error) or shown structurally:
 * say so instead of silently dropping the process, exhaustion explanation and assessments.
 */
function DefinitionUnavailable({ error, refetch }: { error: Error | null; refetch: () => Promise<unknown> }): JSX.Element {
  const { t } = useTranslation();
  return <div role="alert" className="border-border-default mt-4 rounded-md border p-4 text-sm">
    <h2 className="text-text-primary font-medium">{t('seqwf.defUnavailable')}</h2>
    <p className="text-text-muted mt-1">{t('seqwf.defUnavailableBody')}</p>
    {error && <pre className="text-text-muted mt-2 text-xs whitespace-pre-wrap wrap-anywhere">{reviewDiagnostic(error)}</pre>}
    <Button className="mt-3" size="sm" variant="outline" onClick={() => void refetch()}>{t('workflows.refresh')}</Button>
  </div>;
}

function Bindings({ detail, def }: { detail: Detail; def: DefView | null }): JSX.Element {
  const { t } = useTranslation();
  const reviewerRoles = new Set(def?.nodes.filter(n => REVIEW_TYPES.has(n.output.type)).map(n => n.role) ?? []);
  return <section aria-labelledby="seqwf-bindings" className="mt-4 text-sm">
    <h2 id="seqwf-bindings" className="text-h2 text-text-primary">{t('seqwf.bindings')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.bindingsBody')}</p>
    <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">{detail.bindings.map(b => <li key={b.role} className="border-border-default rounded-md border p-3 wrap-anywhere">
      <p><span className="font-medium">{b.role}</span> — <span className="font-mono">{b.principal}</span></p>
      {reviewerRoles.has(b.role) && <>
        <p className="text-text-muted mt-1 text-xs">{t('seqwf.reviewerSelected')}</p>
        <p className="text-text-muted mt-1 text-xs">{t('seqwf.legacyReviewerDependency')}</p>
      </>}
    </li>)}</ul>
  </section>;
}

function Process({ slug, detail, def, facts }: { slug: string; detail: Detail; def: DefView; facts: RunFacts }): JSX.Element {
  const { t } = useTranslation();
  const base = `/orgs/${slug}`;
  const principal = (role: string) => detail.bindings.find(b => b.role === role)?.principal ?? '—';
  return <section aria-labelledby="seqwf-run-process" className="mt-4 text-sm">
    <h2 id="seqwf-run-process" className="text-h2 text-text-primary">{t('seqwf.process')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.processBody')}</p>
    <ol className="mt-2 flex flex-col gap-3">{def.nodes.map(n => {
      const visits = facts.byNode.get(n.id) ?? [];
      const exhaustedHere = facts.headline.kind === 'exhausted' && facts.headline.target.id === n.id;
      return <li key={n.id} className="border-border-default rounded-lg border p-3 wrap-anywhere">
        <h3 className="font-medium">{t('seqwf.nodeRole', { node: n.id, role: n.role })} — <span className="font-mono">{principal(n.role)}</span></h3>
        {visits.length === 0 && !exhaustedHere && <p className="text-text-muted mt-1">{t('seqwf.notCreated')}</p>}
        <ul className="mt-2 flex flex-col gap-2">{visits.map(v => <VisitRow key={v.id} v={v} base={base} detail={detail} active={facts.active?.id === v.id} />)}</ul>
        {exhaustedHere && <p className="bg-attention-soft mt-2 rounded-md px-3 py-2">{facts.headline.kind === 'exhausted' && facts.headline.limit !== 'revision'
          ? t('seqwf.noFurtherTaskCeiling', { role: n.role, limit: t(facts.headline.limit === 'visits' ? 'seqwf.limit.visits' : 'seqwf.limit.events') })
          : t('seqwf.noFurtherTask', { role: n.role })}</p>}
      </li>;
    })}</ol>
  </section>;
}

function VisitRow({ v, base, detail, active }: { v: Visit; base: string; detail: Detail; active: boolean }): JSX.Element {
  const { t } = useTranslation();
  const visitState = useVisitState();
  const outputs = detail.outputs.filter(o => o.visit_id === v.id);
  return <li aria-current={active ? 'step' : undefined} className={`bg-surface-raised rounded-md border p-3 ${active ? 'border-accent-default' : 'border-border-default'}`}>
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-medium">{t('seqwf.visitLabel', { ordinal: v.ordinal })}</span>
      <span className="text-text-muted text-xs">{t('seqwf.revisionLabel', { epoch: v.epoch })}</span>
      <span className="text-text-muted text-xs">{visitState(v.state)}</span>
      {v.outcome && <OutcomeBadge outcome={v.outcome} />}
      {active && <span className="text-accent-default text-xs font-medium">{t('seqwf.activeNow')}</span>}
    </div>
    <div className="mt-1 flex flex-wrap gap-x-4 text-xs"><TaskRef label="seqwf.visitTask" id={v.task_id} base={base} /><span className="text-text-muted font-mono">{v.principal}</span></div>
    {v.findings.length > 0 && <div className="mt-2"><p className="text-text-muted text-xs">{t('seqwf.findings')} · {t('seqwf.authored')}</p>
      <ul className="mt-1 flex flex-col gap-1">{v.findings.map((f, i) => <li key={i} className="border-border-default border-l-2 pl-2 whitespace-pre-wrap">{f}</li>)}</ul></div>}
    {outputs.map(o => <details key={o.id} className="mt-2">
      <summary className="cursor-pointer text-xs">{t('seqwf.outputSummary', { slot: o.slot, contract: o.contract, epoch: o.epoch })}</summary>
      <p className="text-text-muted mt-1 font-mono text-xs break-all">{o.content_digest}</p>
      <pre className="border-border-default mt-1 max-h-64 overflow-auto rounded-md border p-2 text-xs whitespace-pre-wrap break-all">{o.content_utf8}</pre>
    </details>)}
  </li>;
}

/** Recorded assessments and pending finalization rechecks are separate facts. */
function Finalization({ detail, def, facts }: { detail: Detail; def: DefView; facts: RunFacts }): JSX.Element | null {
  const { t } = useTranslation();
  const visitState = useVisitState();
  const assessed = def.nodes.filter(n => ASSESSMENT_TYPES.has(n.output.type));
  if (!assessed.length && !facts.pendingFinal.length) return null;
  const latest = (node: string) => [...(facts.byNode.get(node) ?? [])].reverse().find(v => v.outcome !== null) ?? null;
  return <section aria-labelledby="seqwf-final" className="border-border-default mt-4 rounded-lg border p-4 text-sm wrap-anywhere">
    <h2 id="seqwf-final" className="text-h2 text-text-primary">{t('seqwf.assessments')}</h2>
    <p className="text-text-muted text-xs">{t('seqwf.assessmentsBody')}</p>
    <ul className="mt-2 flex flex-col gap-1">{assessed.map(n => {
      const v = latest(n.id);
      return <li key={n.id} className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{n.id}</span>
        {v ? <><OutcomeBadge outcome={v.outcome!} /><span className="text-text-muted text-xs">{t('seqwf.recordedFor', { ordinal: v.ordinal, epoch: v.epoch })}</span>
          {v.epoch < detail.run.counters.epoch && <span className="text-text-muted text-xs">{t('seqwf.earlierRevision')}</span>}</>
          : <span className="text-text-muted">{t('seqwf.noAssessmentYet')}</span>}
      </li>;
    })}</ul>
    {facts.pendingFinal.map(v => <p key={v.id} className="bg-info-soft mt-2 rounded-md px-3 py-2">{t('seqwf.pendingRecheck', { node: v.node_id, ordinal: v.ordinal, state: visitState(v.state) })}</p>)}
  </section>;
}

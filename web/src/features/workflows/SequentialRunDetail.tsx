/**
 * U8a run detail. Every statement comes from the recorded run projection and
 * its admitted definition version: visits/outcomes/findings verbatim, real
 * task links, "Task not created yet" for nodes without a visit, and no
 * successor claim unless its visit exists. Read-only.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialRun } from '@/hooks/workflows';
import { deriveRun, parseDefinition, taskLinks, type DefView, type RunFacts, type SequentialRunDetail as Detail, type Visit } from '@/lib/workflowSequentialView';
import { OutcomeBadge, RunHeadlineText, RunStateBadge, useRecordedToken, useVisitState } from '@/shared/workflows/SequentialRunPresentation';
import { ReadOnlyNote, ReadState, TaskRef } from './SequentialCommon';

const ASSESSMENT_TYPES = new Set(['independent-code-review@1', 'code-qa@1', 'document-review@1']);
const REVIEW_TYPES = new Set(['independent-code-review@1', 'document-review@1']);

export function SequentialRunDetail({ slug, runId }: { slug: string; runId: string }): JSX.Element {
  const q = useSequentialRun(runId);
  return <ReadState q={q}>{() => <Loaded slug={slug} detail={q.data!} />}</ReadState>;
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
    <div className="flex flex-wrap items-center gap-2">
      <h1 className="font-display text-h1 text-text-primary font-mono wrap-anywhere">{run.id}</h1>
      <RunStateBadge state={run.state} />
    </div>
    <div className="text-text-muted mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      <Link className="text-accent-default hover:underline" to={`${base}/workflows/definitions/${encodeURIComponent(run.definition_id)}`}>{t('seqwf.admittedVersion', { identity: run.definition.identity, version: run.definition.version })}</Link>
      <span>{t('seqwf.settingsGeneration', { generation: run.settings.generation })}</span>
      <span>{t('seqwf.counters', { visits: run.counters.visits, epoch: run.counters.epoch, returns: run.counters.content_returns })}</span>
      {links.source && <TaskRef label="seqwf.sourceTask" id={links.source} base={base} />}
      {links.carrier && <TaskRef label="seqwf.carrierTask" id={links.carrier} base={base} />}
    </div>
    <ReadOnlyNote />
    <section aria-labelledby="seqwf-state" className="border-border-default bg-bg-raised mt-4 rounded-lg border p-4 text-sm wrap-anywhere">
      <h2 id="seqwf-state" className="text-h2 text-text-primary">{t('seqwf.currentState')}</h2>
      <p className="mt-2"><RunHeadlineText facts={facts} roleOf={roleOf} taskHref={id => `${base}/tasks/${encodeURIComponent(id)}`} /></p>
      {facts.headline.kind === 'exhausted' && <p className="mt-2">{t('seqwf.allowance', {
        returns: run.counters.content_returns, returnsLimit: Math.min(detail.limits.content_returns, def?.limits.content_returns ?? Infinity),
        epoch: run.counters.epoch, epochLimit: Math.min(detail.limits.max_content_epochs, def?.limits.max_content_epochs ?? Infinity),
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
    {def ? <Process slug={slug} detail={detail} def={def} facts={facts} /> : defQ.isError ? null : <p className="text-text-muted mt-4">{t('seqwf.loading')}</p>}
    {def && <Finalization detail={detail} def={def} facts={facts} />}
  </>;
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
        {exhaustedHere && <p className="bg-attention-soft mt-2 rounded-md px-3 py-2">{t('seqwf.noFurtherTask', { role: n.role })}</p>}
      </li>;
    })}</ol>
  </section>;
}

function VisitRow({ v, base, detail, active }: { v: Visit; base: string; detail: Detail; active: boolean }): JSX.Element {
  const { t } = useTranslation();
  const visitState = useVisitState();
  const outputs = detail.outputs.filter(o => o.visit_id === v.id);
  return <li aria-current={active ? 'step' : undefined} className={`bg-bg-raised rounded-md border p-3 ${active ? 'border-accent-default' : 'border-border-default'}`}>
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

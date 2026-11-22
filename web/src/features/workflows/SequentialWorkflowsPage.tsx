/**
 * Common Workflows journey: Definitions, definition overview, Setup (U8b),
 * Runs and run detail. Agents initiate runs from their tasks; no view has a
 * start, retry, reassign or merge control, and only Setup edits configuration
 * (current default agents and separate new-run admission; never repositories).
 */
import type { ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialDefinitions, useSequentialRunsV2 } from '@/hooks/workflows';
import type { GenericSequentialRun, SequentialRun } from '@/shared/workflows/SequentialTaskBanner';
import { parseDefinition, taskLinks, type DefEdge, type DefView } from '@/lib/workflowSequentialView';
import { RunStateBadge } from '@/shared/workflows/SequentialRunPresentation';
import { GenericRunState, HistoricalLabel, HoldFacts, WorkflowSetupStatusCard } from '@/shared/workflows/SequentialTaskBanner';
import { SequentialRunDetail } from './SequentialRunDetail';
import { SequentialSetup } from './SequentialSetup';
import { ReadOnlyNote, ReadState, RunTaskLinks, TaskRef, V2ReadState } from './SequentialCommon';

export function SequentialWorkflowsPage({ view }: { view: 'definitions' | 'setup' | 'runs' | 'definition' | 'run' }): JSX.Element {
  const { slug = '', definitionId = '', runId = '' } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const base = `/orgs/${slug}/workflows`;
  // THR-376 r2 W1: on every Workflows route the breadcrumb + page h1 come first and the
  // setup status card follows; detail views place the card directly after their own h1.
  return <ContentWrap>
    <nav aria-label={t('seqwf.breadcrumb')} className="text-text-muted mb-3 flex flex-wrap items-center gap-1 text-xs">
      <Link className="hover:underline" to={base}>{t('seqwf.title')}</Link>
      {view !== 'definitions' && <><span aria-hidden="true">›</span>
        {view === 'setup' ? <span className="text-text-primary">{t('seqwf.tab.setup')}</span>
          : <Link className="hover:underline" to={view === 'runs' || view === 'run' ? `${base}/runs` : base}>{t(view === 'runs' || view === 'run' ? 'seqwf.tab.runs' : 'seqwf.tab.definitions')}</Link>}</>}
      {(view === 'definition' || view === 'run') && <><span aria-hidden="true">›</span><span className="text-text-primary font-mono wrap-anywhere">{view === 'run' ? runId : definitionId}</span></>}
    </nav>
    {view === 'definitions' || view === 'runs' || view === 'setup' ? <>
      <PageTitle title={t('seqwf.title')} />
      <WorkflowSetupStatusCard key={slug} />
      {view === 'setup' ? <p className="bg-info-soft text-text-primary mt-3 rounded-md px-3 py-2 text-sm">{t('seqwf.setup.note')}</p> : <ReadOnlyNote />}
      {/* THR-376 r2 W2: shared accent-soft SegmentedControl pill; picking a view navigates. */}
      <SegmentedControl className="mt-4" aria-label={t('seqwf.tabsAria')} value={view}
        onValueChange={(next) => { if (next !== view) navigate(next === 'definitions' ? base : `${base}/${next}`); }}
        options={[
          { value: 'definitions', label: t('seqwf.tab.definitions') },
          { value: 'setup', label: t('seqwf.tab.setup') },
          { value: 'runs', label: t('seqwf.tab.runs') },
        ]} />
      <div className="mt-4">{view === 'definitions' ? <DefinitionsList key={slug} base={base} />
        : view === 'setup' ? <SequentialSetup key={slug} slug={slug} /> : <RunsList key={slug} slug={slug} />}</div>
    </> : view === 'definition'
      ? <DefinitionOverview key={JSON.stringify([slug, definitionId])} id={definitionId} setupCard={<WorkflowSetupStatusCard key={slug} />} />
      : <SequentialRunDetail key={JSON.stringify([slug, runId])} slug={slug} runId={runId} setupCard={<WorkflowSetupStatusCard key={slug} />} />}
  </ContentWrap>;
}

function Unavailable(): JSX.Element {
  const { t } = useTranslation();
  return <div className="border-border-default rounded-md border p-4" role="status"><p className="font-medium">{t('seqwf.unavailable')}</p><p className="text-text-muted mt-1 text-sm">{t('seqwf.unavailableBody')}</p></div>;
}

function DefinitionsList({ base }: { base: string }): JSX.Element {
  const { t } = useTranslation();
  const q = useSequentialDefinitions();
  return <ReadState q={q}>{() => {
    const data = q.data!;
    if (data.storage !== 'storage readable') return <Unavailable />;
    if (!data.definitions.length) return <p className="text-text-muted">{t('seqwf.definitionsEmpty')}</p>;
    return <ul className="flex flex-col gap-3">{data.definitions.map(d => {
      const parsed = parseDefinition(d.definition_json);
      return <li key={d.id} className="border-border-default bg-surface-raised rounded-lg border p-4 wrap-anywhere">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0"><h2 className="text-h2 text-text-primary">{t('seqwf.versionLink', { identity: d.pin.identity, version: d.pin.version })}</h2>
            {parsed && <p className="text-text-primary mt-1 text-sm">{parsed.description}</p>}
            <p className="text-text-muted mt-1 text-xs">{parsed ? t('seqwf.defCounts', { roles: parsed.roles.length, nodes: parsed.nodes.length }) : t('seqwf.defUnparsed')}</p></div>
          <Link className="text-accent-default border-border-default shrink-0 rounded-md border px-3 py-1 text-sm hover:underline" to={`${base}/definitions/${encodeURIComponent(d.id)}`}>{t('seqwf.inspect')}</Link>
        </div>
      </li>;
    })}</ul>;
  }}</ReadState>;
}

/**
 * S7b: the plural @2 runs projection is the only source. Every link keeps the projection's
 * stable run-ID order; @1 entries carry the explicit historical label and their @1 summary.
 */
function RunsList({ slug }: { slug: string }): JSX.Element {
  const { t } = useTranslation();
  const q = useSequentialRunsV2();
  const base = `/orgs/${slug}`;
  return <V2ReadState q={q}>{data => {
    if (data.storage === 'absent') return <p className="text-text-muted">{t('seqwf.v2.storageAbsent')}</p>;
    if (!data.runs.length) return <p className="text-text-muted">{t('seqwf.runsEmpty')}</p>;
    return <ul className="flex flex-col gap-3" aria-label={t('seqwf.tab.runs')}>{data.runs.map((link, i) => link.abi === '@1'
      ? <HistoricalRunItem key={`${i}:${link.run.id}`} r={link.run} base={base} />
      : <GenericRunItem key={`${i}:${link.id}`} r={link} base={base} />)}</ul>;
  }}</V2ReadState>;
}

function GenericRunItem({ r, base }: { r: GenericSequentialRun; base: string }): JSX.Element {
  const { t, render } = useTranslation();
  return <li className="border-border-default bg-surface-raised rounded-lg border p-4 text-sm wrap-anywhere">
    <div className="flex flex-wrap items-center gap-2">
      <Link className="text-accent-default font-mono font-medium hover:underline" to={`${base}/workflows/runs/${encodeURIComponent(r.id)}`}>{r.id}</Link>
      <GenericRunState run={r} />
      <span className="text-text-muted">{t('seqwf.versionLink', { identity: r.definition.identity, version: r.definition.version })}</span>
    </div>
    <p className="mt-1 text-xs">{render('seqwf.v2.initiator', {
      task: <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(r.initiator_task_id)}`}>{r.initiator_task_id}</Link>,
      agent: <span className="font-mono">{r.initiator_agent_id}</span>,
    })}</p>
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.v2.counts', { visits: r.visits, results: r.result_events, stored: r.stored_events, recoveries: r.recoveries })}</p>
    {r.state === 'held' && r.hold && <div className="mt-2"><HoldFacts hold={r.hold} /></div>}
    <div className="mt-2"><RunTaskLinks tasks={r.tasks} base={base} /></div>
  </li>;
}

/** The original @1 summary, unchanged apart from the explicit historical label. */
function HistoricalRunItem({ r, base }: { r: SequentialRun; base: string }): JSX.Element {
  const { t } = useTranslation();
  const links = taskLinks(r);
  return <li className="border-border-default bg-surface-raised rounded-lg border p-4 text-sm wrap-anywhere">
    <div className="flex flex-wrap items-center gap-2">
      <HistoricalLabel />
      <Link className="text-accent-default font-mono font-medium hover:underline" to={`${base}/workflows/runs/${encodeURIComponent(r.id)}`}>{r.id}</Link>
      <RunStateBadge state={r.state} />
      <Link className="text-text-muted hover:underline" to={`${base}/workflows/definitions/${encodeURIComponent(r.definition_id)}`}>{t('seqwf.versionLink', { identity: r.definition.identity, version: r.definition.version })}</Link>
    </div>
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.counters', { visits: r.counters.visits, epoch: r.counters.epoch, returns: r.counters.content_returns })}</p>
    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {links.source && <TaskRef label="seqwf.sourceTask" id={links.source} base={base} />}
      {links.activeTask && <TaskRef label="seqwf.activeTask" id={links.activeTask} base={base} />}
      {r.state === 'completed' && <span className="text-text-muted">{t('seqwf.noActiveNode')}</span>}
    </div>
  </li>;
}

export function useEdgeText(): (outcome: string, edge: DefEdge) => string {
  const { t } = useTranslation();
  return (outcome, e) => e.action === 'next'
    ? t(e.consume_counter ? 'seqwf.edge.return' : 'seqwf.edge.next', { outcome, node: e.node })
    : e.action === 'hold' ? t('seqwf.edge.hold', { outcome })
      : e.action === 'fail' ? t('seqwf.edge.fail', { outcome, code: e.code })
        : t('seqwf.edge.terminal', { outcome, condition: e.condition });
}

/** The setup card keeps one tree slot in every read state (loading, error, loaded) so it never remounts. */
function DefinitionOverview({ id, setupCard }: { id: string; setupCard: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  const q = useSequentialDefinition(id);
  return <>
    <ReadState q={q}>{() => <h1 className="font-display text-h1 text-text-primary wrap-anywhere">{t('seqwf.versionLink', { identity: q.data!.pin.identity, version: q.data!.pin.version })}</h1>}</ReadState>
    {setupCard}
    {!q.isError && q.data && <DefinitionLoaded d={q.data} />}
  </>;
}

function DefinitionLoaded({ d }: { d: NonNullable<ReturnType<typeof useSequentialDefinition>['data']> }): JSX.Element {
  const { t } = useTranslation();
  const parsed = parseDefinition(d.definition_json);
  return <>
    <ReadOnlyNote />
    <dl className="text-text-muted mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
      <div><dt className="inline">{t('seqwf.definitionDigest')} </dt><dd className="inline font-mono break-all">{d.pin.definition_digest}</dd></div>
      <div><dt className="inline">{t('seqwf.sourceDigest')} </dt><dd className="inline font-mono break-all">{d.pin.source_digest}</dd></div>
    </dl>
    {parsed ? <DefinitionBody def={parsed} /> : <section className="mt-4"><p>{t('seqwf.defUnparsed')}</p><pre className="mt-2 text-xs whitespace-pre-wrap break-all">{d.definition_json}</pre></section>}
  </>;
}

function DefinitionBody({ def }: { def: DefView }): JSX.Element {
  const { t } = useTranslation();
  const edgeText = useEdgeText();
  return <>
    <p className="text-text-primary mt-3">{def.description}</p>
    <section className="mt-6" aria-labelledby="seqwf-process">
      <h2 id="seqwf-process" className="text-h2 text-text-primary">{t('seqwf.process')}</h2>
      <ol className="mt-3 flex flex-col gap-3">{def.nodes.map((n, index) => <li key={n.id} className="border-border-default bg-surface-raised rounded-lg border p-4 text-sm wrap-anywhere">
        <h3 className="font-medium">{t('seqwf.nodeHeading', { index: index + 1, node: n.id, role: n.role })}{n.id === def.entry ? ` · ${t('seqwf.entry')}` : ''}</h3>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.responsibilities')}</p>
        <ul className="ml-6 list-disc">{n.responsibilities.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.inputs')}</p>
        <p className="font-mono text-xs">{n.inputs.map(i => `${i.name} ← ${i.source}${i.required ? '' : ` (${t('seqwf.optional')})`}`).join(' · ') || '—'}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.output')}</p>
        <p className="font-mono text-xs">{`${n.output.slot}: ${n.output.type}`}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.checks')}</p>
        <p className="font-mono text-xs">{n.checks.join(' · ')}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.outcomes')}</p>
        <ul className="ml-6 list-disc">{n.outcomes.map(o => <li key={o.outcome}>{edgeText(o.outcome, o.edge)}</li>)}</ul>
      </li>)}</ol>
    </section>
    <section className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="border-border-default rounded-lg border p-4 text-sm wrap-anywhere">
        <h2 className="text-h2 text-text-primary">{t('seqwf.roles')}</h2>
        <ul className="mt-2 flex flex-col gap-1">{def.roles.map(r => <li key={r.name}><span className="font-medium">{r.name}</span> — {t('seqwf.roleLine', { eligibility: r.eligibility, independent: r.independentOf.join(', ') || '—' })}</li>)}</ul>
      </div>
      <div className="border-border-default rounded-lg border p-4 text-sm wrap-anywhere">
        <h2 className="text-h2 text-text-primary">{t('seqwf.limitsHeading')}</h2>
        <p className="mt-2">{t('seqwf.limits', { returns: def.limits.content_returns, epochs: def.limits.max_content_epochs, visits: def.limits.max_visits, events: def.limits.max_events })}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.runInputs')}</p>
        <p className="font-mono text-xs">{def.inputs.map(i => `${i.name}: ${i.contract}`).join(' · ')}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.terminals')}</p>
        <ul className="font-mono text-xs">{def.terminals.map(c => <li key={c.name}>{`${c.name}: ${c.output} [${c.requires.join(', ')}]`}</li>)}</ul>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.noRepositoryInSetup')}</p>
      </div>
    </section>
  </>;
}

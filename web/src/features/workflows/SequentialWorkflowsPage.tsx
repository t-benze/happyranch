/**
 * U8a common Workflows journey (read-only): Definitions, definition overview,
 * Runs and run detail. Agents initiate runs from their tasks; this surface has
 * no start, retry, reassign, merge or configuration control.
 */
import { Link, useParams } from 'react-router-dom';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { SubTabBar } from '@/design-system/primitives/SubTabBar';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialDefinitions, useSequentialRuns } from '@/hooks/workflows';
import { parseDefinition, taskLinks, type DefEdge, type DefView } from '@/lib/workflowSequentialView';
import { RunStateBadge } from '@/shared/workflows/SequentialRunPresentation';
import { SequentialRunDetail } from './SequentialRunDetail';
import { ReadOnlyNote, ReadState, TaskRef } from './SequentialCommon';

export function SequentialWorkflowsPage({ view }: { view: 'definitions' | 'runs' | 'definition' | 'run' }): JSX.Element {
  const { slug = '', definitionId = '', runId = '' } = useParams();
  const { t } = useTranslation();
  const base = `/orgs/${slug}/workflows`;
  return <ContentWrap>
    <nav aria-label={t('seqwf.breadcrumb')} className="text-text-muted mb-3 flex flex-wrap items-center gap-1 text-xs">
      <Link className="hover:underline" to={base}>{t('seqwf.title')}</Link>
      {view !== 'definitions' && <><span aria-hidden="true">›</span>
        <Link className="hover:underline" to={view === 'runs' || view === 'run' ? `${base}/runs` : base}>{t(view === 'runs' || view === 'run' ? 'seqwf.tab.runs' : 'seqwf.tab.definitions')}</Link></>}
      {(view === 'definition' || view === 'run') && <><span aria-hidden="true">›</span><span className="text-text-primary font-mono wrap-anywhere">{view === 'run' ? runId : definitionId}</span></>}
    </nav>
    {view === 'definitions' || view === 'runs' ? <>
      <h1 className="font-display text-h1 text-text-primary">{t('seqwf.title')}</h1>
      <ReadOnlyNote />
      <SubTabBar className="mt-4" active={view} tabs={[
        { value: 'definitions', label: t('seqwf.tab.definitions'), to: base },
        { value: 'runs', label: t('seqwf.tab.runs'), to: `${base}/runs` },
      ]} />
      <div className="mt-4">{view === 'definitions' ? <DefinitionsList key={slug} base={base} /> : <RunsList key={slug} slug={slug} />}</div>
    </> : view === 'definition'
      ? <DefinitionOverview key={JSON.stringify([slug, definitionId])} id={definitionId} />
      : <SequentialRunDetail key={JSON.stringify([slug, runId])} slug={slug} runId={runId} />}
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
      return <li key={d.id} className="border-border-default bg-bg-raised rounded-lg border p-4 wrap-anywhere">
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

function RunsList({ slug }: { slug: string }): JSX.Element {
  const { t } = useTranslation();
  const q = useSequentialRuns();
  const base = `/orgs/${slug}`;
  return <ReadState q={q}>{() => {
    const data = q.data!;
    if (data.storage !== 'storage readable') return <Unavailable />;
    if (!data.runs.length) return <p className="text-text-muted">{t('seqwf.runsEmpty')}</p>;
    return <ul className="flex flex-col gap-3" aria-label={t('seqwf.tab.runs')}>{data.runs.map(r => {
      const links = taskLinks(r);
      return <li key={r.id} className="border-border-default bg-bg-raised rounded-lg border p-4 text-sm wrap-anywhere">
        <div className="flex flex-wrap items-center gap-2">
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
    })}</ul>;
  }}</ReadState>;
}

export function useEdgeText(): (outcome: string, edge: DefEdge) => string {
  const { t } = useTranslation();
  return (outcome, e) => e.action === 'next'
    ? t(e.consume_counter ? 'seqwf.edge.return' : 'seqwf.edge.next', { outcome, node: e.node })
    : e.action === 'hold' ? t('seqwf.edge.hold', { outcome })
      : e.action === 'fail' ? t('seqwf.edge.fail', { outcome, code: e.code })
        : t('seqwf.edge.terminal', { outcome, condition: e.condition });
}

function DefinitionOverview({ id }: { id: string }): JSX.Element {
  const { t } = useTranslation();
  const q = useSequentialDefinition(id);
  return <ReadState q={q}>{() => {
    const d = q.data!;
    const parsed = parseDefinition(d.definition_json);
    return <>
      <h1 className="font-display text-h1 text-text-primary wrap-anywhere">{t('seqwf.versionLink', { identity: d.pin.identity, version: d.pin.version })}</h1>
      <ReadOnlyNote />
      <dl className="text-text-muted mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div><dt className="inline">{t('seqwf.definitionDigest')} </dt><dd className="inline font-mono break-all">{d.pin.definition_digest}</dd></div>
        <div><dt className="inline">{t('seqwf.sourceDigest')} </dt><dd className="inline font-mono break-all">{d.pin.source_digest}</dd></div>
      </dl>
      {parsed ? <DefinitionBody def={parsed} /> : <section className="mt-4"><p>{t('seqwf.defUnparsed')}</p><pre className="mt-2 text-xs whitespace-pre-wrap break-all">{d.definition_json}</pre></section>}
    </>;
  }}</ReadState>;
}

function DefinitionBody({ def }: { def: DefView }): JSX.Element {
  const { t } = useTranslation();
  const edgeText = useEdgeText();
  return <>
    <p className="text-text-primary mt-3">{def.description}</p>
    <section className="mt-5" aria-labelledby="seqwf-process">
      <h2 id="seqwf-process" className="text-h2 text-text-primary">{t('seqwf.process')}</h2>
      <ol className="mt-3 flex flex-col gap-3">{def.nodes.map((n, index) => <li key={n.id} className="border-border-default bg-bg-raised rounded-lg border p-4 text-sm wrap-anywhere">
        <h3 className="font-medium">{t('seqwf.nodeHeading', { index: index + 1, node: n.id, role: n.role })}{n.id === def.entry ? ` · ${t('seqwf.entry')}` : ''}</h3>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.responsibilities')}</p>
        <ul className="ml-5 list-disc">{n.responsibilities.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.inputs')}</p>
        <p className="font-mono text-xs">{n.inputs.map(i => `${i.name} ← ${i.source}${i.required ? '' : ` (${t('seqwf.optional')})`}`).join(' · ') || '—'}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.output')}</p>
        <p className="font-mono text-xs">{`${n.output.slot}: ${n.output.type}`}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.checks')}</p>
        <p className="font-mono text-xs">{n.checks.join(' · ')}</p>
        <p className="text-text-muted mt-2 text-xs">{t('seqwf.outcomes')}</p>
        <ul className="ml-5 list-disc">{n.outcomes.map(o => <li key={o.outcome}>{edgeText(o.outcome, o.edge)}</li>)}</ul>
      </li>)}</ol>
    </section>
    <section className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2">
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

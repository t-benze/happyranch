/**
 * U8a source/node task banner. Shows the linked run's OWN recorded state,
 * independent of the task's status. A refused read renders a fixed denied note
 * and discloses no run id, link, request, output or finding.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialRun, useTaskSequentialWorkflow, useWorkflowSetupStatus } from '@/hooks/workflows';
import type { MessageKey } from '@/lib/i18n';
import { deriveRun, parseDefinition } from '@/lib/workflowSequentialView';
import { RunHeadlineText, RunStateBadge } from './SequentialRunPresentation';

export function isAccessRefusal(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  return status === 403 || status === 404;
}

const REL: Record<string, MessageKey> = { source: 'seqwf.banner.relSource', carrier: 'seqwf.banner.relCarrier', visit: 'seqwf.banner.relVisit' };

export function SequentialTaskBanner({ slug, taskId }: { slug: string; taskId: string }): JSX.Element | null {
  const { t } = useTranslation();
  const link = useTaskSequentialWorkflow(taskId);
  const status = (link.error as { status?: number } | null)?.status;
  if (link.isError && status === 404 && (link.error as { code?: string } | null)?.code === 'task_not_found') return null;
  if (link.isError) return <BannerShell>{isAccessRefusal(link.error) ? <Denied /> :
    <div className="flex flex-wrap items-center gap-2" role="alert"><span>{t('seqwf.banner.error')}</span>
      <Button size="sm" variant="outline" onClick={() => void link.refetch()}>{t('workflows.refresh')}</Button></div>}{!isAccessRefusal(link.error) && <WorkflowSetupStatusCard />}</BannerShell>;
  const runId = link.data?.run?.id;
  if (!runId) return null;
  return <LinkedRun key={JSON.stringify([slug, taskId, runId])} slug={slug} taskId={taskId} runId={runId} linkStale={!!link.refreshFailed} refreshLink={link.refetch} />;
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
  return <section aria-label={t('seqwf.setupStatus.title')} className="border-border-default bg-bg-raised my-3 rounded-md border p-3 text-sm wrap-anywhere">
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
  return <section aria-label={t('seqwf.banner.label')} className="border-border-default bg-bg-raised mt-3 rounded-md border px-3 py-2 text-sm wrap-anywhere">{children}</section>;
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

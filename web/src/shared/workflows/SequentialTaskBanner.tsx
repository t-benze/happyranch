/**
 * U8a source/node task banner. Shows the linked run's OWN recorded state,
 * independent of the task's status. A refused read renders a fixed denied note
 * and discloses no run id, link, request, output or finding.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useSequentialDefinition, useSequentialRun, useTaskSequentialWorkflow } from '@/hooks/workflows';
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
      <Button size="sm" variant="outline" onClick={() => void link.refetch()}>{t('workflows.refresh')}</Button></div>}</BannerShell>;
  const runId = link.data?.run?.id;
  if (!runId) return null;
  return <LinkedRun key={JSON.stringify([slug, taskId, runId])} slug={slug} taskId={taskId} runId={runId} linkStale={!!link.refreshFailed} refreshLink={link.refetch} />;
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
  if (detail.isError && isAccessRefusal(detail.error)) return <BannerShell><Denied /></BannerShell>;
  if (!accepted) {
    return <BannerShell>{detail.isError
      ? <div className="flex flex-wrap items-center gap-2" role="alert"><span>{t('seqwf.banner.detailError')}</span>
        <Button size="sm" variant="outline" onClick={() => void detail.refetch()}>{t('workflows.refresh')}</Button></div>
      : <p role="status">{t('seqwf.loading')}</p>}</BannerShell>;
  }
  const run = accepted.run;
  const parsed = def.data ? parseDefinition(def.data.definition_json) : null;
  const facts = deriveRun(accepted, parsed);
  const roleOf = (node: string) => parsed?.nodes.find(n => n.id === node)?.role ?? node;
  const rel = REL[run.tasks.find(x => x.task_id === taskId)?.relationship ?? ''] ?? null;
  const base = `/orgs/${slug}`;
  return <BannerShell>
    {(detail.refreshFailed || linkStale) && <div role="status" className="bg-attention-soft mb-2 flex flex-wrap items-center gap-2 rounded-md px-2 py-1">
      <span>{t('seqwf.stale')}</span>
      <Button size="sm" variant="outline" onClick={() => { void refreshLink(); void detail.refetch(); }}>{t('workflows.refresh')}</Button></div>}
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
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.banner.independent')}</p>
  </BannerShell>;
}

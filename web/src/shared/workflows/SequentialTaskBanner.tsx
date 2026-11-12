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
import type { SequentialRun } from '@/lib/workflowSequentialView';
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
  const run = link.data?.run;
  if (!run) return null;
  return <LinkedRun key={JSON.stringify([slug, taskId, run.id])} slug={slug} taskId={taskId} run={run} rel={REL[run.tasks.find(x => x.task_id === taskId)?.relationship ?? ''] ?? null} />;
}

function BannerShell({ children }: { children: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  return <section aria-label={t('seqwf.banner.label')} className="border-border-default bg-bg-raised mt-3 rounded-md border px-3 py-2 text-sm wrap-anywhere">{children}</section>;
}
function Denied(): JSX.Element {
  const { t } = useTranslation();
  return <p role="status">{t('seqwf.banner.denied')}</p>;
}

function LinkedRun({ slug, taskId, run, rel }: { slug: string; taskId: string; run: SequentialRun; rel: MessageKey | null }): JSX.Element {
  const { t, render } = useTranslation();
  const detail = useSequentialRun(run.id);
  const def = useSequentialDefinition(run.definition_id);
  if (detail.isError && isAccessRefusal(detail.error)) return <BannerShell><Denied /></BannerShell>;
  const parsed = def.data ? parseDefinition(def.data.definition_json) : null;
  const facts = detail.data && detail.data.run.id === run.id ? deriveRun(detail.data, parsed) : null;
  const roleOf = (node: string) => parsed?.nodes.find(n => n.id === node)?.role ?? node;
  const base = `/orgs/${slug}`;
  return <BannerShell>
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
    <p className="mt-1">{facts ? <RunHeadlineText facts={facts} roleOf={roleOf} taskHref={id => `${base}/tasks/${encodeURIComponent(id)}`} />
      : detail.isError ? t('seqwf.banner.detailError') : t('seqwf.loading')}</p>
    <p className="text-text-muted mt-1 text-xs">{t('seqwf.banner.independent')}</p>
  </BannerShell>;
}

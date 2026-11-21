/** Shared read-state and link pieces for the U8a Workflows pages and the S7b @2 projections. */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { SequentialProjection, SequentialRun } from '@/shared/workflows/SequentialTaskBanner';
import { isAccessRefusal, isLateOrgResponse, isProjectionUnavailable, Pill, ProjectionUnavailable } from '@/shared/workflows/SequentialTaskBanner';
import { reviewDiagnostic } from './strings';

export function ReadOnlyNote(): JSX.Element {
  const { t } = useTranslation();
  return <p className="bg-info-soft text-text-primary mt-3 rounded-md px-3 py-2 text-sm">{t('seqwf.readOnly')}</p>;
}

/** PRD F6: last-loaded data stays visible; this warning says it may be out of date. Distinct from a refusal. */
export function StaleNotice({ error, refetch, message = 'seqwf.stale' }: { error: Error | null | undefined; refetch: () => Promise<unknown>; message?: MessageKey }): JSX.Element {
  const { t } = useTranslation();
  return <div role="status" className="bg-attention-soft text-text-primary mt-3 flex flex-wrap items-center gap-2 rounded-md px-3 py-2 text-sm">
    <span>{t(message)}</span>
    {error && <span className="text-text-muted font-mono text-xs wrap-anywhere">{reviewDiagnostic(error)}</span>}
    <Button size="sm" variant="outline" onClick={() => void refetch()}>{t('workflows.refresh')}</Button>
  </div>;
}

/** Refusal shows a fixed message only; other failures keep the raw daemon code verbatim. */
export function ReadState({ q, children }: { q: { isLoading: boolean; isError: boolean; error: Error | null; data: unknown; refetch: () => Promise<unknown>; refreshFailed?: boolean; refreshError?: Error | null }; children: () => ReactNode }): JSX.Element {
  const { t } = useTranslation();
  if (q.isError) {
    return isAccessRefusal(q.error)
      ? <div role="alert" className="border-border-default rounded-md border p-4"><h2 className="text-text-primary font-medium">{t('seqwf.denied')}</h2><p className="text-text-muted mt-1 text-sm">{t('seqwf.deniedBody')}</p></div>
      : <div role="alert" className="border-border-default rounded-md border p-4"><h2 className="text-text-primary font-medium">{t('seqwf.error')}</h2>
        <pre className="text-text-muted mt-2 text-xs whitespace-pre-wrap wrap-anywhere">{reviewDiagnostic(q.error)}</pre>
        <Button className="mt-3" size="sm" variant="outline" onClick={() => void q.refetch()}>{t('workflows.refresh')}</Button></div>;
  }
  if (!q.data) return <p role="status" className="text-text-muted">{t('seqwf.loading')}</p>;
  return <>{q.refreshFailed && <StaleNotice error={q.refreshError} refetch={q.refetch} />}{children()}</>;
}

export function TaskRef({ label, id, base }: { label: MessageKey; id: string; base: string }): JSX.Element {
  const { render } = useTranslation();
  return <span>{render(label, { task: <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(id)}`}>{id}</Link> })}</span>;
}

type V2Query<T> = { isLoading: boolean; isError: boolean; error: Error | null; data: SequentialProjection<T> | undefined;
  refetch: () => Promise<unknown>; refreshFailed?: boolean; refreshError?: Error | null };
/**
 * S7a @2 read states: a rejected late/foreign-org response shows loading (never prior-org data),
 * access refusals and other errors keep the existing ReadState handling, and a finite
 * unavailable@2 body gets its own message. Children receive only an accepted projection.
 */
export function V2ReadState<T>({ q, children }: { q: V2Query<T>; children: (value: T) => ReactNode }): JSX.Element {
  const { t } = useTranslation();
  if (q.isError && isLateOrgResponse(q.error)) return <p role="status" className="text-text-muted">{t('seqwf.v2.lateOrg')}</p>;
  return <ReadState q={q}>{() => isProjectionUnavailable(q.data!) ? <ProjectionUnavailable value={q.data} /> : children(q.data as T)}</ReadState>;
}

/** Every recorded source/carrier/visit task link of a run, in projection order; never collapsed to one. */
export function RunTaskLinks({ tasks, base }: { tasks: SequentialRun['tasks']; base: string }): JSX.Element {
  const { t, render } = useTranslation();
  if (!tasks.length) return <p className="text-text-muted text-xs">{t('seqwf.v2.noLinks')}</p>;
  return <ul aria-label={t('seqwf.v2.links')} className="flex flex-wrap gap-x-4 gap-y-1 text-xs">{tasks.map((link, i) => {
    const task = <Link className="text-id-task font-mono hover:underline" to={`${base}/tasks/${encodeURIComponent(link.task_id)}`}>{link.task_id}</Link>;
    return <li key={`${i}:${link.relationship}:${link.task_id}:${link.visit_id ?? ''}`}>{link.relationship === 'source' ? render('seqwf.v2.rel.source', { task })
      : link.relationship === 'carrier' ? render('seqwf.v2.rel.carrier', { task })
        : link.relationship === 'visit' ? render('seqwf.v2.rel.visit', { task, visit: <span className="font-mono">{link.visit_id ?? '—'}</span> })
          : <>{link.relationship} {task}</>}</li>;
  })}</ul>;
}

/** Provenance tags: agent-reported content versus engine-authenticated native identity/evidence only. */
export function AgentReported(): JSX.Element {
  const { t } = useTranslation();
  return <Pill tone="attention">{t('seqwf.v2.agentReported')}</Pill>;
}
export function EngineAuthenticated(): JSX.Element {
  const { t } = useTranslation();
  return <Pill tone="info">{t('seqwf.v2.engineAuthenticated')}</Pill>;
}

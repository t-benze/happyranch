import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useWorkflowActivations, useWorkflowReviewDetail } from '@/hooks/workflows';
import { WorkflowReviewPanel } from './WorkflowReviewPanel';
import { reviewDiagnostic, reviewErrorKey } from './strings';

export function WorkflowReviewsPage(): JSX.Element {
  const { slug = '', activationId } = useParams();
  const [params] = useSearchParams();
  const history = params.get('view') === 'history';
  const list = useWorkflowActivations();
  const { t } = useTranslation();
  return <div className="flex h-full min-w-0">
    <section aria-label={t('workflows.list')} className={`border-border min-w-0 overflow-y-auto border-r p-4 md:w-72 md:shrink-0 ${activationId ? 'hidden md:block' : 'w-full md:block'}`}>
      <h1 className="text-fg mb-5 text-2xl font-semibold">{t('workflows.title')}</h1>
      {list.isLoading && <p>{t('workflows.loading')}</p>}
      {list.isError && <div role="alert"><p>{t('workflows.errorBody')}</p><pre className="whitespace-pre-wrap wrap-anywhere">{reviewDiagnostic(list.error)}</pre><Button variant="outline" onClick={() => void list.refetch()}>{t('workflows.refresh')}</Button></div>}
      {list.data?.length === 0 && <><p>{t('workflows.empty')}</p><p className="text-fg-muted mt-2 text-sm">{t('workflows.emptyBody')}</p></>}
      {list.data?.map(a => <Link key={a.activation_id} to={`/orgs/${slug}/reviews/${encodeURIComponent(a.activation_id)}`} aria-current={activationId === a.activation_id ? 'page' : undefined}
        className={`border-border bg-bg-raised mb-3 block rounded-lg border p-4 wrap-anywhere ${activationId === a.activation_id ? 'border-l-accent border-l-2' : ''}`}>
        <p className="font-semibold">{a.template.identity_id}</p><p className="text-fg-muted mt-2 text-sm">{t('workflows.version', { version: a.template.version })}</p><p className="text-fg-muted text-xs">{a.instance_id}</p>
      </Link>)}
    </section>
    {!activationId ? <section className="hidden min-w-0 flex-1 p-8 md:block"><h2 className="text-2xl">{t('workflows.choose')}</h2><p className="text-fg-muted mt-3">{t('workflows.chooseBody')}</p></section> :
      <SelectedReview key={JSON.stringify([slug, activationId, history])} slug={slug} activationId={activationId} history={history} />}
  </div>;
}
function SelectedReview({ slug, activationId, history }: { slug: string; activationId: string; history: boolean }): JSX.Element {
  const q = useWorkflowReviewDetail(activationId, history);
  const { t } = useTranslation();
  const detail = q.data;
  return <section className="relative min-w-0 flex-1 overflow-y-auto p-4 md:p-6" aria-label={t('workflows.detail')}>
    <div className="mb-5 flex flex-wrap items-center justify-between gap-2 md:absolute md:top-6 md:right-6 md:mb-0"><Link className="text-accent text-sm md:hidden" to={`/orgs/${slug}/reviews`}>{t('workflows.back')}</Link>
      <Button variant="outline" size="sm" onClick={() => void q.refetch()}>{t('workflows.refresh')}</Button></div>
    {q.isError ? <div role="alert"><h2 className="text-xl">{t('workflows.error')}</h2><p className="mt-3">{t(reviewErrorKey(q.error))}</p><pre className="mt-3 whitespace-pre-wrap wrap-anywhere">{reviewDiagnostic(q.error)}</pre></div> :
      !detail ? <p role="status">{t('workflows.loading')}</p> :
        <WorkflowReviewPanel key={JSON.stringify([slug, activationId, history, detail.request?.request_id, detail.request?.request_scope_digest, detail.request?.scope.revision, detail.selection.successor?.revision_operation_id])}
          detail={detail} fresh={!q.isFetching && !q.isError} refresh={async () => {
            const result = await q.refetch() as { data?: typeof detail; isError?: boolean };
            return result.isError ? undefined : result.data;
          }} />}
  </section>;
}

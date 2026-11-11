/**
 * Active-agent knowledge on the agent detail (THR-296 S5): Learnings (with
 * inherited attribution), the Instill entry and the recipient's knowledge
 * imports (learned sources, versions, counts and receipt links). Owns the one
 * Instill drawer. Instill never cancels or interrupts the recipient's work;
 * imported notes reach its next fresh session only.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArchiveRestore, CircleAlert, CircleHelp } from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import { useAgentLearnings, useAgentsRoutes } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import {
  useInstillAdmission, useInstillAttempts, useKnowledgeArchives, useKnowledgeImports, useRetryImport, useStartInstill,
  type ImportOperation,
} from '@/hooks/knowledge';
import { classifyAgentError, renderAgentError } from '../strings';
import { Notice, useUtc } from '../retired/KnowledgeParts';
import { ImportStateBadge, InheritedMarker, InstillDrawer, type InstillView } from './InstillDrawer';

const card = 'bg-surface border-border-default shadow-pasture-sm overflow-hidden rounded-lg border';

export function AgentKnowledge({ agentName, displayName }: { agentName: string; displayName: string }): JSX.Element {
  const { t, render } = useTranslation();
  const utc = useUtc();
  const navigate = useNavigate();
  const routes = useAgentsRoutes();
  const learningsQuery = useAgentLearnings(agentName);
  const archivesQuery = useKnowledgeArchives();
  const importsQuery = useKnowledgeImports(agentName);
  const { attempts, ledgerReadable } = useInstillAttempts(agentName);
  const { mutation: start, lastSubmissionId } = useStartInstill(agentName);
  const retry = useRetryImport(agentName);
  const admission = useInstillAdmission(agentName);
  const [stack, setStack] = useState<InstillView[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [explicit, setExplicit] = useState<Record<string, string[] | undefined>>({});
  const returnFocus = useRef<(HTMLElement | null)[]>([]);
  // One POST per explicit click: a synchronous latch closes the gap before re-render.
  const sending = useRef(false);

  const learningsError = learningsQuery.isError && learningsQuery.error instanceof ApiError ? learningsQuery.error : null;
  const operations = useMemo(
    () => (importsQuery.status === 'success' ? [...importsQuery.data.operations].reverse() : null),
    [importsQuery.status, importsQuery.data],
  );
  const inflight = operations?.find((op) => op.state === 'Queued' || op.state === 'Processing') ?? null;
  const unresolved = attempts[0] ?? null;
  // After an unknown outcome, the recipient's history may hold the operation
  // for the SAME submission ID: that authoritative read settles it.
  const found = start.isError && lastSubmissionId.current
    ? operations?.find((op) => op.submission_id === lastSubmissionId.current) ?? null : null;
  const sourceNames = useMemo(() => {
    const names = new Map<string, string>();
    (archivesQuery.data?.archives ?? []).forEach((a) => names.set(a.source_id, a.source_name));
    return names;
  }, [archivesQuery.data]);
  const archiveNames = useMemo(() => {
    const names = new Map<string, string>();
    (archivesQuery.data?.archives ?? []).forEach((a) => names.set(a.archive_id, a.source_name));
    return names;
  }, [archivesQuery.data]);

  const push = (view: InstillView) => {
    returnFocus.current[stack.length] = document.activeElement as HTMLElement | null;
    setStack((s) => [...s, view]);
  };
  const open = (view: InstillView) => {
    returnFocus.current = [document.activeElement as HTMLElement | null];
    start.reset();
    retry.reset();
    setStack([view]);
  };
  // Focus returns after the previous view is visible again (post-commit).
  const pendingFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (pendingFocus.current && stack.length > 0) { pendingFocus.current.focus(); pendingFocus.current = null; }
  }, [stack]);
  const pop = () => {
    pendingFocus.current = returnFocus.current[stack.length - 1] ?? null;
    setStack((s) => s.slice(0, -1));
  };
  const close = () => setStack([]);

  const confirm = useCallback((archiveId: string, archiveVersion: number, itemIds: string[]) => {
    // Same gate as the button, re-read at the action boundary.
    if (sending.current || !admission.canConfirm(archiveId)) return;
    sending.current = true;
    start.mutate({ selection: { archive_id: archiveId, archive_version: archiveVersion, item_ids: itemIds } }, {
      onSuccess: (op: ImportOperation) => {
        setExplicit((current) => ({ ...current, [archiveId]: undefined }));
        setStack((s) => (s.length ? [{ kind: 'receipt', operationId: op.operation_id }] : s));
      },
      onSettled: () => { sending.current = false; },
    });
  }, [start, admission]);
  const recover = useCallback((submissionId: string) => {
    if (sending.current) return;
    sending.current = true;
    start.mutate({ submissionId }, {
      onSuccess: (op: ImportOperation) => setStack((s) => (s.length ? [{ kind: 'receipt', operationId: op.operation_id }] : s)),
      onSettled: () => { sending.current = false; },
    });
  }, [start]);
  const doRetry = useCallback((operationId: string) => {
    if (sending.current || !admission.canRetry(operationId)) return;
    sending.current = true;
    retry.mutate(operationId, { onSettled: () => { sending.current = false; } });
  }, [retry, admission]);

  return (
    <>
      {/* Learnings */}
      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-overline text-text-muted tracking-wider uppercase">
            {t('agents.detail.learnings')}
          </h3>
          <Button variant="outline" size="sm" onClick={() => open({ kind: 'pick' })}>
            <ArchiveRestore aria-hidden="true" />{t('knowledge.instill.open')}
          </Button>
        </div>
        {learningsQuery.isLoading ? (
          <p className="text-text-muted text-xs">{t('agents.detail.learningsLoading')}</p>
        ) : learningsError?.status === 412 ? (
          <p className="text-text-muted text-xs">
            {render('agents.detail.learningsNotMigrated', { command: <code>happyranch memory reindex</code> })}
          </p>
        ) : learningsError ? (
          <p className="text-tier-red text-xs">
            {renderAgentError(classifyAgentError(learningsError, 'agents.detail.learningsError'), t)}
          </p>
        ) : learningsQuery.data && learningsQuery.data.entries.length > 0 ? (
          <ul className="space-y-2">
            {learningsQuery.data.entries.map((e) => (
              <li
                key={e.id}
                className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-text-muted font-mono tabular-nums">{e.id}</span>
                  <span className="text-text-muted">·</span>
                  <span className="text-text-muted">{e.topic}</span>
                  {e.provenance === 'inherited' && e.inherited_from ? (
                    <button type="button" onClick={() => open({ kind: 'memory', memoryId: e.id })}
                      aria-label={t('knowledge.instill.inheritedOpen', { id: e.id })}
                      className="focus-visible:ring-ring ml-auto rounded-full focus-visible:ring-2 focus-visible:outline-none">
                      <InheritedMarker sourceLabel={sourceNames.get(e.inherited_from.source_id) ?? e.inherited_from.source_id} />
                    </button>
                  ) : null}
                </div>
                <p className="text-text-primary mt-1 text-sm font-medium">{e.title}</p>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title={t('agents.detail.noLearningsTitle')}
            body={t('agents.detail.noLearningsBody')}
          />
        )}
      </section>

      {/* Knowledge imports: learned sources, versions, counts and receipts */}
      <section aria-labelledby="instill-history-heading">
        <div className={card}>
          <div className="border-border-subtle flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
            <h3 id="instill-history-heading" className="text-text-primary text-sm font-semibold">{t('knowledge.instill.history.heading')}</h3>
            <span className="text-text-secondary text-sm">{t('knowledge.instill.history.subtitle')}</span>
          </div>
          {!ledgerReadable ? (
            <div className="px-4 py-3">
              <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>{t('knowledge.instill.ledger.unavailable')}</Notice>
            </div>
          ) : null}
          {unresolved ? (
            <div className="border-border-subtle flex flex-wrap items-center gap-3 border-b px-4 py-3">
              <CircleHelp size={16} aria-hidden="true" className="text-text-muted shrink-0" />
              <div className="min-w-0 flex-1 text-sm">
                <p className="text-text-primary">{t('knowledge.instill.history.unknown', { date: utc(unresolved.submitted_at), archive: unresolved.archive_id })}</p>
                <p className="text-text-muted mt-0.5 font-mono text-xs break-all">{unresolved.submission_id}</p>
              </div>
              <Button variant="outline" size="sm" disabled={start.isPending} loading={start.isPending}
                onClick={() => { open({ kind: 'confirm', archiveId: unresolved.archive_id }); }}>
                {t('knowledge.instill.recover.open')}
              </Button>
            </div>
          ) : null}
          {importsQuery.isLoading ? (
            <p role="status" className="text-text-muted px-4 py-3 text-sm">{t('knowledge.common.loading')}</p>
          ) : importsQuery.isError ? (
            <div role="alert" className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <span className="text-text-secondary">{t('knowledge.instill.history.loadError')}</span>
              <span className="text-text-muted font-mono text-xs">{importsQuery.error instanceof ApiError ? (importsQuery.error.code ?? `http_${importsQuery.error.status}`) : ''}</span>
              <Button variant="outline" size="sm" onClick={() => void importsQuery.refetch()}>{t('common.retry')}</Button>
            </div>
          ) : !operations || operations.length === 0 ? (
            <p className="text-text-secondary px-4 py-3 text-sm">{t('knowledge.instill.history.empty')}</p>
          ) : (
            <ul>
              {operations.map((op) => (
                <li key={op.operation_id} className="border-border-subtle border-b last:border-b-0">
                  <button type="button" onClick={() => open({ kind: 'receipt', operationId: op.operation_id })}
                    className="hover:bg-surface-hover focus-visible:ring-ring flex w-full items-start gap-3 px-4 py-3 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset">
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2 text-sm">
                        <span className="text-info font-mono text-xs break-all">{op.operation_id}</span>
                        <span className="text-text-primary"><span className="font-mono text-xs">{op.archive_id}</span> · {t('knowledge.common.version', { version: op.archive_version })} · {archiveNames.get(op.archive_id) ?? op.source_id}</span>
                        <span className="text-text-muted text-xs">{utc(op.created_at)}</span>
                      </span>
                      <span className="text-text-secondary mt-1 block text-xs">
                        {t('knowledge.instill.history.counts', { imported: op.imported, skipped: op.skipped, failed: op.failed, pending: op.pending })}
                      </span>
                    </span>
                    <ImportStateBadge state={op.state} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="text-text-muted border-border-subtle border-t px-4 py-2.5 text-xs">{t('knowledge.instill.history.note')}</p>
        </div>
      </section>

      <InstillDrawer
        recipient={{ id: agentName, name: displayName }}
        stack={stack}
        onPush={push}
        onPop={pop}
        onClose={close}
        onClosed={() => returnFocus.current[0]?.focus()}
        onOpenTerminated={() => { close(); navigate(`${routes.inbox()}?view=terminated`); }}
        archives={{
          isLoading: archivesQuery.isLoading,
          isError: archivesQuery.isError,
          error: archivesQuery.error,
          data: archivesQuery.data?.archives,
          refetch: () => void archivesQuery.refetch(),
        }}
        history={operations}
        historyError={importsQuery.error}
        chosen={chosen}
        onChoose={setChosen}
        explicit={explicit}
        onSelect={(archiveId, itemIds) => setExplicit((current) => ({ ...current, [archiveId]: itemIds }))}
        gate={{ ledgerReadable, historyReady: importsQuery.status === 'success' && !importsQuery.isFetching, unresolved, inflight: importsQuery.status === 'success' ? inflight : null }}
        start={{ pending: start.isPending, error: start.error, found, onConfirm: confirm, onRecover: recover }}
        retry={{ pending: retry.isPending, error: retry.error, operationId: retry.variables ?? null, onRetry: doRetry }}
      />
    </>
  );
}

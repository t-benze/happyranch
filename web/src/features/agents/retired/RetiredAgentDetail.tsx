/**
 * Retained, read-only detail of one retired agent (THR-296 S4).
 *
 * Reads the actual retained identity through the S1 retired-source GET (never
 * the active roster), shows coverage, Ready archives and this browser's
 * Distill attempts, and owns the single knowledge drawer. The source stays
 * retired: there is no thread, task, recipient or cancellation action here.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, Lock, Archive, CircleAlert } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { AddressableNameEditor } from '@/shared/identities/AddressableNameEditor';
import { useTranslation } from '@/hooks/i18n';
import { useAgentsRoutes } from '@/hooks/agents';
import { identityInitials } from '@/hooks/identities';
import { ApiError } from '@/lib/api';
import {
  useDistillAttempts, useDistillOperations, useKnowledgeArchives, useRetiredKnowledgeSource, useStartDistill,
  type DistillOperation, type DistillAttempt, type KnowledgeSourceRecord,
} from '@/hooks/knowledge';
import { BlockedReason, CoverageList, Notice, OperationStateBadge, useUtc } from './KnowledgeParts';
import { KnowledgeDrawer, RetiredBadge, type KnowledgeView } from './KnowledgeDrawer';
import { SOURCE_ERROR } from './knowledgeCopy';

const card = 'bg-surface border-border-default shadow-pasture-sm overflow-hidden rounded-lg border';

function recordLabel(record: KnowledgeSourceRecord): string {
  const id = record.metadata.id;
  return typeof id === 'string' ? id : record.relative_path;
}

export function RetiredAgentDetail({ sourceId }: { sourceId: string }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const navigate = useNavigate();
  const routes = useAgentsRoutes();
  const sourceQuery = useRetiredKnowledgeSource(sourceId);
  const archivesQuery = useKnowledgeArchives();
  const { attempts, ledgerReadable } = useDistillAttempts(sourceId);
  const known = attempts.filter((a): a is DistillAttempt & { operation_id: string } => a.operation_id !== null);
  const unknown = attempts.filter((a) => a.operation_id === null);
  const operations = useDistillOperations(known.map((a) => a.operation_id));
  const start = useStartDistill(sourceId);
  const [stack, setStack] = useState<KnowledgeView[]>([]);
  const returnFocus = useRef<(HTMLElement | null)[]>([]);

  const ready = useMemo(
    () => (archivesQuery.data?.archives ?? []).filter((a) => a.source_id === sourceId).sort((a, b) => b.version - a.version),
    [archivesQuery.data, sourceId],
  );
  const opRows = known.map((attempt, index) => ({ attempt, query: operations[index] })).reverse();
  const inflight = opRows.find((row) => row.query?.data && (row.query.data.state === 'Queued' || row.query.data.state === 'Processing'));
  // A new attempt waits until every known attempt has an authoritatively read
  // state (loading, read error and absent data all count as unconfirmed), every
  // unknown-outcome submission was explicitly re-checked with its same key, and
  // this browser's ledger is readable (server refuses a duplicate in-flight
  // attempt regardless).
  const unconfirmed = opRows.some((row) => !row.query?.data) || unknown.length > 0 || !ledgerReadable;
  const canStart = !!sourceQuery.data?.capture_available && !inflight && !unconfirmed;

  const push = (view: KnowledgeView) => {
    returnFocus.current[stack.length] = document.activeElement as HTMLElement | null;
    setStack((s) => [...s, view]);
  };
  const open = (view: KnowledgeView) => {
    returnFocus.current = [document.activeElement as HTMLElement | null];
    start.reset();
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
  const replace = (view: KnowledgeView) => {
    if (view.kind === 'confirm') start.reset();
    setStack((s) => [...s.slice(0, -1), view]);
  };
  const confirm = (submissionId?: string) => {
    // A fresh key is minted only while starting is currently allowed.
    if (!submissionId && !canStart) return;
    start.mutate({ submissionId }, {
      onSuccess: (op: DistillOperation) => setStack((s) => (s.length ? [...s.slice(0, -1), { kind: 'operation', operationId: op.operation_id }] : s)),
    });
  };

  if (sourceQuery.isLoading) {
    return (
      <div role="status" aria-label={t('knowledge.common.loading')} className="animate-pulse space-y-4 p-5">
        <div className="bg-surface-sunken h-14 w-2/3 rounded-lg" />
        <div className="bg-surface-sunken h-12 w-full rounded-lg" />
        <div className="bg-surface-sunken h-48 w-full rounded-lg" />
      </div>
    );
  }
  if (sourceQuery.isError || !sourceQuery.data) {
    const error = sourceQuery.error;
    const code = error instanceof ApiError ? error.code : null;
    return (
      <div className="space-y-3 p-5">
        <BackLink onClick={() => navigate(`${routes.inbox()}?view=terminated`)} />
        <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
          <p>{code && SOURCE_ERROR[code] ? t(SOURCE_ERROR[code]) : t('knowledge.sourceError.generic')}</p>
          <p className="text-text-muted mt-1 font-mono text-xs">{sourceId}{code ? ` · ${code}` : ''}</p>
          {code !== 'unknown_retired_source' ? (
            <Button variant="outline" size="sm" className="mt-2" onClick={() => void sourceQuery.refetch()}>{t('common.retry')}</Button>
          ) : null}
        </Notice>
      </div>
    );
  }
  const source = sourceQuery.data;
  const blocked = !source.capture_available;
  const distillDisabled = blocked || (!inflight && unconfirmed);

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-5 px-6 py-4">
        <BackLink onClick={() => navigate(`${routes.inbox()}?view=terminated`)} />
        <header className="border-border-subtle flex items-start gap-4 border-b pb-4">
          <span aria-hidden="true" className="bg-surface-sunken text-text-secondary flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-base font-semibold">
            {identityInitials(source.name)}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-text-primary text-2xl font-medium break-words">{source.name}</h2>
            <div className="text-text-secondary mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="text-text-muted font-mono text-xs">{source.source_id}</span>
              <RetiredBadge />
              <span>{source.retired_at ? t('knowledge.retired.on', { date: utc(source.retired_at) }) : t('knowledge.retired.dateUnknown')}</span>
              <span>{t('knowledge.retired.teamProvider', { team: source.team, provider: source.provider })}</span>
            </div>
          </div>
        </header>

        <Notice tone="neutral" icon={<Lock size={16} aria-hidden="true" />}>
          <strong className="font-semibold">{t('knowledge.retired.readOnlyLead')}</strong> {t('knowledge.retired.readOnlyBody')}
        </Notice>

        <section aria-labelledby="retired-knowledge-heading" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 id="retired-knowledge-heading" className="text-text-primary text-base font-semibold">{t('knowledge.heading')}</h3>
            {inflight?.query?.data ? (
              <Button variant="outline" onClick={() => open({ kind: 'operation', operationId: inflight.query!.data!.operation_id })}>
                <Archive aria-hidden="true" />{t('knowledge.viewDistillation')}
              </Button>
            ) : (
              <Button variant="outline" disabled={distillDisabled} onClick={() => open({ kind: 'confirm' })}>
                <Archive aria-hidden="true" />{t('knowledge.distill')}
              </Button>
            )}
          </div>
          <p className="text-text-secondary text-sm">
            {t('knowledge.intro')}
            {blocked ? <>{' '}<BlockedReason source={source} /></> : null}
          </p>
          {!ledgerReadable ? (
            <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>{t('knowledge.ledgerUnavailable')}</Notice>
          ) : null}
          {!blocked && !inflight && unconfirmed && ledgerReadable ? (
            <p className="text-text-muted text-xs">{t('knowledge.unconfirmedHint')}</p>
          ) : null}
          <CoverageList coverage={source.coverage} mode="summary" label={t('knowledge.coverage.heading')} />

          <div className={card}>
            <div className="border-border-subtle flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
              <h4 className="text-text-primary text-sm font-semibold">{t('knowledge.archives.heading')}</h4>
              <span className="text-text-secondary text-sm">{t('knowledge.archives.subtitle')}</span>
            </div>
            {archivesQuery.isLoading ? (
              <p role="status" className="text-text-muted px-4 py-3 text-sm">{t('knowledge.common.loading')}</p>
            ) : archivesQuery.isError ? (
              <div role="alert" className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <span className="text-text-secondary">{t('knowledge.archives.loadError')}</span>
                <Button variant="outline" size="sm" onClick={() => void archivesQuery.refetch()}>{t('common.retry')}</Button>
              </div>
            ) : ready.length === 0 ? (
              <p className="text-text-secondary px-4 py-3 text-sm">{t('knowledge.archives.empty')}</p>
            ) : (
              <ul>
                {ready.map((a) => (
                  <li key={a.archive_id} className="border-border-subtle flex items-start gap-3 border-b px-4 py-3 last:border-b-0">
                    <div className="min-w-0 flex-1">
                      <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
                        <span className="text-info font-mono text-xs break-all">{a.archive_id}</span>
                        <span className="text-text-primary">{t('knowledge.archives.captured', { version: a.version, date: utc(a.cutoff) })}</span>
                      </p>
                      <p className="text-text-secondary mt-1 text-xs">
                        {t('knowledge.archive.contents', { items: a.item_count, eligible: a.eligible_count })} ·{' '}
                        <button type="button" className="text-accent-text font-semibold underline-offset-2 hover:underline" onClick={() => open({ kind: 'archive', archiveId: a.archive_id })}>
                          {t('knowledge.archive.view')}
                        </button>
                      </p>
                    </div>
                    <OperationStateBadge state="Ready" />
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className={card}>
            <div className="border-border-subtle flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
              <h4 className="text-text-primary text-sm font-semibold">{t('knowledge.attempts.heading')}</h4>
              <span className="text-text-secondary text-sm">{t('knowledge.attempts.subtitle')}</span>
            </div>
            <AttemptRows rows={opRows} unknown={unknown} utc={utc}
              onOpen={(operationId) => open({ kind: 'operation', operationId })}
              onCheck={(submissionId) => confirm(submissionId)} checking={start.isPending} />
          </div>
        </section>

        <section aria-labelledby="retired-records-heading" className="space-y-3">
          <h3 id="retired-records-heading" className="text-text-primary text-base font-semibold">{t('knowledge.records.heading')}</h3>
          <div className={card}>
            <div className="border-border-subtle flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
              <h4 className="text-text-primary text-sm font-semibold">{t('knowledge.records.subheading')}</h4>
              <span className="text-text-secondary text-sm">{t('knowledge.records.readOnly')}</span>
            </div>
            {source.records.length === 0 ? (
              <p className="text-text-secondary px-4 py-3 text-sm">{t('knowledge.records.empty')}</p>
            ) : (
              <ul>
                {source.records.map((record) => (
                  <li key={record.reference_id} className="border-border-subtle border-b last:border-b-0">
                    <button type="button"
                      onClick={() => open({ kind: 'reference', referenceId: record.reference_id, label: recordLabel(record) })}
                      className="hover:bg-surface-hover focus-visible:ring-ring flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2.5 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset">
                      <span className="text-info font-mono text-xs">{recordLabel(record)}</span>
                      <span className="text-text-muted text-xs">{record.category === 'runtime_memory' ? t('knowledge.reference.runtimeKind') : t('knowledge.reference.claudeKind')}</span>
                      <span className="text-text-primary min-w-0 flex-1 truncate text-sm">
                        {typeof record.metadata.title === 'string' ? record.metadata.title : record.relative_path}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-text-muted border-border-subtle border-t px-4 py-2.5 text-xs">{t('knowledge.records.note')}</p>
          </div>
        </section>

        <section className="border-border-subtle border-t pt-4">
          <AddressableNameEditor canonicalId={source.source_id} />
        </section>
      </div>

      <KnowledgeDrawer
        source={source}
        stack={stack}
        onPush={push}
        onReplace={replace}
        onPop={pop}
        onClose={close}
        onClosed={() => returnFocus.current[0]?.focus()}
        readyArchives={ready}
        canStart={canStart}
        start={{
          pending: start.isPending,
          error: start.error,
          onConfirm: () => confirm(),
          recoverySubmissionId: unknown[0]?.submission_id ?? null,
          onRecover: (submissionId) => confirm(submissionId),
        }}
      />
    </div>
  );
}

function BackLink({ onClick }: { onClick: () => void }): JSX.Element {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onClick} className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1 text-sm">
      <ChevronLeft size={14} aria-hidden="true" />{t('identity.terminated')}
    </button>
  );
}

type OperationQuery = ReturnType<typeof useDistillOperations>[number];

function AttemptRows({ rows, unknown, utc, onOpen, onCheck, checking }: {
  rows: { attempt: DistillAttempt & { operation_id: string }; query: OperationQuery | undefined }[];
  unknown: DistillAttempt[];
  utc: (value: string | null | undefined) => string;
  onOpen: (operationId: string) => void;
  onCheck: (submissionId: string) => void;
  checking: boolean;
}): JSX.Element {
  const { t } = useTranslation();
  const visible = rows.filter((row) => row.query?.data?.state !== 'Ready');
  if (visible.length === 0 && unknown.length === 0) {
    return <p className="text-text-secondary px-4 py-3 text-sm">{t('knowledge.attempts.empty')}</p>;
  }
  return (
    <ul>
      {unknown.map((attempt) => (
        <li key={attempt.submission_id} className="border-border-subtle flex flex-wrap items-center gap-3 border-b px-4 py-3 last:border-b-0">
          <div className="min-w-0 flex-1 text-sm">
            <p className="text-text-primary">{t('knowledge.attempts.unknown', { date: utc(attempt.submitted_at) })}</p>
            <p className="text-text-muted mt-0.5 font-mono text-xs break-all">{attempt.submission_id}</p>
          </div>
          <Button variant="outline" size="sm" disabled={checking} onClick={() => onCheck(attempt.submission_id)}>
            {t('knowledge.attempts.checkAgain')}
          </Button>
        </li>
      ))}
      {visible.map(({ attempt, query }) => {
        const op = query?.data;
        return (
          <li key={attempt.operation_id} className="border-border-subtle flex items-start gap-3 border-b px-4 py-3 last:border-b-0">
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="text-info font-mono text-xs break-all">{attempt.operation_id}</span>
                <span className="text-text-primary">{t('knowledge.attempts.started', { date: utc(op?.created_at ?? attempt.submitted_at) })}</span>
              </p>
              <p className="text-text-secondary mt-1 text-xs">
                {query?.isError
                  ? t('knowledge.attempts.unreadable')
                  : op ? t('knowledge.attempts.lastUpdate', { date: utc(op.updated_at) }) : t('knowledge.common.loading')}
                {' · '}
                <button type="button" className="text-accent-text font-semibold underline-offset-2 hover:underline" onClick={() => onOpen(attempt.operation_id)}>
                  {t('knowledge.attempts.view')}
                </button>
              </p>
            </div>
            {op ? <OperationStateBadge state={op.state} /> : query?.isError ? (
              <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>
                {t('knowledge.attempts.checkAgain')}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

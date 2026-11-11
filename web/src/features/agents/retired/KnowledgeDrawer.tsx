/**
 * One right-hand drawer for a retired source's knowledge (THR-296 S4):
 * Distill confirmation, operation, immutable archive and read-only source
 * record. Views form a stack; earlier views stay mounted (hidden) so going
 * back preserves scroll, expansions and focus. No cancellation control exists:
 * closing the drawer never stops processing.
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { Archive, ChevronLeft, Clock, Lock, X, CircleAlert, Check, Info } from 'lucide-react';
import { Drawer, DrawerContent, DrawerTitle } from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { ApiError } from '@/lib/api';
import {
  DistillLedgerError, useDistillOperation, useKnowledgeArchive, useRetiredKnowledgeReference,
  type RetiredKnowledgeSource, type KnowledgeArchiveSelection, type CuratedKnowledgeItem, type KnowledgeArchive,
} from '@/hooks/knowledge';
import { BlockedReason, CoverageList, Fact, Facts, Notice, OperationStateBadge, Pill, SectionLabel, useUtc } from './KnowledgeParts';
import { DISPOSITION_LABEL, EXCLUSION_LABEL, ITEM_CATEGORY, ITEM_FLAG, PHASE_LABEL, SUBMIT_ERROR, failureKey } from './knowledgeCopy';

export type KnowledgeView =
  | { kind: 'confirm' }
  | { kind: 'operation'; operationId: string }
  | { kind: 'archive'; archiveId: string }
  | { kind: 'reference'; referenceId: string; label: string };

interface DrawerProps {
  source: RetiredKnowledgeSource;
  stack: KnowledgeView[];
  onPush: (view: KnowledgeView) => void;
  onReplace: (view: KnowledgeView) => void;
  onPop: () => void;
  onClose: () => void;
  /** Called by the drawer when it has closed; restores focus to the opener. */
  onClosed: () => void;
  readyArchives: KnowledgeArchiveSelection[];
  /** False while another attempt is in flight or unconfirmed, the ledger is unreadable, or the source is blocked. */
  canStart: boolean;
  /**
   * `recoverySubmissionId`: an unknown-outcome submission recorded by this
   * browser. While set, the only start action is `onRecover` with that SAME ID.
   */
  start: { pending: boolean; error: unknown; onConfirm: () => void; recoverySubmissionId: string | null; onRecover: (submissionId: string) => void };
}

export function KnowledgeDrawer(props: DrawerProps): JSX.Element {
  const { stack, onPop, onClose, onClosed } = props;
  return (
    <Drawer open={stack.length > 0} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DrawerContent
        className="max-w-full"
        aria-describedby={undefined}
        onCloseAutoFocus={(event) => { event.preventDefault(); onClosed(); }}
        onEscapeKeyDown={(event) => {
          // Escape steps back through the stack before closing the drawer.
          if (stack.length > 1) { event.preventDefault(); onPop(); }
        }}
      >
        {stack.map((view, index) => (
          <ViewFrame key={`${index}:${view.kind}:${'operationId' in view ? view.operationId : 'archiveId' in view ? view.archiveId : 'referenceId' in view ? view.referenceId : ''}`}
            top={index === stack.length - 1}>
            <ViewBody {...props} view={view} depth={index} top={index === stack.length - 1} />
          </ViewFrame>
        ))}
      </DrawerContent>
    </Drawer>
  );
}

function ViewFrame({ top, children }: { top: boolean; children: ReactNode }): JSX.Element {
  return <div hidden={!top} className={top ? 'flex min-h-0 flex-1 flex-col' : undefined}>{children}</div>;
}

interface ShellProps {
  sourceName: string;
  title: string;
  top: boolean;
  breadcrumb?: ReactNode;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
}

export function Shell({ sourceName, title, top, breadcrumb, onClose, footer, children }: ShellProps): JSX.Element {
  const { t } = useTranslation();
  const heading = useRef<HTMLHeadingElement>(null);
  const opened = useRef(false);
  // Focus the heading only when this view first opens; returning to an
  // earlier view restores focus to the control that left it instead.
  useEffect(() => {
    if (top && !opened.current) heading.current?.focus();
    if (top) opened.current = true;
  }, [top]);
  const Title = top ? DrawerTitle : 'h2';
  return (
    <>
      <header className="border-border-subtle flex items-start gap-3 border-b px-5 py-4">
        <div className="min-w-0 flex-1">
          {breadcrumb ?? <div className="text-text-muted truncate text-xs">{sourceName}</div>}
          <Title ref={heading} tabIndex={-1} className="text-text-primary mt-0.5 text-lg font-semibold focus:outline-none">
            {title}
          </Title>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('knowledge.drawer.closeLabel', { title })}>
          <X aria-hidden="true" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      <footer className="border-border-subtle bg-surface-raised flex flex-col gap-3 border-t px-5 py-3 md:flex-row md:items-center md:justify-between">
        {footer}
      </footer>
    </>
  );
}

function ViewBody(props: DrawerProps & { view: KnowledgeView; depth: number; top: boolean }): JSX.Element {
  const { view } = props;
  if (view.kind === 'confirm') return <ConfirmView {...props} />;
  if (view.kind === 'operation') return <OperationView {...props} operationId={view.operationId} />;
  if (view.kind === 'archive') return <ArchiveView {...props} archiveId={view.archiveId} />;
  return <ReferenceView {...props} referenceId={view.referenceId} label={view.label} />;
}

/** The display fields a source line needs (a retired source or an archive's source). */
export type KnowledgeSourceLabel = Pick<RetiredKnowledgeSource, 'name' | 'source_id'>;

function sourceLine(source: KnowledgeSourceLabel, t: ReturnType<typeof useTranslation>['t']): ReactNode {
  return (
    <>
      {source.name} <span className="text-text-muted font-mono text-xs">({source.source_id})</span> · {t('knowledge.retired.badge')}
    </>
  );
}

export function submitErrorKey(error: unknown): string {
  if (error instanceof DistillLedgerError) return error.code;
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return error.code ?? `http_${error.status}`;
  return 'unknown';
}

function ConfirmView({ source, start, canStart, onClose, top }: DrawerProps & { top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const blocked = !source.capture_available;
  const code = start.error ? submitErrorKey(start.error) : null;
  const recovery = start.recoverySubmissionId;
  // While an outcome is unknown the only action re-sends that SAME submission.
  const recovering = !!recovery && !start.pending;
  const operationId = start.error instanceof DistillLedgerError ? start.error.operationId : null;
  // A refused recovery names the daemon's own code, not the local wrapper code.
  const shownCode = start.error instanceof DistillLedgerError && start.error.refusal ? start.error.refusal : code;
  let message: ReactNode;
  if (code) {
    message = (
      <p role="alert" className="text-status-escalated">
        {SUBMIT_ERROR[code] ? t(SUBMIT_ERROR[code], { code: shownCode ?? code, operation: operationId ?? '—' })
          : code === 'unknown' ? t('knowledge.submitError.unknown')
            : t('knowledge.submitError.refused', { code })}
      </p>
    );
  } else if (recovering) {
    message = <p role="status" className="text-status-escalated">{t('knowledge.confirm.recoverPending')}</p>;
  } else if (!canStart && !blocked && !start.pending) {
    message = <p role="status" className="text-status-escalated">{t('knowledge.confirm.unresolved')}</p>;
  } else {
    message = <p>{t('knowledge.confirm.question', { name: source.name, id: source.source_id })}</p>;
  }
  return (
    <Shell
      sourceName={source.name}
      title={t('knowledge.confirm.title')}
      top={top}
      onClose={onClose}
      footer={(
        <>
          <div className="text-text-secondary min-w-0 text-xs leading-relaxed md:max-w-md">
            {message}
          </div>
          <div className="flex shrink-0 items-center gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onClose}>{t('knowledge.confirm.notNow')}</Button>
            {recovering ? (
              <Button onClick={() => start.onRecover(recovery)}>{t('knowledge.attempts.checkAgain')}</Button>
            ) : (
              <Button onClick={start.onConfirm} disabled={!canStart || blocked || start.pending} loading={start.pending}>
                {t('knowledge.confirm.start')}
              </Button>
            )}
          </div>
        </>
      )}
    >
      <p className="text-text-secondary text-sm leading-relaxed">{t('knowledge.confirm.intro')}</p>
      <div className="mt-4">
        <Facts>
              <Fact term={t('knowledge.facts.source')}>{<span className="font-medium">{source.name}</span>}</Fact>
              <Fact term={t('knowledge.facts.permanentId')}>{<span className="font-mono text-xs">{source.source_id}</span>}</Fact>
              <Fact term={t('knowledge.facts.status')}>{<span className="inline-flex flex-wrap items-center gap-2"><RetiredBadge /> {source.retired_at ?? t('knowledge.retired.dateUnknown')}</span>}</Fact>
              <Fact term={t('knowledge.facts.recipient')}>{t('knowledge.confirm.noRecipient')}</Fact>
            </Facts>
      </div>
      {blocked ? (
        <div className="mt-4">
          <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
            <BlockedReason source={source} />
          </Notice>
        </div>
      ) : null}
      <SectionLabel>{t('knowledge.coverage.heading')}</SectionLabel>
      <CoverageList coverage={source.coverage} mode="full" label={t('knowledge.coverage.heading')} />
      <SectionLabel>{t('knowledge.confirm.keepsHeading')}</SectionLabel>
      <ul className="text-text-secondary list-disc space-y-1 pl-5 text-sm">
        <li>{t('knowledge.confirm.keeps1')}</li>
        <li>{t('knowledge.confirm.keeps2')}</li>
        <li>{t('knowledge.confirm.keeps3')}</li>
        <li>{t('knowledge.confirm.keeps4')}</li>
      </ul>
      <div className="mt-4">
        <Notice tone="neutral" icon={<Info size={16} aria-hidden="true" />}>{t('knowledge.lossy')}</Notice>
      </div>
    </Shell>
  );
}

export function RetiredBadge(): JSX.Element {
  const { t } = useTranslation();
  return (
    <span className="border-border-default text-text-primary inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold">
      <Archive size={12} aria-hidden="true" />
      {t('knowledge.retired.badge')}
    </span>
  );
}

export function LoadingBlock(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div role="status" aria-label={t('knowledge.common.loading')} className="animate-pulse space-y-3">
      <div className="bg-surface-sunken h-6 w-1/3 rounded" />
      <div className="bg-surface-sunken h-20 w-full rounded-lg" />
      <div className="bg-surface-sunken h-40 w-full rounded-lg" />
    </div>
  );
}

export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry: () => void }): JSX.Element {
  const { t } = useTranslation();
  const code = error instanceof ApiError ? (error.code ?? `http_${error.status}`) : null;
  return (
    <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
      <p>{t('knowledge.common.loadError')}</p>
      {code ? <p className="text-text-muted mt-1 font-mono text-xs">{code}</p> : null}
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>{t('common.retry')}</Button>
    </Notice>
  );
}

function OperationView({ source, operationId, onClose, onPush, onReplace, readyArchives, canStart, top }: DrawerProps & { operationId: string; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const query = useDistillOperation(operationId);
  const op = query.data;
  const archive = useKnowledgeArchive(op?.state === 'Ready' ? op.archive_id : null);
  const earlier = readyArchives.filter((a) => a.operation_id !== operationId).sort((a, b) => b.version - a.version)[0];
  const failureCode = op?.failure ? String(op.failure.code ?? '') : '';
  let footer: ReactNode = (
    <div className="flex w-full justify-end"><Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button></div>
  );
  if (op) {
    const summary = <div className="text-text-muted min-w-0 truncate text-xs">{source.name} · <span className="font-mono">{op.operation_id}</span></div>;
    if (op.state === 'Queued' || op.state === 'Processing') {
      footer = <>{summary}<Button variant="secondary" onClick={onClose}>{t('knowledge.operation.leave')}</Button></>;
    } else if (op.state === 'Ready') {
      footer = (
        <>{summary}
          <div className="flex shrink-0 gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>
            {op.archive_id ? <Button onClick={() => onPush({ kind: 'archive', archiveId: op.archive_id! })}>{t('knowledge.archive.view')}</Button> : null}
          </div>
        </>
      );
    } else {
      footer = (
        <>{summary}
          <div className="flex shrink-0 flex-wrap gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>
            {earlier ? <Button variant="outline" onClick={() => onPush({ kind: 'archive', archiveId: earlier.archive_id })}>{t('knowledge.operation.viewEarlier')}</Button> : null}
            {canStart ? <Button onClick={() => onReplace({ kind: 'confirm' })}>{t('knowledge.operation.tryAgain')}</Button> : null}
          </div>
        </>
      );
    }
  }
  return (
    <Shell sourceName={source.name} title={t('knowledge.operation.title')} top={top} onClose={onClose} footer={footer}>
      {query.isLoading ? <LoadingBlock /> : query.isError ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : op ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <OperationStateBadge state={op.state} />
            <span className="text-text-secondary text-sm">{t('knowledge.operation.label')} <span className="font-mono">{op.operation_id}</span></span>
          </div>
          <div className="mt-4">
            {op.state === 'Queued' || op.state === 'Processing' ? (
              <Notice tone="neutral" role="status" icon={<Clock size={16} aria-hidden="true" />}>{t('knowledge.operation.inflight')}</Notice>
            ) : op.state === 'Ready' ? (
              <Notice tone="positive" role="status" icon={<Check size={16} aria-hidden="true" />}>
                {archive.data
                  ? t('knowledge.operation.ready', { count: archive.data.items.length })
                  : t('knowledge.operation.readyNoCount')}
              </Notice>
            ) : (
              <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
                <p className="font-medium">{t('knowledge.operation.failed')}</p>
                {earlier ? <p className="mt-1">{t('knowledge.operation.earlierStillReady')}</p> : null}
                <p className="mt-1">{t(failureKey(failureCode))}</p>
                <p className="text-text-muted mt-1 font-mono text-xs break-all">{failureCode || '—'}</p>
              </Notice>
            )}
          </div>
          <div className="mt-4">
            <Facts>
              <Fact term={t('knowledge.facts.source')}>{sourceLine(source, t)}</Fact>
              <Fact term={t('knowledge.facts.operation')}>{<span className="font-mono text-xs">{op.operation_id}</span>}</Fact>
              <Fact term={t('knowledge.facts.started')}>{utc(op.created_at)}</Fact>
              <Fact term={t('knowledge.facts.lastUpdate')}>{utc(op.updated_at)}</Fact>
              <Fact term={t('knowledge.facts.phase')}>{<span>
                  {PHASE_LABEL[op.phase] ? t(PHASE_LABEL[op.phase]) : op.phase}{' '}
                  <span className="text-text-muted font-mono text-xs">({op.phase})</span>
                </span>}</Fact>
              <Fact term={t('knowledge.facts.cutoff')}>{utc(op.cutoff)}</Fact>
              <Fact term={t('knowledge.facts.processor')}>{op.processor_id ?? op.requested_processor_id ?? t('knowledge.operation.processorPending')}</Fact>
              <Fact term={t('knowledge.facts.task')}>{<span className="font-mono text-xs">{op.task_id ?? '—'}</span>}</Fact>
              <Fact term={t('knowledge.facts.session')}>{<span className="font-mono text-xs break-all">{op.session_id ?? '—'}</span>}</Fact>
              <Fact term={t('knowledge.facts.result')}>{<span className="font-mono text-xs">{op.result_id ?? '—'}</span>}</Fact>
              {op.archive_id ? <Fact term={t('knowledge.facts.archive')}>{<button type="button" className="text-accent-text font-mono text-xs underline-offset-2 hover:underline" onClick={() => onPush({ kind: 'archive', archiveId: op.archive_id! })}>
                  {op.archive_id} · {t('knowledge.common.version', { version: op.archive_version ?? '—' })}
                </button>}</Fact> : null}
            </Facts>
          </div>
          <SectionLabel>{t('knowledge.coverage.heading')}</SectionLabel>
          <CoverageList coverage={op.coverage} mode="summary" label={t('knowledge.coverage.heading')} />
        </>
      ) : null}
    </Shell>
  );
}

export function referenceLabel(archive: KnowledgeArchive, referenceId: string): string {
  const entry = archive.manifest.inventory.find((r) => r.reference_id === referenceId);
  const id = entry?.metadata && typeof entry.metadata.id === 'string' ? entry.metadata.id : null;
  return id ?? entry?.relative_path ?? `${referenceId.slice(0, 18)}…`;
}

/** Item text, source references, attribution, warnings and open-work facts (shared with S5 preview). */
export function ItemBody({ item, archive, source, onOpen }: { item: CuratedKnowledgeItem; archive: KnowledgeArchive; source: KnowledgeSourceLabel; onOpen: (referenceId: string, label: string) => void }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const references = Array.from(new Set([...item.references, ...item.attribution.map((a) => a.reference_id)]));
  return (
    <>
      <h4 className="font-display text-text-primary mt-2 text-base font-medium">{item.title}</h4>
      {item.content
        ? <p className="text-text-primary mt-1 text-sm leading-relaxed whitespace-pre-wrap">{item.content}</p>
        : <p className="text-text-muted mt-1 text-sm">{t('knowledge.item.noContent')}</p>}
      <div className="text-text-secondary mt-3 space-y-1 text-xs">
        <p>{t('knowledge.item.sourceAgent', { name: source.name, id: source.source_id })}</p>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {references.map((ref) => {
            const label = referenceLabel(archive, ref);
            return (
              <button key={ref} type="button" onClick={() => onOpen(ref, label)}
                aria-label={t('knowledge.item.viewRecord', { record: label })}
                className="text-accent-text focus-visible:ring-ring rounded-sm font-mono font-semibold underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:outline-none">
                {label}
              </button>
            );
          })}
        </p>
        {item.attribution.map((a) => (
          <p key={a.reference_id}>
            {t('knowledge.item.attribution', {
              author: a.original_author ?? t('knowledge.common.unknown'),
              authored: a.authored_at ? utc(a.authored_at) : t('knowledge.common.unknownDate'),
              observed: a.observed_at ? utc(a.observed_at) : t('knowledge.common.unknownDate'),
            })}
            {a.founder_ruling ? ` · ${t('knowledge.item.founderRuling', { ruling: a.founder_ruling })}` : ''}
          </p>
        ))}
      </div>
      {item.warnings.length > 0 || item.prerequisites || item.limits ? (
        <div className="bg-attention-soft text-text-primary mt-3 space-y-1 rounded-md px-3 py-2 text-xs leading-relaxed">
          {item.warnings.map((w) => <p key={w}>{w}</p>)}
          {item.prerequisites ? <p>{t('knowledge.item.prerequisites', { text: item.prerequisites })}</p> : null}
          {item.limits ? <p>{t('knowledge.item.limits', { text: item.limits })}</p> : null}
        </div>
      ) : null}
      {item.open_work ? (
        <div className="border-border-subtle text-text-secondary mt-3 rounded-md border px-3 py-2 text-xs leading-relaxed">
          <p className="text-text-primary font-medium">{t('knowledge.item.openWorkNote')}</p>
          <p>{t('knowledge.item.openWorkFacts', {
            owner: item.open_work.owner ?? t('knowledge.common.unknown'),
            status: item.open_work.status ?? t('knowledge.common.unknown'),
            asOf: item.open_work.as_of ?? t('knowledge.common.unknownDate'),
          })}</p>
          {item.open_work.next_action ? <p>{t('knowledge.item.nextAction', { text: item.open_work.next_action })}</p> : null}
          {item.open_work.holds ? <p>{t('knowledge.item.holds', { text: item.open_work.holds })}</p> : null}
        </div>
      ) : null}
    </>
  );
}

function ItemCard({ item, archive, source, onOpen }: { item: CuratedKnowledgeItem; archive: KnowledgeArchive; source: RetiredKnowledgeSource; onOpen: (referenceId: string, label: string) => void }): JSX.Element {
  const { t } = useTranslation();
  return (
    <li className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-text-primary font-mono text-xs font-semibold">{item.item_id}</span>
        <span className="text-text-secondary text-xs">{ITEM_CATEGORY[item.category] ? t(ITEM_CATEGORY[item.category]) : item.category}</span>
        {item.instill_eligible
          ? <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.item.eligible')}</Pill>
          : <Pill tone="neutral">{t('knowledge.item.referenceOnly')}</Pill>}
        {item.flags.map((flag) => (
          <Pill key={flag} tone={flag === 'uncertain' || flag === 'conflict' || flag === 'attribution_unverified' ? 'attention' : 'neutral'}>
            {ITEM_FLAG[flag] ? t(ITEM_FLAG[flag]) : flag}
          </Pill>
        ))}
      </div>
      <ItemBody item={item} archive={archive} source={source} onOpen={onOpen} />
    </li>
  );
}

function ArchiveView({ source, archiveId, onClose, onPush, top }: DrawerProps & { archiveId: string; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const query = useKnowledgeArchive(archiveId);
  const archive = query.data;
  const excluded = archive?.manifest.dispositions.filter((d) => d.disposition === 'excluded') ?? [];
  return (
    <Shell
      sourceName={source.name}
      title={t('knowledge.archive.title', { id: archiveId })}
      top={top}
      onClose={onClose}
      footer={(
        <>
          <div className="text-text-muted min-w-0 truncate text-xs"><span className="font-mono">{archiveId}</span> · {source.name}</div>
          <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>
        </>
      )}
    >
      {query.isLoading ? <LoadingBlock /> : query.isError ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : archive ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <OperationStateBadge state="Ready" />
            <span className="text-text-secondary text-sm">{t('knowledge.archive.immutable')}</span>
          </div>
          <div className="mt-4">
            <Facts>
              <Fact term={t('knowledge.facts.archiveId')}>{<span className="font-mono text-xs break-all">{archive.archive_id}</span>}</Fact>
              <Fact term={t('knowledge.facts.version')}>{t('knowledge.common.version', { version: archive.version })}</Fact>
              <Fact term={t('knowledge.facts.source')}>{sourceLine(source, t)}</Fact>
              <Fact term={t('knowledge.facts.captured')}>{utc(archive.manifest.cutoff)}</Fact>
              <Fact term={t('knowledge.facts.processed')}>{utc(archive.manifest.processed_at)}</Fact>
              <Fact term={t('knowledge.facts.contents')}>{t('knowledge.archive.contents', { items: archive.items.length, eligible: archive.manifest.eligible_count })}</Fact>
              <Fact term={t('knowledge.facts.createdBy')}>{<button type="button" className="text-accent-text font-mono text-xs underline-offset-2 hover:underline" onClick={() => onPush({ kind: 'operation', operationId: archive.operation_id })}>
                  {archive.operation_id}
                </button>}</Fact>
              <Fact term={t('knowledge.facts.processor')}>{<span>{archive.manifest.processor_id} · <span className="font-mono text-xs">{archive.manifest.task_id}</span></span>}</Fact>
            </Facts>
          </div>
          <SectionLabel>{t('knowledge.coverage.heading')}</SectionLabel>
          <CoverageList coverage={archive.manifest.coverage} mode="summary" label={t('knowledge.coverage.heading')} />
          <div className="mt-4">
            <Notice tone="neutral" icon={<Info size={16} aria-hidden="true" />}>{t('knowledge.archive.lossy')}</Notice>
          </div>
          <SectionLabel>{t('knowledge.archive.items')}</SectionLabel>
          {archive.items.length === 0 ? (
            <p className="text-text-secondary text-sm">
              {archive.manifest.explicitly_empty ? t('knowledge.archive.emptyVerified') : t('knowledge.archive.emptyExcluded')}
            </p>
          ) : (
            <ul className="space-y-3">
              {archive.items.map((item) => (
                <ItemCard key={item.item_id} item={item} archive={archive} source={source}
                  onOpen={(referenceId, label) => onPush({ kind: 'reference', referenceId, label })} />
              ))}
            </ul>
          )}
          <SectionLabel>{t('knowledge.archive.accounting')}</SectionLabel>
          <p className="text-text-secondary text-sm">
            {Object.entries(archive.manifest.disposition_counts).map(([key, count]) => (
              `${DISPOSITION_LABEL[key] ? t(DISPOSITION_LABEL[key]) : key}: ${count}`
            )).join(' · ') || '—'}
          </p>
          {excluded.length > 0 ? (
            <details className="bg-surface border-border-default mt-2 rounded-lg border">
              <summary className="text-accent-text cursor-pointer px-4 py-2.5 text-xs font-semibold">
                {t('knowledge.archive.leftOut', { count: excluded.length })}
              </summary>
              <ul className="border-border-subtle border-t">
                {excluded.map((d) => (
                  <li key={d.reference_id} className="border-border-subtle flex flex-col gap-1 border-b px-4 py-2 text-xs last:border-b-0">
                    <span className="flex flex-wrap items-center gap-2">
                      <Pill tone="neutral">{d.exclusion_category && EXCLUSION_LABEL[d.exclusion_category] ? t(EXCLUSION_LABEL[d.exclusion_category]) : (d.exclusion_category ?? '—')}</Pill>
                      <span className="text-text-muted font-mono">{referenceLabel(archive, d.reference_id)}</span>
                    </span>
                    {d.reason !== d.exclusion_category ? <span className="text-text-secondary">{d.reason}</span> : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}
    </Shell>
  );
}

function ReferenceView({ source, referenceId, label, stack, depth, onPop, onClose, top }: DrawerProps & { referenceId: string; label: string; depth: number; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const previous = depth > 0 ? stack[depth - 1] : null;
  const backLabel = previous?.kind === 'archive' ? t('knowledge.archive.title', { id: previous.archiveId })
    : previous?.kind === 'operation' ? t('knowledge.operation.title') : null;
  return (
    <Shell
      sourceName={source.name}
      title={t('knowledge.reference.title', { record: label })}
      top={top}
      onClose={onClose}
      breadcrumb={backLabel ? (
        <div className="text-text-muted flex min-w-0 items-center gap-1 text-xs">
          <button type="button" onClick={onPop} className="text-accent-text truncate underline-offset-2 hover:underline">{backLabel}</button>
          <span aria-hidden="true">›</span>
          <span className="truncate font-mono">{label}</span>
        </div>
      ) : undefined}
      footer={(
        <>
          <div className="text-text-muted min-w-0 truncate text-xs"><span className="font-mono">{label}</span> · {source.name}</div>
          {backLabel ? (
            <Button variant="secondary" onClick={onPop}>
              <ChevronLeft aria-hidden="true" />
              {previous?.kind === 'archive' ? t('knowledge.reference.backArchive') : t('knowledge.reference.backOperation')}
            </Button>
          ) : <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>}
        </>
      )}
    >
      <ReferenceRecord source={source} referenceId={referenceId} label={label} />
    </Shell>
  );
}

/** Read-only source record (or its explicit unavailable state); shared with the S5 Instill drawer. */
export function ReferenceRecord({ source, referenceId, label }: { source: KnowledgeSourceLabel; referenceId: string; label: string }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const query = useRetiredKnowledgeReference(source.source_id, referenceId);
  const record = query.data?.record;
  const meta = record?.metadata ?? {};
  const metaText = (key: string) => (typeof meta[key] === 'string' ? (meta[key] as string) : null);
  return (
    <>
      {query.isLoading ? <LoadingBlock /> : query.isError ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : query.data ? (
        query.data.available && record ? (
          <>
            <blockquote className="border-accent bg-surface text-text-primary rounded-r-lg border-l-4 px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">
              {record.body}
            </blockquote>
            <div className="mt-4">
              <Facts>
              <Fact term={t('knowledge.facts.recordId')}>{<span className="font-mono text-xs">{metaText('id') ?? label}</span>}</Fact>
              <Fact term={t('knowledge.facts.kind')}>{record.category === 'runtime_memory' ? t('knowledge.reference.runtimeKind') : t('knowledge.reference.claudeKind')}</Fact>
              <Fact term={t('knowledge.facts.file')}>{<span className="font-mono text-xs break-all">{record.relative_path}</span>}</Fact>
              {metaText('title') ? <Fact term={t('knowledge.facts.recordTitle')}>{metaText('title')}</Fact> : null}
              <Fact term={t('knowledge.facts.sourceAgent')}>{sourceLine(source, t)}</Fact>
              <Fact term={t('knowledge.facts.authored')}>{utc(metaText('authored_at'))}</Fact>
              {metaText('lifecycle') ? <Fact term={t('knowledge.facts.lifecycle')}>{metaText('lifecycle')}</Fact> : null}
            </Facts>
            </div>
            <p className="text-text-muted mt-4 flex items-center gap-1.5 text-xs">
              <Lock size={12} aria-hidden="true" />{t('knowledge.reference.readOnly')}
            </p>
          </>
        ) : (
          <>
            <Notice tone="neutral" role="status" icon={<CircleAlert size={16} aria-hidden="true" />}>
              <p className="font-medium">{t('knowledge.reference.unavailable')}</p>
              <p className="mt-1">{t('knowledge.reference.unavailableBody')}</p>
              <p className="text-text-muted mt-1 font-mono text-xs">{query.data.reason ?? '—'}</p>
            </Notice>
            <div className="mt-4">
              <Facts>
              <Fact term={t('knowledge.facts.recordId')}>{<span className="font-mono text-xs break-all">{referenceId}</span>}</Fact>
              <Fact term={t('knowledge.facts.sourceAgent')}>{sourceLine(source, t)}</Fact>
            </Facts>
            </div>
          </>
        )
      ) : null}
    </>
  );
}

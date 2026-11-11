/**
 * One right-hand drawer for instilling a Ready archive into an ACTIVE agent
 * (THR-296 S5): archive picker, preview/selection, confirmation, import
 * receipt, read-only source record and recipient memory note. Views form a
 * stack inside the single drawer; earlier views stay mounted (hidden) so going
 * back preserves scroll, expansions, focus and the selection. Previewing,
 * navigating and switching locale send nothing; only the explicit Instill and
 * Retry buttons POST, once per click.
 */
import { useMemo, type ReactNode } from 'react';
import { ArchiveX, ChevronDown, ChevronLeft, CircleAlert, CircleHelp, Check, Circle, CircleDot, CircleX, Clock, Info, Lock, Archive } from 'lucide-react';
import { Drawer, DrawerContent } from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { ApiError } from '@/lib/api';
import {
  InstillLedgerError, blockersOf, useAgentMemoryEntry, useImportEligibility, useKnowledgeArchive, useKnowledgeImport,
  type CuratedKnowledgeItem, type ImportEligibility, type ImportOperation, type InstillAttempt, type KnowledgeArchiveSelection,
  type SourceCoverage,
} from '@/hooks/knowledge';
import { CoverageList, Fact, Facts, Notice, Pill, useUtc } from '../retired/KnowledgeParts';
import { ErrorBlock, ItemBody, LoadingBlock, ReferenceRecord, Shell } from '../retired/KnowledgeDrawer';
import { COVERAGE_LABEL, ITEM_CATEGORY, ITEM_FLAG } from '../retired/knowledgeCopy';
import { BLOCKER, ITEM_REASON, LEDGER, PHASE_KEY, STATE_KEY, refusalKey } from './instillCopy';

export type InstillView =
  | { kind: 'pick' }
  | { kind: 'preview'; archiveId: string }
  | { kind: 'confirm'; archiveId: string }
  | { kind: 'receipt'; operationId: string }
  | { kind: 'reference'; archiveId: string; referenceId: string; label: string }
  | { kind: 'memory'; memoryId: string };

type Translate = ReturnType<typeof useTranslation>['t'];
type EligibilityItem = ImportEligibility['items'][number];

export interface InstillDrawerProps {
  recipient: { id: string; name: string };
  stack: InstillView[];
  onPush: (view: InstillView) => void;
  onPop: () => void;
  onClose: () => void;
  /** Called by the drawer when it has closed; restores focus to the opener. */
  onClosed: () => void;
  onOpenTerminated: () => void;
  archives: { isLoading: boolean; isError: boolean; error: unknown; data: KnowledgeArchiveSelection[] | undefined; refetch: () => void };
  /** Recipient history; null while not authoritatively read. */
  history: ImportOperation[] | null;
  historyError: unknown;
  chosen: string | null;
  onChoose: (archiveId: string) => void;
  /** Explicit selection per archive; undefined means the server's default. */
  explicit: Record<string, string[] | undefined>;
  onSelect: (archiveId: string, itemIds: string[]) => void;
  gate: { ledgerReadable: boolean; historyReady: boolean; unresolved: InstillAttempt | null; inflight: ImportOperation | null };
  start: {
    pending: boolean;
    error: unknown;
    /** The operation recorded for the last send's submission ID, located in the recipient's history. */
    found: ImportOperation | null;
    onConfirm: (archiveId: string, archiveVersion: number, itemIds: string[]) => void;
    onRecover: (submissionId: string) => void;
  };
  retry: { pending: boolean; error: unknown; operationId: string | null; onRetry: (operationId: string) => void };
}

const viewKey = (view: InstillView): string => {
  if (view.kind === 'receipt') return view.operationId;
  if (view.kind === 'memory') return view.memoryId;
  if (view.kind === 'reference') return `${view.archiveId}:${view.referenceId}`;
  if (view.kind === 'pick') return '';
  return view.archiveId;
};

export function InstillDrawer(props: InstillDrawerProps): JSX.Element {
  const { stack, onPop, onClose, onClosed } = props;
  return (
    <Drawer open={stack.length > 0} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DrawerContent
        className="max-w-full"
        aria-describedby={undefined}
        onCloseAutoFocus={(event) => { event.preventDefault(); onClosed(); }}
        onEscapeKeyDown={(event) => {
          // Escape steps back through the steps before closing the drawer.
          if (stack.length > 1) { event.preventDefault(); onPop(); }
        }}
      >
        {stack.map((view, index) => {
          const top = index === stack.length - 1;
          return (
            <div key={`${index}:${view.kind}:${viewKey(view)}`} hidden={!top} className={top ? 'flex min-h-0 flex-1 flex-col' : undefined}>
              <ViewBody {...props} view={view} depth={index} top={top} />
            </div>
          );
        })}
      </DrawerContent>
    </Drawer>
  );
}

function ViewBody(props: InstillDrawerProps & { view: InstillView; depth: number; top: boolean }): JSX.Element {
  const { view } = props;
  if (view.kind === 'pick') return <PickView {...props} />;
  if (view.kind === 'preview') return <PreviewView {...props} archiveId={view.archiveId} />;
  if (view.kind === 'confirm') return <ConfirmView {...props} archiveId={view.archiveId} />;
  if (view.kind === 'receipt') return <ReceiptView {...props} operationId={view.operationId} />;
  if (view.kind === 'reference') return <ReferenceView {...props} archiveId={view.archiveId} referenceId={view.referenceId} label={view.label} />;
  return <MemoryView {...props} memoryId={view.memoryId} />;
}

// ---------------------------------------------------------------------------
// Shared helpers

function Crumbs({ parts }: { parts: { label: string; onClick?: () => void; mono?: boolean }[] }): JSX.Element {
  return (
    <div className="text-text-muted flex min-w-0 flex-wrap items-center gap-1 text-xs">
      {parts.map((part, index) => (
        <span key={`${index}:${part.label}`} className="inline-flex max-w-full min-w-0 items-center gap-1">
          {index > 0 ? <span aria-hidden="true">›</span> : null}
          {part.onClick ? (
            <button type="button" onClick={part.onClick} className="text-accent-text truncate underline-offset-2 hover:underline">{part.label}</button>
          ) : <span className={part.mono ? 'truncate font-mono' : 'truncate'}>{part.label}</span>}
        </span>
      ))}
    </div>
  );
}

function recipientLine(recipient: { id: string; name: string }): ReactNode {
  return <>{recipient.name} <span className="text-text-muted font-mono text-xs">({recipient.id})</span></>;
}

/** Item IDs the user may select: eligible, with no mapping or intent for this recipient. */
export function selectableIds(eligibility: ImportEligibility | undefined): string[] {
  return (eligibility?.items ?? []).filter((i) => i.eligible && i.mapping === null).map((i) => i.item_id);
}

/** The current selection: the explicit choice (still selectable), else every selectable item. */
export function effectiveSelection(explicit: string[] | undefined, eligibility: ImportEligibility | undefined): string[] {
  const selectable = selectableIds(eligibility);
  if (!explicit) return selectable;
  const allowed = new Set(selectable);
  return explicit.filter((id) => allowed.has(id));
}

function errorCode(error: unknown): string | null {
  if (error instanceof InstillLedgerError) return error.refusal ?? error.code;
  if (error instanceof ApiError) return error.code ?? `http_${error.status}`;
  return null;
}

function RecipientStatus({ eligibility, loading }: { eligibility: ImportEligibility | undefined; loading: boolean }): JSX.Element {
  const { t } = useTranslation();
  if (!eligibility) {
    return <Pill tone="neutral" icon={<Circle size={12} aria-hidden="true" />}>{loading ? t('knowledge.instill.status.checking') : t('knowledge.instill.status.unknown')}</Pill>;
  }
  if (eligibility.recipient_eligible) return <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.instill.status.eligible')}</Pill>;
  if (eligibility.reason === 'knowledge_import_busy_finish_wait_then_retry') {
    return <Pill tone="info" icon={<CircleDot size={12} aria-hidden="true" />}>{t('knowledge.instill.status.busy')}</Pill>;
  }
  return <Pill tone="danger" icon={<CircleX size={12} aria-hidden="true" />}>{t('knowledge.instill.status.notEligible')}</Pill>;
}

function BlockerList({ blockers }: { blockers: { kind: string; id: string }[] }): JSX.Element | null {
  const { t } = useTranslation();
  if (blockers.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {blockers.map((b) => (
        <li key={`${b.kind}:${b.id}`}>
          {BLOCKER[b.kind] ? t(BLOCKER[b.kind]) : b.kind}{' '}
          <span className="font-mono text-xs break-all">{b.id}</span>
        </li>
      ))}
    </ul>
  );
}

/** Recipient-level advisory state from the eligibility projection. */
function EligibilityNotice({ recipient, eligibility, onCheck, checking }: {
  recipient: { id: string; name: string };
  eligibility: ImportEligibility | undefined;
  onCheck: () => void;
  checking: boolean;
}): JSX.Element | null {
  const { t } = useTranslation();
  if (!eligibility || eligibility.recipient_eligible) return null;
  const code = eligibility.reason ?? '';
  const busy = code === 'knowledge_import_busy_finish_wait_then_retry';
  return (
    <Notice tone={busy ? 'neutral' : 'danger'} role="status" icon={<CircleDot size={16} aria-hidden="true" />}>
      <p>
        <span className="font-semibold">{busy ? t('knowledge.instill.busy.lead', { name: recipient.name }) : t(refusalKey(code))}</span>
        {busy ? ` ${t('knowledge.instill.busy.body')}` : null}
      </p>
      <BlockerList blockers={eligibility.blockers} />
      <p className="text-text-muted mt-1 font-mono text-xs break-all">{code}</p>
      <Button variant="outline" size="sm" className="mt-2" disabled={checking} loading={checking} onClick={onCheck}>
        {t('knowledge.instill.checkAgain')}
      </Button>
    </Notice>
  );
}

/** Compact one-line coverage (runtime, Claude, other provider memory). */
function coverageLine(coverage: SourceCoverage[], t: Translate): string {
  return ['runtime_memory', 'claude_memory', 'other_provider_memory']
    .map((category) => coverage.find((c) => c.category === category))
    .filter((c): c is SourceCoverage => !!c)
    .map((c) => {
      const label = COVERAGE_LABEL[c.category] ? t(COVERAGE_LABEL[c.category]) : c.category;
      const state = c.state === 'included' ? t('knowledge.instill.coverage.included')
        : c.state === 'unavailable' ? t('knowledge.instill.coverage.notCaptured') : t('knowledge.instill.coverage.excluded');
      return `${label} ${state}`;
    })
    .join(' · ');
}

/** Expandable coverage (closed by default) used by both preview and confirmation. */
function CoverageDisclosure({ coverage }: { coverage: SourceCoverage[] }): JSX.Element {
  const { t } = useTranslation();
  return (
    <details className="bg-surface border-border-default group rounded-lg border">
      <summary className="focus-visible:ring-ring flex cursor-pointer list-none items-start gap-2 rounded-lg px-4 py-3 text-xs focus-visible:ring-2 focus-visible:outline-none">
        <ChevronDown size={14} aria-hidden="true" className="text-text-muted mt-0.5 shrink-0 -rotate-90 group-open:rotate-0" />
        <span className="text-text-primary shrink-0 font-semibold tracking-widest uppercase">{t('knowledge.instill.coverage.heading')}</span>
        <span className="text-text-secondary min-w-0">{coverageLine(coverage, t)}</span>
      </summary>
      <div className="px-3 pb-3">
        <CoverageList coverage={coverage} mode="summary" label={t('knowledge.coverage.heading')} />
      </div>
    </details>
  );
}

/** Items of an archive already mapped (imported or reserved) into this recipient, from its history. */
function priorCount(history: ImportOperation[], archiveId: string): number {
  const ids = new Set<string>();
  history.filter((op) => op.archive_id === archiveId).forEach((op) => op.results.forEach((r) => {
    if (r.memory_id && (r.outcome === 'Imported' || r.outcome === 'Skipped')) ids.add(r.item_id);
  }));
  return ids.size;
}

// ---------------------------------------------------------------------------
// Step 1 — choose a Ready archive

function PickView({ recipient, archives, history, historyError, chosen, onChoose, onPush, onClose, onOpenTerminated, top }: InstillDrawerProps & { top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const list = useMemo(
    () => [...(archives.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.version - a.version),
    [archives.data],
  );
  // Recipient status is advisory and archive-independent apart from the
  // same-agent rule: read it for the chosen archive, else the newest one.
  const probe = chosen ?? list.find((a) => a.source_id !== recipient.id)?.archive_id ?? null;
  const eligibility = useImportEligibility(recipient.id, probe);
  const chosenArchive = list.find((a) => a.archive_id === chosen) ?? null;
  return (
    <Shell
      sourceName={recipient.name}
      title={t('knowledge.instill.title')}
      top={top}
      onClose={onClose}
      footer={(
        <>
          <div className="text-text-secondary min-w-0 text-xs">
            {t('knowledge.instill.footer.recipient')} <span className="text-text-primary font-semibold">{recipient.name}</span>
            {' · '}{t('knowledge.instill.footer.archive')}{' '}
            {chosenArchive ? <span className="text-text-primary font-mono">{chosenArchive.archive_id}</span> : <span className="text-text-primary font-semibold">{t('knowledge.instill.footer.noneChosen')}</span>}
          </div>
          <div className="flex shrink-0 items-center gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>
            <Button disabled={!chosenArchive} onClick={() => chosenArchive && onPush({ kind: 'preview', archiveId: chosenArchive.archive_id })}>
              {t('knowledge.instill.preview.open')}
            </Button>
          </div>
        </>
      )}
    >
      <div className="bg-surface border-border-default flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-3 text-sm">
        <span className="text-text-muted text-xs font-semibold tracking-widest uppercase">{t('knowledge.instill.recipient')}</span>
        <span className="text-text-primary">{recipientLine(recipient)}</span>
        {probe ? <RecipientStatus eligibility={eligibility.data} loading={eligibility.isLoading} /> : null}
      </div>
      {eligibility.isError ? (
        <div className="mt-3"><ErrorBlock error={eligibility.error} onRetry={() => void eligibility.refetch()} /></div>
      ) : null}
      <div className="mt-3">
        <EligibilityNotice recipient={recipient} eligibility={eligibility.data} checking={eligibility.isFetching}
          onCheck={() => void eligibility.refetch()} />
      </div>
      <p id="instill-pick-label" className="text-text-primary mt-4 text-sm font-medium">{t('knowledge.instill.pick.prompt', { name: recipient.name })}</p>
      <div className="mt-3">
        {archives.isLoading ? <LoadingBlock /> : archives.isError ? (
          <ErrorBlock error={archives.error} onRetry={archives.refetch} />
        ) : list.length === 0 ? (
          <div className="bg-surface border-border-default flex flex-col items-center gap-2 rounded-lg border px-6 py-8 text-center">
            <ArchiveX size={20} aria-hidden="true" className="text-text-muted" />
            <p className="font-display text-text-primary text-lg">{t('knowledge.instill.pick.emptyTitle')}</p>
            <p className="text-text-secondary max-w-sm text-sm">{t('knowledge.instill.pick.emptyBody', { name: recipient.name })}</p>
            <Button variant="outline" className="mt-2" onClick={onOpenTerminated}>{t('knowledge.instill.pick.openTerminated')}</Button>
          </div>
        ) : (
          <ul role="radiogroup" aria-labelledby="instill-pick-label" className="space-y-3">
            {list.map((a) => {
              const own = a.source_id === recipient.id;
              const prior = history ? priorCount(history, a.archive_id) : null;
              const selected = chosen === a.archive_id;
              return (
                <li key={a.archive_id}>
                  <label className={`bg-surface shadow-pasture-sm flex gap-3 rounded-lg border p-4 ${own ? 'border-border-default opacity-70' : selected ? 'border-accent ring-accent ring-1' : 'border-border-default hover:border-text-muted cursor-pointer'}`}>
                    <input type="radio" name="instill-archive" className="mt-1 shrink-0 self-start" value={a.archive_id}
                      checked={selected} disabled={own} onChange={() => onChoose(a.archive_id)} />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-text-primary font-mono text-sm font-semibold break-all">{a.archive_id}</span>
                        <span className="text-text-primary text-sm font-medium">{a.source_name}</span>
                        <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.state.ready')}</Pill>
                      </span>
                      <span className="text-text-secondary mt-1 block text-xs">
                        {t('knowledge.instill.pick.captured', { version: a.version, date: utc(a.cutoff) })}
                        {' · '}{t('knowledge.archive.contents', { items: a.item_count, eligible: a.eligible_count })}
                        {' · '}{t('knowledge.instill.pick.source', { id: a.source_id })}
                      </span>
                      <span className="text-text-secondary mt-1 block text-xs">{coverageLine(a.coverage, t)}</span>
                      <span className="text-text-primary mt-1 block text-xs font-medium">
                        {own ? t('knowledge.instill.pick.own')
                          : prior === null ? (historyError ? t('knowledge.instill.pick.priorUnreadable') : t('knowledge.instill.pick.priorChecking'))
                            : prior > 0 ? t('knowledge.instill.pick.prior', { count: prior }) : t('knowledge.instill.pick.priorNone')}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <p className="text-text-secondary mt-4 text-sm">{t('knowledge.instill.pick.readyOnly')}</p>
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — preview and select

type ItemState = 'selectable' | 'imported' | 'held' | 'incomplete' | 'ineligible';

function itemState(entry: EligibilityItem | undefined): ItemState {
  if (!entry) return 'ineligible';
  if (entry.mapping) {
    if (entry.mapping.state === 'completed') return 'imported';
    if (entry.mapping.state === 'held' || entry.mapping.state === 'writing') return 'held';
    return 'incomplete';
  }
  return entry.eligible ? 'selectable' : 'ineligible';
}

function ItemStatePill({ state }: { state: ItemState }): JSX.Element {
  const { t } = useTranslation();
  if (state === 'selectable') return <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.item.eligible')}</Pill>;
  if (state === 'imported') return <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.instill.item.imported')}</Pill>;
  if (state === 'held') return <Pill tone="attention" icon={<CircleHelp size={12} aria-hidden="true" />}>{t('knowledge.instill.item.held')}</Pill>;
  if (state === 'incomplete') return <Pill tone="attention" icon={<Clock size={12} aria-hidden="true" />}>{t('knowledge.instill.item.incomplete')}</Pill>;
  return <Pill tone="neutral" icon={<Circle size={12} aria-hidden="true" />}>{t('knowledge.instill.item.notSelectable')}</Pill>;
}

function ineligibleWhy(item: CuratedKnowledgeItem, t: Translate): string {
  const flags = item.flags.filter((f) => ['superseded', 'evicted', 'stale', 'promoted'].includes(f))
    .map((f) => (ITEM_FLAG[f] ? t(ITEM_FLAG[f]) : f));
  if (flags.length) return t('knowledge.instill.item.whyFlags', { flags: flags.join(', ') });
  if (!item.instill_eligible) return t('knowledge.instill.item.whyReference');
  return t('knowledge.instill.item.whyNoContent');
}

function MappingLine({ state, entry, item, onOpenReceipt }: { state: ItemState; entry: EligibilityItem | undefined; item: CuratedKnowledgeItem; onOpenReceipt: (operationId: string) => void }): JSX.Element | null {
  const { t } = useTranslation();
  const mapping = entry?.mapping;
  const opLink = mapping ? (
    <button type="button" onClick={() => onOpenReceipt(mapping.operation_id)}
      className="text-accent-text font-mono font-semibold underline-offset-2 hover:underline">{mapping.operation_id}</button>
  ) : null;
  if (state === 'imported' && mapping) {
    return <p className="text-text-secondary mt-3 flex flex-wrap items-center gap-1 text-xs"><Check size={12} aria-hidden="true" />{t('knowledge.instill.item.importedAs')} <span className="text-accent-text font-mono font-semibold">{mapping.memory_id}</span> ({opLink}). {t('knowledge.instill.item.notSelectableDot')}</p>;
  }
  if (state === 'held' && mapping) {
    return <p className="text-attention-text mt-3 flex flex-wrap items-center gap-1 text-xs"><CircleHelp size={12} aria-hidden="true" />{t('knowledge.instill.item.heldAs', { memory: mapping.memory_id })} ({opLink}). {t('knowledge.instill.item.notSelectableDot')}</p>;
  }
  if (state === 'incomplete' && mapping) {
    return <p className="text-attention-text mt-3 flex flex-wrap items-center gap-1 text-xs"><Clock size={12} aria-hidden="true" />{t('knowledge.instill.item.incompleteIn', { memory: mapping.memory_id })} {opLink}. {t('knowledge.instill.item.notSelectableDot')}</p>;
  }
  if (state === 'ineligible') {
    return <p className="text-text-secondary mt-3 flex flex-wrap items-center gap-1 text-xs"><Circle size={12} aria-hidden="true" />{ineligibleWhy(item, t)}</p>;
  }
  return null;
}

function PreviewView({ recipient, archiveId, explicit, onSelect, onPush, onPop, onClose, top }: InstillDrawerProps & { archiveId: string; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const archiveQuery = useKnowledgeArchive(archiveId);
  const eligibility = useImportEligibility(recipient.id, archiveId);
  const archive = archiveQuery.data;
  const elig = eligibility.data;
  const byId = useMemo(() => new Map((elig?.items ?? []).map((i) => [i.item_id, i])), [elig]);
  const selectable = selectableIds(elig);
  const selection = effectiveSelection(explicit[archiveId], elig);
  const chosen = new Set(selection);
  const already = (elig?.items ?? []).filter((i) => i.mapping?.state === 'completed').length;
  const ready = eligibility.status === 'success' && archiveQuery.status === 'success';
  const canReview = ready && !!elig?.recipient_eligible && selection.length > 0;
  const toggle = (itemId: string, on: boolean) => {
    const next = on ? [...selection, itemId] : selection.filter((id) => id !== itemId);
    onSelect(archiveId, selectable.filter((id) => next.includes(id)));
  };
  const source = archive ? { name: archive.manifest.source_name, source_id: archive.source_id } : null;
  return (
    <Shell
      sourceName={recipient.name}
      title={t('knowledge.instill.preview.title')}
      top={top}
      onClose={onClose}
      breadcrumb={<Crumbs parts={[{ label: t('knowledge.instill.title'), onClick: onPop }, { label: archiveId, mono: true }]} />}
      footer={(
        <>
          <div className="text-text-secondary min-w-0 text-xs">
            <p><span className="text-text-primary font-semibold">{recipient.name}</span> <span className="font-mono">({recipient.id})</span> · <span className="font-mono">{archiveId}</span> · <span className="text-text-primary font-semibold">{t('knowledge.instill.selectedCount', { count: selection.length })}</span></p>
            {elig && !elig.recipient_eligible ? <p className="text-attention-text mt-0.5">{t(refusalKey(elig.reason ?? ''))}</p>
              : ready && selection.length === 0 ? <p className="mt-0.5">{t('knowledge.instill.preview.zero')}</p> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onPop}>{t('knowledge.instill.preview.change')}</Button>
            <Button disabled={!canReview} onClick={() => onPush({ kind: 'confirm', archiveId })}>
              {t('knowledge.instill.preview.review', { count: selection.length })}
            </Button>
          </div>
        </>
      )}
    >
      {archiveQuery.isLoading || eligibility.isLoading ? <LoadingBlock />
        : archiveQuery.isError ? <ErrorBlock error={archiveQuery.error} onRetry={() => void archiveQuery.refetch()} />
          : eligibility.isError ? <ErrorBlock error={eligibility.error} onRetry={() => void eligibility.refetch()} />
            : archive && elig && source ? (
              <div className="space-y-4">
                <EligibilityNotice recipient={recipient} eligibility={elig} checking={eligibility.isFetching} onCheck={() => void eligibility.refetch()} />
                <div className="bg-surface border-border-default flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border px-4 py-3 text-sm">
                  <span className="text-text-muted text-xs font-semibold tracking-widest uppercase">{t('knowledge.instill.recipient')}</span>
                  <span className="text-text-primary">{recipientLine(recipient)}</span>
                  <RecipientStatus eligibility={elig} loading={false} />
                  <span className="text-text-muted text-xs font-semibold tracking-widest uppercase">{t('knowledge.instill.archive')}</span>
                  <span className="text-text-primary font-mono text-xs break-all">{archiveId}</span>
                  <span className="text-text-secondary">{t('knowledge.instill.fromSource', { name: source.name, version: archive.version })}</span>
                  <span className="text-text-muted text-xs font-semibold tracking-widest uppercase">{t('knowledge.instill.selected')}</span>
                  <span className="text-text-primary font-semibold tabular-nums">{selection.length}</span>
                </div>
                <CoverageDisclosure coverage={archive.manifest.coverage} />
                {already > 0 ? (
                  <p className="text-text-secondary flex items-center gap-1.5 text-sm"><Check size={14} aria-hidden="true" />{t('knowledge.instill.preview.already', { count: already })}</p>
                ) : null}
                {selectable.length > 0 ? (
                  <label className="border-border-default flex items-center gap-3 rounded-lg border border-dashed px-4 py-3 text-sm">
                    <input type="checkbox" checked={selection.length === selectable.length}
                      ref={(el) => { if (el) el.indeterminate = selection.length > 0 && selection.length < selectable.length; }}
                      onChange={(event) => onSelect(archiveId, event.target.checked ? selectable : [])} />
                    <span><span className="text-text-primary font-semibold">{t('knowledge.instill.preview.selectAll')}</span>{' '}
                      <span className="text-text-secondary">{t('knowledge.instill.preview.selectAllCount', { selected: selection.length, total: selectable.length })}</span></span>
                  </label>
                ) : (
                  <p className="text-text-secondary text-sm">{t('knowledge.instill.preview.noneSelectable')}</p>
                )}
                {archive.items.length === 0 ? (
                  <p className="text-text-secondary text-sm">{t('knowledge.archive.emptyExcluded')}</p>
                ) : (
                  <ul className="space-y-3" aria-label={t('knowledge.archive.items')}>
                    {archive.items.map((item) => {
                      const entry = byId.get(item.item_id);
                      const state = itemState(entry);
                      const on = chosen.has(item.item_id);
                      const id = `instill-item-${item.item_id}`;
                      return (
                        <li key={item.item_id} className={`bg-surface shadow-pasture-sm rounded-lg border p-4 ${on ? 'border-accent ring-accent ring-1' : 'border-border-default'}`}>
                          <div className="flex items-start gap-3">
                            <input id={id} type="checkbox" className="mt-0.5 shrink-0 self-start" checked={on} disabled={state !== 'selectable'}
                              aria-label={t('knowledge.instill.preview.selectItem', { item: item.item_id, title: item.title })}
                              onChange={(event) => toggle(item.item_id, event.target.checked)} />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-text-primary font-mono text-xs font-semibold">{item.item_id}</span>
                                <span className="text-text-secondary text-xs">{ITEM_CATEGORY[item.category] ? t(ITEM_CATEGORY[item.category]) : item.category}</span>
                                <ItemStatePill state={state} />
                                {item.flags.filter((f) => f === 'uncertain' || f === 'conflict' || f === 'attribution_unverified' || f === 'historical').map((flag) => (
                                  <Pill key={flag} tone={flag === 'historical' ? 'neutral' : 'attention'}>{ITEM_FLAG[flag] ? t(ITEM_FLAG[flag]) : flag}</Pill>
                                ))}
                              </div>
                              <ItemBody item={item} archive={archive} source={source}
                                onOpen={(referenceId, label) => onPush({ kind: 'reference', archiveId, referenceId, label })} />
                              <MappingLine state={state} entry={entry} item={item}
                                onOpenReceipt={(operationId) => onPush({ kind: 'receipt', operationId })} />
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="text-text-secondary text-sm">{t('knowledge.instill.preview.writesNothing')}</p>
              </div>
            ) : null}
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — single-drawer confirmation

function RefusalNotice({ error }: { error: unknown }): JSX.Element {
  const { t } = useTranslation();
  if (error instanceof InstillLedgerError) {
    const refusal = error.code === 'recovery_refused' ? error.refusal : null;
    return (
      <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
        <p className="font-semibold">{t(LEDGER[error.code])}</p>
        {refusal ? <p className="mt-1">{t(refusalKey(refusal))}</p> : null}
        <BlockerList blockers={error.blockers} />
        {refusal ? <p className="text-text-muted mt-1 font-mono text-xs break-all">{refusal}</p> : null}
      </Notice>
    );
  }
  const code = errorCode(error);
  if (error instanceof ApiError && error.status >= 400 && error.status < 500 && code) {
    const busy = code === 'knowledge_import_busy_finish_wait_then_retry';
    return (
      <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
        <p className="font-semibold">{t('knowledge.instill.refused.lead')}</p>
        <p className="mt-1">{t(refusalKey(code))}{busy ? ` ${t('knowledge.instill.busy.body')}` : ''}</p>
        <BlockerList blockers={blockersOf(error)} />
        <p className="text-text-muted mt-1 font-mono text-xs break-all">{code}</p>
        <p className="mt-1">{t('knowledge.instill.refused.kept')}</p>
      </Notice>
    );
  }
  return (
    <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
      <p className="font-semibold">{t('knowledge.instill.unknown.lead')}</p>
      <p className="mt-1">{t('knowledge.instill.unknown.body')}</p>
      {code ? <p className="text-text-muted mt-1 font-mono text-xs">{code}</p> : null}
    </Notice>
  );
}

function ConfirmView({ recipient, archiveId, explicit, gate, start, onPop, onClose, onPush, top }: InstillDrawerProps & { archiveId: string; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const archiveQuery = useKnowledgeArchive(archiveId);
  const eligibility = useImportEligibility(recipient.id, archiveId);
  const archive = archiveQuery.data;
  const selection = effectiveSelection(explicit[archiveId], eligibility.data);
  const items = (archive?.items ?? []).filter((i) => selection.includes(i.item_id));
  const unresolved = gate.unresolved;
  const recovering = !!unresolved && !start.pending;
  const blockedReason = !gate.ledgerReadable ? t('knowledge.instill.ledger.unavailable')
    : !gate.historyReady ? t('knowledge.instill.gate.history')
      : gate.inflight ? t('knowledge.instill.gate.inflight', { operation: gate.inflight.operation_id })
        : null;
  const canConfirm = !start.found && !!archive && selection.length > 0 && gate.ledgerReadable && gate.historyReady && !unresolved && !gate.inflight
    && !start.pending && eligibility.status === 'success';
  return (
    <Shell
      sourceName={recipient.name}
      title={t('knowledge.instill.confirm.title', { count: selection.length })}
      top={top}
      onClose={onClose}
      breadcrumb={<Crumbs parts={[{ label: t('knowledge.instill.title') }, { label: archiveId, onClick: onPop }, { label: t('knowledge.instill.confirm.crumb') }]} />}
      footer={(
        <>
          <div className="text-text-secondary min-w-0 text-xs">
            <span className="text-text-primary font-semibold">{recipient.name}</span> · <span className="font-mono">{archiveId}</span> · <span className="text-text-primary font-semibold">{t('knowledge.instill.selectedCount', { count: selection.length })}</span>
          </div>
          <div className="flex shrink-0 items-center gap-2 max-md:justify-end">
            <Button variant="ghost" onClick={onPop}>{t('knowledge.instill.confirm.back')}</Button>
            {recovering ? (
              <Button onClick={() => start.onRecover(unresolved.submission_id)}>{t('knowledge.instill.recover.check')}</Button>
            ) : (
              <Button disabled={!canConfirm} loading={start.pending}
                onClick={() => archive && start.onConfirm(archiveId, archive.version, selection)}>
                {t('knowledge.instill.confirm.go', { count: selection.length })}
              </Button>
            )}
          </div>
        </>
      )}
    >
      {archiveQuery.isLoading || eligibility.isLoading ? <LoadingBlock />
        : archiveQuery.isError ? <ErrorBlock error={archiveQuery.error} onRetry={() => void archiveQuery.refetch()} />
          : eligibility.isError ? <ErrorBlock error={eligibility.error} onRetry={() => void eligibility.refetch()} />
            : archive ? (
              <div className="space-y-4">
                {start.found ? (
                  <Notice tone="positive" role="status" icon={<Check size={16} aria-hidden="true" />}>
                    <p className="font-semibold">{t('knowledge.instill.found.lead')}</p>
                    <p className="mt-1">{t('knowledge.instill.found.body', { operation: start.found.operation_id })}</p>
                    <Button variant="outline" size="sm" className="mt-2" onClick={() => onPush({ kind: 'receipt', operationId: start.found!.operation_id })}>
                      {t('knowledge.instill.history.view')}
                    </Button>
                  </Notice>
                ) : start.error ? <RefusalNotice error={start.error} /> : null}
                {recovering ? (
                  <Notice tone="neutral" role="status" icon={<CircleHelp size={16} aria-hidden="true" />}>
                    <p className="font-semibold">{t('knowledge.instill.recover.lead')}</p>
                    <p className="mt-1">{t('knowledge.instill.recover.body')}</p>
                    <p className="text-text-muted mt-1 font-mono text-xs break-all">{unresolved.submission_id}</p>
                  </Notice>
                ) : null}
                {blockedReason ? (
                  <Notice tone="danger" role="status" icon={<CircleAlert size={16} aria-hidden="true" />}>
                    <p>{blockedReason}</p>
                    {gate.inflight ? (
                      <Button variant="outline" size="sm" className="mt-2" onClick={() => onPush({ kind: 'receipt', operationId: gate.inflight!.operation_id })}>
                        {t('knowledge.instill.history.view')}
                      </Button>
                    ) : null}
                  </Notice>
                ) : null}
                <p className="text-text-primary text-sm leading-relaxed">
                  {t('knowledge.instill.confirm.question', {
                    count: selection.length, archive: archiveId, source: archive.manifest.source_name, name: recipient.name, id: recipient.id,
                  })}
                </p>
                <Facts>
                  <Fact term={t('knowledge.facts.recipient')}>{recipientLine(recipient)}</Fact>
                  <Fact term={t('knowledge.instill.archive')}>
                    <span className="font-mono text-xs break-all">{archiveId}</span> · {archive.manifest.source_name} · {t('knowledge.instill.pick.captured', { version: archive.version, date: utc(archive.manifest.cutoff) })}
                  </Fact>
                  <Fact term={t('knowledge.instill.selected')}>
                    <span className="font-semibold tabular-nums">{selection.length}</span>
                    {items.length ? <> — {items.map((i) => (
                      <span key={i.item_id} className="mr-2 inline-block"><span className="font-mono text-xs">{i.item_id}</span> {ITEM_CATEGORY[i.category] ? t(ITEM_CATEGORY[i.category]) : i.category}</span>
                    ))}</> : null}
                  </Fact>
                </Facts>
                <CoverageDisclosure coverage={archive.manifest.coverage} />
                <p className="text-text-secondary text-sm">{t('knowledge.instill.confirm.newNotes', { name: recipient.name })}</p>
                <Notice tone="info" icon={<Info size={16} aria-hidden="true" />}>{t('knowledge.instill.freshSession', { name: recipient.name })}</Notice>
              </div>
            ) : null}
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Import receipt

const RECEIPT_TONE: Record<ImportOperation['state'], 'neutral' | 'info' | 'positive' | 'attention' | 'danger'> = {
  Queued: 'neutral', Processing: 'info', Succeeded: 'positive', 'Partially failed': 'attention', Failed: 'danger',
};

export function ImportStateBadge({ state }: { state: ImportOperation['state'] }): JSX.Element {
  const { t } = useTranslation();
  const icon = state === 'Succeeded' ? <Check size={12} aria-hidden="true" />
    : state === 'Failed' ? <CircleX size={12} aria-hidden="true" />
      : state === 'Partially failed' ? <CircleAlert size={12} aria-hidden="true" />
        : state === 'Processing' ? <CircleDot size={12} aria-hidden="true" /> : <Clock size={12} aria-hidden="true" />;
  return <Pill tone={RECEIPT_TONE[state]} icon={icon}>{STATE_KEY[state] ? t(STATE_KEY[state]) : state}</Pill>;
}

function Tile({ value, label, icon }: { value: number; label: string; icon: ReactNode }): JSX.Element {
  return (
    <div className="bg-surface border-border-default rounded-lg border px-3 py-2.5">
      <div className="font-display text-text-primary text-2xl tabular-nums">{value}</div>
      <div className="text-text-secondary mt-0.5 flex items-center gap-1 text-xs">{icon}{label}</div>
    </div>
  );
}

/** Retry-eligible items: the server marked them retryable and not held. */
export function retryableCount(op: ImportOperation): number {
  return op.results.filter((r) => r.outcome === 'Failed' && r.retryable && !r.held).length;
}

function ReceiptView({ recipient, operationId, history, gate, retry, onPush, onClose, top }: InstillDrawerProps & { operationId: string; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const query = useKnowledgeImport(operationId);
  const op = query.data;
  const archiveQuery = useKnowledgeArchive(op?.archive_id ?? null);
  const eligibility = useImportEligibility(recipient.id, op?.archive_id ?? null);
  const archive = archiveQuery.data;
  const items = new Map((archive?.items ?? []).map((i) => [i.item_id, i]));
  const mappings = new Map((eligibility.data?.items ?? []).map((i) => [i.item_id, i]));
  const held = op ? op.results.filter((r) => r.held).length : 0;
  const retryable = op ? retryableCount(op) : 0;
  const retrying = retry.pending && retry.operationId === operationId;
  // Retry needs an authoritative, current read of THIS operation.
  const authoritative = query.status === 'success' && !query.isFetching;
  const canRetry = !!op && authoritative && op.state !== 'Succeeded' && retryable > 0 && !retry.pending
    && !(gate.inflight && gate.inflight.operation_id !== operationId);
  const retryError = retry.operationId === operationId ? retry.error : null;
  const sourceName = archive?.manifest.source_name
    ?? history?.find((o) => o.operation_id === operationId)?.source_id ?? op?.source_id ?? '';
  const outside = archive && op ? archive.items.filter((i) => !op.item_ids.includes(i.item_id)) : [];
  return (
    <Shell
      sourceName={recipient.name}
      title={t('knowledge.instill.receipt.title')}
      top={top}
      onClose={onClose}
      footer={(
        <>
          <div className="text-text-secondary min-w-0 truncate text-xs">
            {recipient.name} · <span className="font-mono">{op?.archive_id ?? '—'}</span> · {t('knowledge.instill.selectedCount', { count: op?.selected ?? 0 })}
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 max-md:justify-end">
            {op && (op.state === 'Queued' || op.state === 'Processing') ? (
              <Button variant="secondary" onClick={onClose}>{t('knowledge.instill.receipt.leave')}</Button>
            ) : (
              <>
                <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>
                {op && (op.imported + op.skipped) > 0 ? <Button variant="outline" onClick={onClose}>{t('knowledge.instill.receipt.viewMemories')}</Button> : null}
                {op && op.state !== 'Succeeded' && retryable > 0 ? (
                  <Button disabled={!canRetry} loading={retrying} onClick={() => retry.onRetry(operationId)}>
                    {t('knowledge.instill.receipt.retry', { count: retryable })}
                  </Button>
                ) : null}
              </>
            )}
          </div>
        </>
      )}
    >
      {query.isLoading ? <LoadingBlock /> : query.isError && !op ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : op ? (
        <div className="space-y-4">
          {query.isError ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : null}
          <div className="flex flex-wrap items-center gap-2">
            <ImportStateBadge state={op.state} />
            <span className="text-text-secondary text-sm">{t('knowledge.instill.receipt.operation')} <span className="font-mono">{op.operation_id}</span></span>
          </div>
          {op.state === 'Queued' || op.state === 'Processing' ? (
            <Notice tone="neutral" role="status" icon={<Clock size={16} aria-hidden="true" />}>{t('knowledge.instill.receipt.inflight', { count: op.selected, name: recipient.name })}</Notice>
          ) : null}
          <Facts>
            <Fact term={t('knowledge.facts.recipient')}>{recipientLine(recipient)}</Fact>
            <Fact term={t('knowledge.instill.archive')}>
              <span className="font-mono text-xs break-all">{op.archive_id}</span> · {t('knowledge.common.version', { version: op.archive_version })} · {t('knowledge.instill.fromSourceShort', { name: sourceName })}
            </Fact>
            <Fact term={t('knowledge.facts.started')}>{utc(op.started_at ?? op.created_at)}</Fact>
            <Fact term={t('knowledge.facts.lastUpdate')}>{utc(op.updated_at)}</Fact>
            <Fact term={t('knowledge.facts.phase')}>{PHASE_KEY[op.phase] ? t(PHASE_KEY[op.phase]) : op.phase} <span className="text-text-muted font-mono text-xs">({op.phase})</span></Fact>
            <Fact term={t('knowledge.instill.receipt.submission')}><span className="font-mono text-xs break-all">{op.submission_id}</span></Fact>
          </Facts>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
            <Tile value={op.imported} label={t('knowledge.instill.count.imported')} icon={<Check size={12} aria-hidden="true" />} />
            <Tile value={op.skipped} label={t('knowledge.instill.count.skipped')} icon={<Circle size={12} aria-hidden="true" />} />
            <Tile value={op.failed - held} label={t('knowledge.instill.count.failed')} icon={<CircleX size={12} aria-hidden="true" />} />
            {held > 0 ? <Tile value={held} label={t('knowledge.instill.count.held')} icon={<CircleHelp size={12} aria-hidden="true" />} /> : null}
            {op.pending > 0 ? <Tile value={op.pending} label={t('knowledge.instill.count.pending')} icon={<Clock size={12} aria-hidden="true" />} /> : null}
          </div>
          <p className="text-text-secondary text-xs">
            {t('knowledge.instill.count.equation', {
              selected: op.selected, imported: op.imported, skipped: op.skipped, failed: op.failed - held, held, pending: op.pending,
            })}
          </p>
          <ul className="bg-surface border-border-default overflow-hidden rounded-lg border" aria-label={t('knowledge.instill.receipt.items')}>
            {op.results.map((r) => {
              const item = items.get(r.item_id);
              const original = mappings.get(r.item_id)?.mapping;
              const otherOp = r.reason === 'knowledge_import_incomplete_intent_retry_original_operation'
                && original && original.operation_id !== op.operation_id ? original.operation_id : null;
              const pill = r.held ? <Pill tone="attention" icon={<CircleHelp size={12} aria-hidden="true" />}>{t('knowledge.instill.count.held')}</Pill>
                : r.outcome === 'Imported' ? <Pill tone="positive" icon={<Check size={12} aria-hidden="true" />}>{t('knowledge.instill.outcome.imported')}</Pill>
                  : r.outcome === 'Skipped' ? <Pill tone="neutral" icon={<Circle size={12} aria-hidden="true" />}>{t('knowledge.instill.outcome.skipped')}</Pill>
                    : r.outcome === 'Pending' ? <Pill tone="neutral" icon={<Clock size={12} aria-hidden="true" />}>{t('knowledge.instill.outcome.pending')}</Pill>
                      : <Pill tone="danger" icon={<CircleX size={12} aria-hidden="true" />}>{t('knowledge.instill.outcome.failed')}</Pill>;
              return (
                <li key={r.item_id} className="border-border-subtle border-b px-4 py-3 last:border-b-0">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="text-text-primary min-w-0 text-sm">
                      <span className="font-mono text-xs font-semibold">{r.item_id}</span>
                      {item ? <> · {ITEM_CATEGORY[item.category] ? t(ITEM_CATEGORY[item.category]) : item.category}</> : null}
                      {r.memory_id && (r.outcome === 'Imported' || r.outcome === 'Skipped') ? (
                        <> → <button type="button" onClick={() => onPush({ kind: 'memory', memoryId: r.memory_id! })}
                          className="text-accent-text font-mono text-xs font-semibold underline-offset-2 hover:underline">{r.memory_id} →</button></>
                      ) : null}
                    </p>
                    {pill}
                  </div>
                  <p className="text-text-secondary mt-1 text-xs leading-relaxed">
                    {r.outcome === 'Imported' && !r.reason ? t('knowledge.instill.outcome.importedNote', { name: recipient.name, source: sourceName })
                      : r.outcome === 'Pending' ? t('knowledge.instill.outcome.pendingNote')
                        : r.reason ? t(ITEM_REASON[r.reason] ?? refusalKey(r.reason), { memory: r.memory_id ?? '—' }) : null}
                    {r.outcome === 'Failed' && !r.held ? ` ${r.retryable ? t('knowledge.instill.outcome.retryable') : t('knowledge.instill.outcome.notRetryable')}` : null}
                  </p>
                  {otherOp ? (
                    <p className="text-text-secondary mt-1 text-xs">
                      {t('knowledge.instill.outcome.originalOperation')}{' '}
                      <button type="button" onClick={() => onPush({ kind: 'receipt', operationId: otherOp })}
                        className="text-accent-text font-mono font-semibold underline-offset-2 hover:underline">{otherOp}</button>
                    </p>
                  ) : null}
                  {r.reason ? <p className="text-text-muted mt-1 font-mono text-xs break-all">{r.reason}</p> : null}
                </li>
              );
            })}
          </ul>
          {outside.length > 0 ? (
            <p className="text-text-secondary text-sm">
              <span className="text-text-primary font-semibold">{t('knowledge.instill.receipt.outside')}</span>{' '}
              {outside.map((i) => {
                const m = mappings.get(i.item_id);
                const status = m?.mapping?.state === 'completed' ? t('knowledge.instill.receipt.outsideImported', { memory: m.mapping.memory_id })
                  : m?.mapping ? t('knowledge.instill.receipt.outsideHeld') : m?.eligible ? t('knowledge.instill.receipt.outsideNotSelected') : t('knowledge.instill.receipt.outsideIneligible');
                return `${i.item_id} ${status}`;
              }).join(' · ')}
            </p>
          ) : null}
          {op.imported + op.skipped > 0 ? (
            <Notice tone="info" icon={<Info size={16} aria-hidden="true" />}>
              <span className="font-semibold">{t('knowledge.instill.freshLead')}</span> {t('knowledge.instill.freshBody', { name: recipient.name })}
            </Notice>
          ) : null}
          {retryable > 0 && op.state !== 'Succeeded' ? (
            <Notice tone="neutral" icon={<CircleAlert size={16} aria-hidden="true" />}>{t('knowledge.instill.receipt.retryNote', { count: retryable })}</Notice>
          ) : null}
          {held > 0 ? (
            <Notice tone="neutral" icon={<CircleHelp size={16} aria-hidden="true" />}>
              <span className="font-semibold">{t('knowledge.instill.receipt.heldLead', { count: held })}</span> {t('knowledge.instill.receipt.heldBody')}
            </Notice>
          ) : null}
          {retryError ? (
            retryError instanceof ApiError && retryError.status >= 400 && retryError.status < 500 ? (
              <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
                <p className="font-semibold">{t('knowledge.instill.retryRefused')}</p>
                <p className="mt-1">{t(refusalKey(errorCode(retryError) ?? ''))}</p>
                <BlockerList blockers={blockersOf(retryError)} />
                <p className="text-text-muted mt-1 font-mono text-xs break-all">{errorCode(retryError)}</p>
              </Notice>
            ) : (
              <Notice tone="danger" role="alert" icon={<CircleAlert size={16} aria-hidden="true" />}>
                <p className="font-semibold">{t('knowledge.instill.retryUnknown')}</p>
                {errorCode(retryError) ? <p className="text-text-muted mt-1 font-mono text-xs">{errorCode(retryError)}</p> : null}
              </Notice>
            )
          ) : null}
        </div>
      ) : null}
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Read-only source record (return preserves recipient, archive and selection)

function backLabelFor(view: InstillView | null, t: Translate): string | null {
  if (!view) return null;
  if (view.kind === 'preview') return t('knowledge.instill.back.preview', { archive: view.archiveId });
  if (view.kind === 'receipt') return t('knowledge.instill.back.receipt');
  if (view.kind === 'memory') return t('knowledge.instill.back.memory', { memory: view.memoryId });
  return null;
}

function ReferenceView({ recipient, archiveId, referenceId, label, stack, depth, onPop, onClose, top }: InstillDrawerProps & { archiveId: string; referenceId: string; label: string; depth: number; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const archiveQuery = useKnowledgeArchive(archiveId);
  const archive = archiveQuery.data;
  const backLabel = backLabelFor(depth > 0 ? stack[depth - 1] : null, t);
  return (
    <Shell
      sourceName={recipient.name}
      title={t('knowledge.reference.title', { record: label })}
      top={top}
      onClose={onClose}
      breadcrumb={backLabel ? <Crumbs parts={[{ label: backLabel, onClick: onPop }, { label, mono: true }]} /> : undefined}
      footer={(
        <>
          <div className="text-text-muted min-w-0 truncate text-xs"><span className="font-mono">{label}</span> · {archive?.manifest.source_name ?? ''}</div>
          {backLabel ? (
            <Button variant="secondary" className="max-w-full min-w-0" onClick={onPop}><ChevronLeft aria-hidden="true" /><span className="truncate">{backLabel}</span></Button>
          ) : <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>}
        </>
      )}
    >
      {archiveQuery.isLoading ? <LoadingBlock /> : archiveQuery.isError ? <ErrorBlock error={archiveQuery.error} onRetry={() => void archiveQuery.refetch()} />
        : archive ? <ReferenceRecord source={{ name: archive.manifest.source_name, source_id: archive.source_id }} referenceId={referenceId} label={label} /> : null}
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Recipient memory note (existing memory read) with inherited attribution

function MemoryView({ recipient, memoryId, history, stack, depth, onPush, onPop, onClose, top }: InstillDrawerProps & { memoryId: string; depth: number; top: boolean }): JSX.Element {
  const { t } = useTranslation();
  const utc = useUtc();
  const query = useAgentMemoryEntry(recipient.id, memoryId);
  const entry = query.data;
  const inherited = entry?.inherited_from ?? null;
  const archiveQuery = useKnowledgeArchive(inherited?.archive_id ?? null);
  const archive = archiveQuery.data;
  const item = archive?.items.find((i) => i.item_id === inherited?.archive_item_id);
  const operation = history?.find((op) => op.results.some((r) => r.memory_id === memoryId && (r.outcome === 'Imported' || r.outcome === 'Skipped')));
  const backLabel = backLabelFor(depth > 0 ? stack[depth - 1] : null, t);
  const title = entry ? `${entry.id} · ${entry.title}` : memoryId;
  return (
    <Shell
      sourceName={recipient.name}
      title={title}
      top={top}
      onClose={onClose}
      breadcrumb={backLabel
        ? <Crumbs parts={[{ label: backLabel, onClick: onPop }, { label: memoryId, mono: true }]} />
        : <div className="text-text-muted truncate text-xs">{t('knowledge.instill.memory.crumb', { name: recipient.name })}</div>}
      footer={(
        <>
          <div className="text-text-muted min-w-0 truncate text-xs"><span className="font-mono">{memoryId}</span>{entry ? ` · ${entry.topic}` : ''}</div>
          {backLabel ? (
            <Button variant="secondary" className="max-w-full min-w-0" onClick={onPop}><ChevronLeft aria-hidden="true" /><span className="truncate">{backLabel}</span></Button>
          ) : <Button variant="ghost" onClick={onClose}>{t('knowledge.common.close')}</Button>}
        </>
      )}
    >
      {query.isLoading ? <LoadingBlock /> : query.isError ? <ErrorBlock error={query.error} onRetry={() => void query.refetch()} /> : entry ? (
        <div className="space-y-4">
          <blockquote className="border-accent bg-surface text-text-primary rounded-r-lg border-l-4 px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">
            {item?.content ?? entry.body}
          </blockquote>
          {inherited ? (
            <Facts>
              <Fact term={t('knowledge.instill.memory.inheritedFrom')}>
                {archive ? archive.manifest.source_name : null} <span className="text-text-muted font-mono text-xs">({inherited.source_id})</span>
              </Fact>
              {inherited.source_dates.map((d) => (
                <Fact key={d.reference_id} term={t('knowledge.instill.memory.sourceDates')}>
                  {t('knowledge.instill.memory.dates', {
                    authored: d.authored_at ? utc(d.authored_at) : t('knowledge.common.unknownDate'),
                    observed: d.observed_at ? utc(d.observed_at) : t('knowledge.common.unknownDate'),
                  })}
                </Fact>
              ))}
              <Fact term={t('knowledge.instill.memory.archiveItem')}>
                <span className="font-mono text-xs break-all">{inherited.archive_id}</span> · {t('knowledge.common.version', { version: inherited.archive_version })} · <span className="font-mono text-xs">{inherited.archive_item_id}</span>
                {item ? <> ({ITEM_CATEGORY[item.category] ? t(ITEM_CATEGORY[item.category]) : item.category})</> : null}
              </Fact>
              <Fact term={t('knowledge.instill.memory.imported')}>{utc(entry.imported_at)}</Fact>
              {operation ? (
                <Fact term={t('knowledge.instill.memory.operation')}>
                  <button type="button" onClick={() => onPush({ kind: 'receipt', operationId: operation.operation_id })}
                    className="text-accent-text font-mono text-xs font-semibold underline-offset-2 hover:underline">{operation.operation_id}</button>
                </Fact>
              ) : null}
            </Facts>
          ) : (
            <Facts>
              <Fact term={t('knowledge.instill.memory.provenance')}>{entry.provenance}</Fact>
              <Fact term={t('knowledge.facts.authored')}>{utc(entry.authored_at)}</Fact>
            </Facts>
          )}
          {item && item.warnings.length > 0 ? (
            <div className="bg-attention-soft text-text-primary space-y-1 rounded-md px-3 py-2 text-xs leading-relaxed">
              {item.warnings.map((w) => <p key={w}>{w}</p>)}
            </div>
          ) : null}
          {inherited ? (
            <Notice tone="neutral" icon={<Lock size={16} aria-hidden="true" />}>{t('knowledge.instill.memory.noAuthority', { name: recipient.name })}</Notice>
          ) : null}
          {inherited ? (
            <details className="bg-surface border-border-default rounded-lg border">
              <summary className="text-accent-text cursor-pointer px-4 py-2.5 text-xs font-semibold">{t('knowledge.instill.memory.stored')}</summary>
              <p className="text-text-secondary border-border-subtle border-t px-4 py-3 text-xs leading-relaxed whitespace-pre-wrap">{entry.body}</p>
            </details>
          ) : null}
        </div>
      ) : null}
    </Shell>
  );
}

/** Small marker for an inherited note in the learnings list. */
export function InheritedMarker({ sourceLabel }: { sourceLabel: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span className="text-info bg-info-soft inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium">
      <Archive size={12} aria-hidden="true" />{t('knowledge.instill.inherited', { source: sourceLabel })}
    </span>
  );
}

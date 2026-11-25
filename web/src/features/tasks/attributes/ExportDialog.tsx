/**
 * Bounded root-task export (design v1.2 frame G layout, PR8 copy deltas).
 *
 * One server page per request via GET /tasks/roots/export with the SAME
 * status / assigned_agent / attr_* as the visible list, an explicit `limit`
 * (default 1000) and, for "Export next page", `before=<next cursor>`. The UI
 * never concatenates pages, never assembles rows client-side and never calls
 * a file complete. Closing, or any org/filter change (which remounts the list),
 * aborts the request and drops any unrevealed result.
 */
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Download } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
import { useTaskRootsExport } from '@/hooks/tasks';
import { ApiError } from '@/lib/api';
import type { AttributeFilter, TaskRootsExportFormat, TaskRootsExportResult } from '@/lib/api/types';
import type { MessageKey, MessageParams } from '@/lib/i18n';

export interface ExportSelection {
  slug: string;
  status?: string;
  statusLabel?: string;
  assignedAgent?: string;
  agentLabel?: string;
  attributes: readonly AttributeFilter[];
  /** Localized clause texts, in the applied order. */
  clauseTexts: string[];
}

type Phase =
  | { kind: 'summary' }
  | { kind: 'preparing' }
  | { kind: 'ready'; result: TaskRootsExportResult; rows: number | null }
  | { kind: 'message'; key: MessageKey; params?: MessageParams; tone: 'bad' | 'warn' };

const LIMIT_RE = /^[1-9][0-9]*$/;

function outcomeFor(error: unknown): Extract<Phase, { kind: 'message' }> {
  if (error instanceof DOMException && error.name === 'AbortError') return { kind: 'message', key: 'tasks.export.unknown', tone: 'warn' };
  if (!(error instanceof ApiError)) return { kind: 'message', key: 'tasks.export.failed', tone: 'bad' };
  if (error.status === 401) return { kind: 'message', key: 'tasks.export.permLost', tone: 'bad' };
  if (error.status === 403) return { kind: 'message', key: 'tasks.export.forbidden', tone: 'bad' };
  const detail = error.detail as { errors?: Record<string, unknown> } | null;
  const reasons = detail && typeof detail === 'object' && detail.errors ? Object.values(detail.errors).flat() : [];
  if (error.status === 413) {
    if (reasons.includes('too_many_attribute_columns')) return { kind: 'message', key: 'tasks.export.tooManyColumns', tone: 'bad' };
    return { kind: 'message', key: 'tasks.export.bodyTooLarge', tone: 'bad' };
  }
  if (error.status === 422) {
    const reason = typeof reasons[0] === 'string' ? reasons[0] : (error.code ?? '422');
    return { kind: 'message', key: 'tasks.export.refused', params: { reason }, tone: 'bad' };
  }
  return { kind: 'message', key: 'tasks.export.failed', tone: 'bad' };
}

/** Row count from the file itself: JSON `tasks.length`, CSV records minus the header. */
async function countRows(result: TaskRootsExportResult, format: TaskRootsExportFormat): Promise<number | null> {
  try {
    const text = await result.blob.text();
    if (format === 'json') {
      const body = JSON.parse(text) as { tasks?: unknown[] };
      return Array.isArray(body.tasks) ? body.tasks.length : null;
    }
    // RFC 4180: count CRLF record ends outside quotes, minus the header.
    let records = 0;
    let quoted = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (ch === '"') quoted = !quoted;
      else if (!quoted && ch === '\n') records += 1;
    }
    if (text.length > 0 && !text.endsWith('\n')) records += 1;
    return Math.max(0, records - 1);
  } catch {
    return null;
  }
}

export function ExportDialog({ selection, onClose }: { selection: ExportSelection; onClose: () => void }): JSX.Element {
  const { t } = useTranslation();
  const [format, setFormat] = useState<TaskRootsExportFormat>('json');
  const [limitText, setLimitText] = useState('1000');
  const [limitError, setLimitError] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: 'summary' });
  // The page a Retry repeats: undefined = first page, else the cursor sent.
  const lastBefore = useRef<string | undefined>(undefined);
  const exportTaskRoots = useTaskRootsExport();
  const controller = useRef<AbortController | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; controller.current?.abort(); };
  }, []);

  const run = async (before?: string) => {
    if (phase.kind === 'preparing') return;
    if (!LIMIT_RE.test(limitText) || Number(limitText) > 1000) { setLimitError(true); return; }
    setLimitError(false);
    lastBefore.current = before;
    const abort = new AbortController();
    controller.current = abort;
    setPhase({ kind: 'preparing' });
    try {
      const result = await exportTaskRoots({
        format,
        limit: Number(limitText),
        ...(selection.status ? { status: selection.status } : {}),
        ...(selection.assignedAgent ? { assigned_agent: selection.assignedAgent } : {}),
        attributes: selection.attributes,
        ...(before ? { before } : {}),
      }, abort.signal);
      const rows = await countRows(result, format);
      if (alive.current && controller.current === abort) setPhase({ kind: 'ready', result, rows });
    } catch (error) {
      if (alive.current && controller.current === abort) setPhase(outcomeFor(error));
    }
  };

  const download = (result: TaskRootsExportResult) => {
    const objectUrl = URL.createObjectURL(result.blob);
    try {
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = result.filename ?? `task-roots.${format}`;
      a.click();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  };

  const summaryRow = (label: string, value: string, mono = false) => (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd className={`text-text-primary min-w-0 break-words ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </>
  );
  const busy = phase.kind === 'preparing';
  const ready = phase.kind === 'ready' ? phase : null;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent closeLabel={t('tasks.export.close')} className="tasks-export-dialog sm:max-w-xl"
        data-testid="tasks-export-dialog">
        <DialogHeader>
          <DialogTitle tabIndex={-1}>{t('tasks.export.title')}</DialogTitle>
          <DialogDescription className="sr-only">{t('tasks.export.selection')}</DialogDescription>
        </DialogHeader>
        <div className="border-border-default bg-surface-raised rounded-sm border px-3 py-2.5 text-sm">
          <dl className="tasks-export-summary gap-x-4 gap-y-1">
            {summaryRow(t('tasks.export.org'), selection.slug, true)}
            {summaryRow(t('tasks.export.scope'), t('tasks.export.scopeRoots'))}
            {summaryRow(t('tasks.export.status'), selection.statusLabel ?? t('tasks.export.any'))}
            {summaryRow(t('tasks.export.agent'), selection.agentLabel ?? t('tasks.export.any'))}
            {summaryRow(t('tasks.export.filters'), selection.clauseTexts.length
              ? selection.clauseTexts.join(` ${t('tasks.filters.attr.and')} `) : t('tasks.export.none'))}
          </dl>
        </div>
        <fieldset className="min-w-0 border-0 p-0">
          <legend className="text-text-secondary mb-1.5 text-xs font-medium">{t('tasks.export.format')}</legend>
          <div className="flex flex-col gap-2">
            {(['json', 'csv'] as const).map((value) => (
              <label key={value} className={`flex cursor-pointer items-start gap-2.5 rounded-sm border px-3 py-2.5 text-sm font-semibold ${format === value ? 'border-accent-default bg-accent-soft' : 'border-border-strong'}`}>
                <input type="radio" name="tasks-export-format" value={value} checked={format === value} className="mt-0.5"
                  disabled={busy} onChange={() => { setFormat(value); lastBefore.current = undefined; if (phase.kind !== 'summary') setPhase({ kind: 'summary' }); }} />
                <span className="min-w-0">
                  {t(value === 'json' ? 'tasks.export.json' : 'tasks.export.csv')}
                  <span className="text-text-secondary mt-0.5 block text-xs font-normal">{t(value === 'json' ? 'tasks.export.jsonHelp' : 'tasks.export.csvHelp')}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="min-w-0">
          <label className="text-text-secondary block text-xs font-medium" htmlFor="tasks-export-limit">{t('tasks.export.limitLabel')}</label>
          <input id="tasks-export-limit" inputMode="numeric" value={limitText} disabled={busy}
            aria-invalid={limitError || undefined} aria-describedby="tasks-export-limit-help"
            className="border-border-default bg-surface-raised text-text-primary mt-1 block w-32 rounded-sm border px-3 py-2 font-mono text-sm"
            onChange={(event) => {
              setLimitText(event.target.value);
              setLimitError(false);
              // After a continuation-page failure (e.g. 413 "lower the row limit and try again") keep
              // the failure and its Retry, which repeats that SAME page (`before=<failed cursor>`)
              // with the new limit; returning to the summary would silently restart page 1.
              if (phase.kind === 'message' && lastBefore.current !== undefined) return;
              if (phase.kind !== 'summary' && phase.kind !== 'preparing') setPhase({ kind: 'summary' });
            }} />
          <p id="tasks-export-limit-help" className={`mt-1 text-xs ${limitError ? 'text-feedback-danger' : 'text-text-muted'}`}>
            {t(limitError ? 'tasks.export.limitInvalid' : 'tasks.export.limitHelp')}
          </p>
        </div>
        <div aria-live="polite" className="min-w-0">
          {busy && <p role="status" className="text-text-secondary text-sm">{t('tasks.export.generating')}</p>}
          {ready && (
            <div role="status" className="border-accent-default bg-accent-soft text-text-primary rounded-sm border px-3.5 py-3 text-sm font-semibold" data-testid="tasks-export-receipt">
              {ready.rows !== null && <p>{t('tasks.export.receipt', { count: ready.rows, fmt: format === 'csv' ? 'CSV' : 'JSON' })}</p>}
              {ready.result.pageFull && <p className="text-text-secondary mt-1 text-xs font-normal">{t('tasks.export.moreMayMatch')}</p>}
            </div>
          )}
          {phase.kind === 'message' && (
            <div role="alert" className={`flex items-start gap-2 rounded-sm border px-3 py-2.5 text-sm ${phase.tone === 'bad' ? 'border-feedback-danger bg-danger-soft' : 'border-feedback-warning bg-attention-soft'}`}>
              <AlertCircle size={16} aria-hidden className="mt-0.5 shrink-0" />
              <p className="text-text-primary min-w-0">{t(phase.key, phase.params)}</p>
            </div>
          )}
        </div>
        <DialogFooter className="flex-wrap">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            {t(phase.kind === 'summary' || busy ? 'tasks.export.cancel' : 'tasks.export.close')}
          </Button>
          {ready?.result.pageFull && ready.result.nextCursor && (
            <Button type="button" variant="outline" size="sm" onClick={() => void run(ready.result.nextCursor ?? undefined)}>
              {t('tasks.export.nextPage')}
            </Button>
          )}
          {ready ? (
            <Button type="button" size="sm" onClick={() => download(ready.result)}>
              <Download size={14} aria-hidden /> {t('tasks.export.download')}
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={busy} loading={busy}
              onClick={() => void run(phase.kind === 'message' ? lastBefore.current : undefined)}>
              <Download size={14} aria-hidden /> {t(phase.kind === 'message' ? 'tasks.export.retry' : 'tasks.export.export')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

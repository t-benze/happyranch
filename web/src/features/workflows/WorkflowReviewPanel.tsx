import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useTranslation } from '@/hooks/i18n';
import { useHumanReviewResponse, useOwnerReviewReturn } from '@/hooks/workflows';
import type { MessageKey } from '@/lib/i18n';
import {
  admittedCommand, clearCommand, clearSelectionCommands, exactBytes, humanCommand, loadCommand,
  ownerIdentity, responseIdentity, returnCommand, saveCommand, validateHumanResponse, validateReturnResponse,
  type CommandEnvelope, type ReviewDetail,
} from '@/lib/workflowReviewSelection';
import type { HumanReviewCommand } from '@/lib/api/types';
import { ReviewResponseDialog } from './ReviewResponseDialog';
import { OwnerReturnDialog } from './OwnerReturnDialog';
import { reviewDiagnostic } from './strings';

export function WorkflowReviewPanel({ detail: d, fresh, refresh, captureAdmission }: {
  detail: ReviewDetail; fresh: boolean; captureAdmission: (detail: ReviewDetail) => () => boolean; refresh: () => Promise<ReviewDetail | undefined>;
}): JSX.Element {
  const { t } = useTranslation();
  const human = useHumanReviewResponse(), owner = useOwnerReviewReturn();
  const [draft, setDraft] = useState('');
  const [outcome, setOutcome] = useState<HumanReviewCommand['outcome'] | null>(null);
  const [returnOpen, setReturnOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [responseEnvelope, setResponseEnvelope] = useState<CommandEnvelope | null>(null);
  const [returnEnvelope, setReturnEnvelope] = useState<CommandEnvelope | null>(null);
  const [journalReady, setJournalReady] = useState(false);
  const [errorKey, setErrorKey] = useState<MessageKey | null>(null);
  const [diagnostic, setDiagnostic] = useState('');
  const [refused, setRefused] = useState(false);
  const [conflict, setConflict] = useState(false);
  const responseTrigger = useRef<HTMLButtonElement | null>(null), ownerTrigger = useRef<HTMLButtonElement | null>(null), heading = useRef<HTMLHeadingElement | null>(null);
  const restoreFocus = (element: HTMLButtonElement | null) => { if (element?.isConnected && !element.disabled) element.focus(); else heading.current?.focus(); };
  const alive = useRef(true), latch = useRef(false), freshRef = useRef(fresh);
  freshRef.current = fresh;
  useLayoutEffect(() => { alive.current = true; return () => { alive.current = false; freshRef.current = false; }; }, []);
  const responseId = responseIdentity(d), ownerId = ownerIdentity(d);
  useEffect(() => {
    let active = true;
    async function restore() {
      try {
        const r = responseId ? loadCommand(responseId) : null;
        const o = ownerId ? loadCommand(ownerId) : null;
        if (r) await admittedCommand(r, d);
        if (o) await admittedCommand(o, d);
        if (!active) return;
        setResponseEnvelope(r); setReturnEnvelope(o); setJournalReady(true);
      } catch { if (active) { setErrorKey('workflows.storage'); setJournalReady(false); } }
    }
    void restore();
    return () => { active = false; };
  }, [responseId, ownerId, d]);
  const q = d.request;
  const original = d.history || !d.selection.successor;
  const graph = original ? d.handoff?.current.graph : d.revision?.current.graph;
  const collection = original ? d.selection.original?.collection : d.revision?.current.collection;
  const waitingAuthor = !original && !graph && collection?.required_action === 'await_revision_author';
  const authorityHeld = d.activation.reconciliation_required || !!d.handoff?.current.reconciliation_required || !!d.revision?.current.reconciliation_required;
  const readable = !refused;
  const admitted = fresh && readable && !busy && journalReady && !conflict && !authorityHeld && !q?.reconciliation_required &&
    d.activation.current_eligibility.eligible && (!d.revision || d.revision.current.eligible);
  const available = admitted && collection?.validity !== 'held' && collection?.state !== 'held';
  // A frozen original return may recover its acknowledgement while its genuine successor waits.
  const canReplayReturn = admitted && (collection?.validity !== 'held' && collection?.state !== 'held' ||
    collection?.validity === 'held' && collection.reason === 'noncurrent_successor' && collection.required_action === 'await_revision_author');
  const locked = !!responseEnvelope || !!returnEnvelope;
  const canRespond = available && !locked && !d.history && !!q?.pending && !q.signed;
  const canReturn = available && !locked && !d.history && !d.selection.successor && d.selection.return.eligible && collection?.state === 'needs_revision' && collection.validity === 'current';
  let validFindings = true;
  try { exactBytes(draft); } catch { validFindings = false; }

  function mutationFailure(error: unknown, lane: 'response' | 'return') {
    if (!alive.current) return;
    const status = (error as { status?: number } | null)?.status;
    setDiagnostic(reviewDiagnostic(error));
    setOutcome(null); setReturnOpen(false);
    if (status === 403 || status === 404) {
      setRefused(true); setDraft(''); setResponseEnvelope(null); setReturnEnvelope(null);
      try { clearSelectionCommands(d.slug, d.activation.activation_id); } catch { /* Whole pane is still hidden. */ }
      setErrorKey('workflows.errorBody'); void refresh();
    } else if (status === 422 && lane === 'response' && (error as { code?: string }).code === 'workflow_human_review_invalid_request') {
      // This named route decoder rejects before admission; preserve findings for a new explicit confirmation.
      if (responseId) { try { clearCommand(responseId); } catch { setJournalReady(false); } }
      setResponseEnvelope(null); setErrorKey('workflows.findingsInvalid');
    } else if (status === 409 || status === 422) {
      // No silent repin/new key, even after a server rejection. A diagnostic remains explicit.
      setConflict(true); setErrorKey('workflows.conflict'); void refresh();
    } else setErrorKey(lane === 'response' ? 'workflows.uncertain' : 'workflows.uncertainReturn');
  }
  async function sendEnvelope(envelope: CommandEnvelope, selected: ReviewDetail) {
    const latest = await refresh();
    if (!alive.current || !latest || latest.history || latest.slug !== selected.slug || latest.activation.activation_id !== selected.activation.activation_id ||
        !latest.activation.current_eligibility.eligible || latest.revision && !latest.revision.current.eligible) return;
    if (latest.activation.reconciliation_required || latest.request?.reconciliation_required || latest.revision?.current.reconciliation_required || latest.handoff?.current.reconciliation_required) return;
    const stillCurrent = captureAdmission(latest);
    const body = await admittedCommand(envelope, latest);
    const col = latest.selection.successor ? latest.revision?.current.collection : latest.selection.original?.collection;
    const pendingReturnReplay = !('request_id' in body) && !!latest.selection.successor &&
      col?.validity === 'held' && col.reason === 'noncurrent_successor' && col.required_action === 'await_revision_author';
    // No await between this renewed selected-read admission and the writer. Keep the journal on drift.
    if (!alive.current || !stillCurrent() || (col?.validity === 'held' || col?.state === 'held') && !pendingReturnReplay) return;
    if ('request_id' in body) {
      const result = await human.mutateAsync({ slug: selected.slug, activationId: selected.activation.activation_id, body });
      await validateHumanResponse(selected, body, result);
    } else {
      const result = await owner.mutateAsync({ slug: selected.slug, activationId: selected.activation.activation_id, body });
      validateReturnResponse(body, result);
    }
    // Completion owns ONLY its captured tuple. A different selection gets no receipt/toast/refresh.
    clearCommand(envelope.identity);
    if (!alive.current) return;
    if ('request_id' in body) { setResponseEnvelope(null); setDraft(''); setOutcome(null); }
    else { setReturnEnvelope(null); setReturnOpen(false); }
    setErrorKey(null); setDiagnostic('');
    await refresh();
  }
  async function respond() {
    if (latch.current || !canRespond || !q || !outcome || !validFindings || !responseId) return;
    latch.current = true; setBusy(true); // Synchronous, before hashing.
    const selected = d, text = draft, chosen = outcome;
    let stored = false;
    try {
      const body = await humanCommand(q, chosen, text);
      if (!alive.current || !freshRef.current) return;
      const envelope = saveCommand(responseId, body); stored = true;
      setResponseEnvelope(envelope);
      await sendEnvelope(envelope, selected);
    } catch (error) { if (stored) mutationFailure(error, 'response'); else if (alive.current) { setErrorKey('workflows.storage'); setJournalReady(false); } }
    finally { latch.current = false; if (alive.current) setBusy(false); }
  }
  async function openReturn() {
    if (latch.current || !canReturn) return;
    latch.current = true; setBusy(true);
    try {
      const selected = await refresh();
      if (alive.current && selected && ownerIdentity(selected) === ownerId && selected.selection.return.eligible && !selected.selection.successor) setReturnOpen(true);
    } finally { latch.current = false; if (alive.current) setBusy(false); }
  }
  async function confirmReturn() {
    if (latch.current || !canReturn || !ownerId) return;
    latch.current = true; setBusy(true);
    let stored = false;
    try {
      const selected = await refresh();
      if (!alive.current || !selected || ownerIdentity(selected) !== ownerId || !selected.selection.return.eligible || selected.selection.successor) { if (alive.current) setReturnOpen(false); return; }
      const body = returnCommand(selected), envelope = saveCommand(ownerId, body); stored = true;
      setReturnEnvelope(envelope);
      await sendEnvelope(envelope, selected);
    } catch (error) { if (stored) mutationFailure(error, 'return'); else if (alive.current) { setErrorKey('workflows.storage'); setJournalReady(false); } }
    finally { latch.current = false; if (alive.current) setBusy(false); }
  }
  async function retry(envelope: CommandEnvelope, lane: 'response' | 'return') {
    if (latch.current || !(lane === 'return' ? canReplayReturn : available) || d.history) return;
    latch.current = true; setBusy(true);
    try { await sendEnvelope(envelope, d); } catch (error) { mutationFailure(error, lane); }
    finally { latch.current = false; if (alive.current) setBusy(false); }
  }
  const base = `/orgs/${d.slug}/reviews/${encodeURIComponent(d.activation.activation_id)}`;
  const stateKey: MessageKey = waitingAuthor ? 'workflows.waitingRevisionAuthor' : collection?.validity === 'held' ? 'workflows.held' : collection?.state === 'approved' && collection.validity === 'current' && collection.approved_use ? 'workflows.joined' :
    collection?.state === 'collecting' ? 'workflows.collecting' : collection?.state === 'ready_to_join' ? 'workflows.ready_to_join' :
    collection?.state === 'needs_revision' ? 'workflows.needs_revision' : 'workflows.held';
  if (!readable) return <div role="alert"><h2>{t('workflows.error')}</h2><p>{t('workflows.errorBody')}</p><pre className="whitespace-pre-wrap wrap-anywhere">{diagnostic}</pre></div>;
  return <div className="min-w-0 space-y-5">
    <header className="space-y-2 wrap-anywhere">
      <p className="text-fg-muted text-sm">{d.activation.template.identity_id} · {t('workflows.version', { version: d.activation.template.version })}</p>
      <h2 ref={heading} tabIndex={-1} className="font-display text-2xl">{q?.scope.output.description ?? t('workflows.detail')}</h2>
      {q && <p className="text-fg-muted text-sm">{t('workflows.revision', { revision: q.scope.revision })}</p>}
      <p className="text-fg-muted text-xs">{t('workflows.identity', { org: d.slug, instance: d.activation.instance_id, slot: 'output' })}</p>
      {q && <p className="text-fg-muted text-xs">{t('workflows.digest', { digest: q.scope.submission_digest })}</p>}
    </header>
    <nav className="border-border flex gap-5 border-b pb-3" aria-label={t('workflows.detail')}>
      <Link aria-current={!d.history ? 'page' : undefined} className={!d.history ? 'text-accent font-semibold' : ''} to={base}>{t('workflows.current')}</Link>
      <Link aria-current={d.history ? 'page' : undefined} className={d.history ? 'text-accent font-semibold' : ''} to={`${base}?view=history`}>{t('workflows.history')}</Link>
    </nav>
    <div aria-live="polite" className="space-y-3">
      {d.history && <p className="border-border inline-block rounded border px-3 py-2 text-sm">{t('workflows.readonly')}</p>}
      {q && !d.history && <p className="border-border inline-block rounded border px-3 py-2 text-sm">{t(q.signed ? q.receipt.outcome === 'approved' ? 'workflows.approvedReceipt' : 'workflows.changesReceipt' : 'workflows.pending')}</p>}
      {!d.history && collection && (!q || q.signed || collection.state !== 'collecting') && <p className="bg-bg-subtle border-border rounded border p-3 text-sm">{t(stateKey)}</p>}
      {collection?.reason && <pre className="text-fg-muted text-sm whitespace-pre-wrap wrap-anywhere">{collection.reason}{collection.required_action ? '\n' + collection.required_action : ''}</pre>}
      {d.revision && !d.history && <div className="space-y-2 text-sm"><p>{t('workflows.newAuthor', { id: d.revision.core.author_task_id })}</p><p>{t(`workflows.state.${d.revision.current.state}`)}</p>
        {d.revision.current.reason && <pre className="whitespace-pre-wrap wrap-anywhere">{d.revision.current.reason}</pre>}</div>}
      {errorKey && <div role="alert"><p>{t(errorKey)}</p><pre className="mt-2 whitespace-pre-wrap wrap-anywhere">{diagnostic}</pre></div>}
      {responseEnvelope && <div className="space-y-2"><p>{t('workflows.uncertain')}</p>{!d.history && <Button variant="outline" disabled={!available} onClick={() => void retry(responseEnvelope, 'response')}>{t('workflows.retryResponse')}</Button>}</div>}
      {returnEnvelope && <div className="space-y-2"><p>{t('workflows.uncertainReturn')}</p>{!d.history && <Button variant="outline" disabled={!canReplayReturn} onClick={() => void retry(returnEnvelope, 'return')}>{t('workflows.retryReturn')}</Button>}</div>}
    </div>
    {d.document !== null ? <pre aria-label={t('workflows.detail')} className="border-border bg-bg-raised min-w-0 rounded-lg border p-4 text-sm leading-relaxed whitespace-pre-wrap wrap-anywhere md:p-5">{d.document}</pre> :
      <div className="border-border rounded-lg border p-5"><p>{t(d.history ? 'workflows.noHistory' : graph ? 'workflows.noRequest' : 'workflows.authorPending')}</p></div>}
    {q?.signed && <section className="space-y-2"><p className="text-fg-muted text-xs wrap-anywhere">{t('workflows.receipt', { id: q.receipt.receipt_id })}</p><pre className="whitespace-pre-wrap wrap-anywhere">{d.findings}</pre></section>}
    {q && !d.history && !q.signed && <section className="space-y-3">
      <Label htmlFor="workflow-findings">{t('workflows.findings')}</Label>
      <Textarea id="workflow-findings" value={draft} onChange={e => setDraft(e.target.value)} placeholder={t('workflows.findingsHint')} disabled={!canRespond} className="min-h-28" />
      {draft && !validFindings && <p role="alert">{t('workflows.findingsInvalid')}</p>}
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        {q.scope.outcomes.includes('changes_requested') && <Button variant="outline" disabled={!canRespond || !validFindings} onClick={event => { responseTrigger.current = event.currentTarget; setOutcome('changes_requested'); }}>{t('workflows.changes')}</Button>}
        {q.scope.outcomes.includes('approved') && <Button disabled={!canRespond || !validFindings} onClick={event => { responseTrigger.current = event.currentTarget; setOutcome('approved'); }}>{t('workflows.sign')}</Button>}
      </div>
    </section>}
    {!d.history && d.selection.return.eligible && !d.selection.successor && <Button variant="outline" ref={ownerTrigger} disabled={!canReturn} onClick={() => void openReturn()}>{t('workflows.return')}</Button>}
    {!d.history && !d.selection.return.eligible && !d.selection.successor && collection?.state === 'needs_revision' && <pre className="text-fg-muted text-sm whitespace-pre-wrap wrap-anywhere">{d.selection.return.reason}{d.selection.return.required_action ? '\n' + d.selection.return.required_action : ''}</pre>}
    {!d.history && d.selection.successor && graph && collection?.validity === 'current' && collection.state === 'needs_revision' && <p>{t('workflows.unsupported')}</p>}
    <ReviewResponseDialog open={outcome !== null} outcome={outcome ?? 'approved'} revision={q?.scope.revision ?? 1} findings={draft} busy={busy} disabled={!canRespond} onClose={() => setOutcome(null)} onConfirm={() => void respond()} onReturnFocus={() => restoreFocus(responseTrigger.current)} />
    <OwnerReturnDialog open={returnOpen} busy={busy} disabled={!canReturn} onClose={() => setReturnOpen(false)} onConfirm={() => void confirmReturn()} onReturnFocus={() => restoreFocus(ownerTrigger.current)} />
  </div>;
}

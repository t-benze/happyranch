import { useIdentityPresentation } from '@/hooks/identities';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/design-system/primitives/Dialog';
import { ApiError } from '@/lib/api';
import {
  authorityPolicyActiveEpoch,
  type TeamEscalationPolicyResponse,
  type V2AuthorityPolicyControlResponse,
  type V2PairedControlRequest,
  useCreateTeamEscalationPolicyV2Release,
  useTeamEscalationPolicy,
  useTeamEscalationPolicyV2History,
} from '@/hooks/authorityPolicy';
import { useAgentsRoutes } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';
import { formatDateShapeFor } from '@/lib/i18n/format';
import { classifyAgentError, renderAgentError, type Translate } from './strings';

/** A locale-neutral status message, rendered through `t` on every render. */
interface PolicyMessage {
  key: MessageKey;
  params?: MessageParams;
}

interface V2Draft {
  policyId: string;
  title: string;
  whatToEscalate: string;
  whatNotToEscalate: string;
}

interface V2EditorState {
  draft: V2Draft;
  baseline: string;
  basedOnSelectorId: string | null;
  action: 'bootstrap' | 'activate';
  sourceKey: string;
}

export function TeamEscalationPolicyEntryCard({ agent }: { agent: { name: string; team: string; role: string } }): JSX.Element {
  const presentation = useIdentityPresentation();
  const query = useTeamEscalationPolicy(agent);
  const routes = useAgentsRoutes();
  const { t } = useTranslation();
  return (
    <PolicyShell>
      <h3 className="font-display text-text-primary text-base font-medium">{t('agents.policy.entryTitle')}</h3>
      <p className="text-text-muted mt-1 text-xs">{t('agents.policy.entryMeta', { team: agent.team, name: presentation.label(agent.name) })}</p>
      {query.isLoading ? <p className="text-text-muted mt-3 text-xs">{t('agents.policy.statusLoading')}</p> : query.isError || !query.data ? <p role="alert" className="text-tier-red mt-3 text-xs">{renderAgentError(classifyAgentError(query.error, 'agents.policy.statusError'), t)}</p> : query.data.family === 'empty' ? <p className="text-text-muted mt-3 text-xs">{t('agents.policy.noActive')}</p> : <p className="text-text-muted mt-3 text-xs">{t(query.data.family === 'v2' ? 'agents.policy.activeV2' : 'agents.policy.activeLegacy', { version: query.data.active.release.version, epoch: authorityPolicyActiveEpoch(query.data.active), digest: query.data.active.release.digest.slice(0, 12) })}</p>}
      <Button asChild size="sm" className="mt-3"><Link to={routes.policy(agent.name)}>{t('agents.policy.open')}</Link></Button>
    </PolicyShell>
  );
}

export function TeamEscalationPolicyCard({
  agent,
  onDirtyChange,
}: {
  agent: { name: string; team: string; role: string };
  onDirtyChange?: (dirty: boolean) => void;
}): JSX.Element {
  const query = useTeamEscalationPolicy(agent);
  const { t, render } = useTranslation();
  const createV2Release = useCreateTeamEscalationPolicyV2Release();
  const v2History = useTeamEscalationPolicyV2History(agent);
  const [editor, setEditor] = useState<V2EditorState | null>(null);
  const [message, setMessage] = useState<PolicyMessage | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reviewingConflict, setReviewingConflict] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [retryRequest, setRetryRequest] = useState<V2PairedControlRequest | null>(null);
  const submittingRef = useRef(false);

  const data = query.data;
  useEffect(() => {
    if (!data) return;
    const next = editorFromProjection(data);
    setEditor((current) => {
      if (!current) return next;
      if (current.sourceKey === next.sourceKey) return current;
      if (draftKey(current.draft) !== current.baseline) return current;
      return next;
    });
  }, [data]);

  const dirty = editor ? draftKey(editor.draft) !== editor.baseline : false;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = true;
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (query.isLoading) return <PolicyShell><p className="text-text-muted text-sm">{t('agents.policy.loading')}</p></PolicyShell>;
  if (query.isError || !data) {
    return <PolicyShell><div role="alert" className="text-tier-red flex flex-wrap items-center gap-2 text-sm"><AlertCircle size={14} /><span>{renderAgentError(classifyAgentError(query.error, 'agents.policy.loadError'), t)}</span><Button size="sm" variant="ghost" onClick={() => void query.refetch()}>{t('common.retry')}</Button></div></PolicyShell>;
  }
  if (!editor) {
    return <PolicyShell><p className="text-text-muted text-sm">{t('agents.policy.preparing')}</p></PolicyShell>;
  }

  const validationError = validateV2Draft(editor.draft);
  const v2HistoryItems = v2History.data?.pages.flatMap((page) => page.items) ?? [];
  const pending = submitting || createV2Release.isPending;
  const canSave = data.family === 'empty' || dirty;

  const updateText = (field: 'whatToEscalate' | 'whatNotToEscalate', value: string) => {
    setEditor((current) => current ? { ...current, draft: { ...current.draft, [field]: value } } : current);
    setRetryRequest(null);
    setConflict(false);
    setMessage(null);
  };

  const buildRequest = (): V2PairedControlRequest => ({
    team: data.team,
    policy_id: editor.draft.policyId,
    title: editor.draft.title,
    create_request_id: crypto.randomUUID(),
    activation_request_id: crypto.randomUUID(),
    based_on_selector_id: editor.basedOnSelectorId,
    expected_selector_id: editor.basedOnSelectorId,
    action: editor.action,
    what_to_escalate: editor.draft.whatToEscalate,
    what_not_to_escalate: editor.draft.whatNotToEscalate,
    acknowledge_shared_credential_attribution: true,
  });

  const submit = async (exactRequest?: V2PairedControlRequest) => {
    if (submittingRef.current || validationError) return;
    const request = exactRequest ?? buildRequest();
    submittingRef.current = true;
    setSubmitting(true);
    setConfirm(false);
    setConflict(false);
    setMessage({ key: 'agents.policy.msg.saving' });
    try {
      const control = await createV2Release.mutateAsync({ agentName: agent.name, body: request });
      if (!receiptMatchesRequest(control, request)) {
        setRetryRequest(request);
        setMessage({ key: 'agents.policy.msg.receiptMismatch' });
        return;
      }
      const refetched = await query.refetch();
      const readback = refetchedData(refetched);
      if (!readback || !readbackMatches(control, request, readback)) {
        setRetryRequest(request);
        setMessage({ key: 'agents.policy.msg.readbackMismatch' });
        return;
      }
      setEditor(editorFromProjection(readback));
      setRetryRequest(null);
      setMessage({
        key: 'agents.policy.msg.saved',
        params: {
          release: control.receipt.release_id,
          activation: control.receipt.activation_id,
          selector: control.receipt.selector_id,
          digest: control.receipt.policy_digest,
        },
      });
    } catch (error) {
      const api = error instanceof ApiError ? error : null;
      if (api?.status === 409) {
        setRetryRequest(null);
        setConflict(true);
        setMessage({ key: 'agents.policy.msg.conflict' });
      } else if (api?.status === 422) {
        setRetryRequest(null);
        setMessage({ key: 'agents.policy.msg.rejected' });
      } else {
        setRetryRequest(request);
        setMessage({ key: 'agents.policy.msg.unknown' });
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const reviewCurrentSelector = async () => {
    if (reviewingConflict) return;
    setReviewingConflict(true);
    try {
      const result = await query.refetch();
      const current = refetchedData(result);
      if (!current) {
        setMessage({ key: 'agents.policy.msg.selectorLoadFailed' });
        return;
      }
      const source = editorFromProjection(current);
      setEditor((prior) => prior ? {
        ...prior,
        draft: {
          ...prior.draft,
          policyId: source.draft.policyId,
          title: source.draft.title,
        },
        basedOnSelectorId: source.basedOnSelectorId,
        action: source.action,
        sourceKey: source.sourceKey,
      } : source);
      setConflict(false);
      setMessage({ key: 'agents.policy.msg.selectorLoaded' });
    } catch {
      setMessage({ key: 'agents.policy.msg.selectorLoadFailed' });
    } finally {
      setReviewingConflict(false);
    }
  };

  return (
    <PolicyShell>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-text-primary text-base font-medium">{t('agents.policy.title')}</h3>
          <p className="text-text-muted mt-1 text-xs">{t('agents.policy.ownedBy', { team: data.team })}</p>
        </div>
        <span className="bg-accent-soft text-accent-text rounded-full px-2 py-1 text-xs">{t('agents.policy.teamOwned')}</span>
      </div>
      <div className="bg-tier-amber-soft text-text-secondary mt-3 rounded-md p-3 text-xs">
        {render('agents.policy.attribution', { credential: <strong>{t('agents.policy.sharedCredential')}</strong> })}
      </div>

      {data.family === 'empty' ? (
        <p className="text-text-muted mt-3 text-xs">{t('agents.policy.emptySelector')}</p>
      ) : data.family === 'legacy_v1' ? (
        <p className="text-text-muted mt-3 text-xs">{t('agents.policy.legacySelector', { version: data.active.release.version, activation: data.active.activation_id, epoch: data.selector_epoch, selector: data.selector_id })}</p>
      ) : (
        <p className="text-text-muted mt-3 break-all text-xs">{t('agents.policy.v2Selector', { version: data.active.release.version, release: data.active.release.id, activation: data.active.activation_id, epoch: data.selector_epoch, selector: data.selector_id, digest: data.active.release.digest })}</p>
      )}

      <dl className="bg-surface-sunken mt-4 rounded-md p-3 text-xs">
        <div><dt className="text-text-muted inline">{t('agents.policy.titleLabel')} </dt><dd className="text-text-primary inline">{editor.draft.title}</dd></div>
        <div className="mt-1"><dt className="text-text-muted inline">{t('agents.policy.policyLabel')} </dt><dd className="text-text-primary inline font-mono">{editor.draft.policyId}</dd></div>
      </dl>

      <label className="text-text-secondary mt-4 block text-xs font-medium">{t('agents.policy.whatTo')}
        <textarea className="border-border-subtle bg-surface mt-1 min-h-32 w-full rounded-md border px-3 py-2 font-mono text-xs" value={editor.draft.whatToEscalate} onChange={(event) => updateText('whatToEscalate', event.target.value)} />
      </label>
      <label className="text-text-secondary mt-3 block text-xs font-medium">{t('agents.policy.whatNot')}
        <textarea className="border-border-subtle bg-surface mt-1 min-h-32 w-full rounded-md border px-3 py-2 font-mono text-xs" value={editor.draft.whatNotToEscalate} onChange={(event) => updateText('whatNotToEscalate', event.target.value)} />
      </label>

      {(validationError || message) && <p role="status" className={`mt-3 break-all text-xs ${validationError ? 'text-tier-red' : 'text-text-secondary'}`}>{validationError ? renderValidation(validationError, t) : message ? t(message.key, message.params) : null}</p>}
      <Dialog open={confirm} onOpenChange={(open) => { if (!pending) setConfirm(open); }}>
        <DialogContent aria-label={t('agents.policy.confirmAria')} closeLabel={t('common.close')}>
          <DialogHeader>
            <DialogTitle>{t('agents.policy.confirmTitle')}</DialogTitle>
            <DialogDescription>{t('agents.policy.confirmBody')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" disabled={pending} onClick={() => void submit()}>{t('agents.policy.confirmSave')}</Button>
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>{t('common.cancel')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" disabled={!canSave || !!validationError || pending} onClick={() => setConfirm(true)}>{pending ? t('agents.policy.saving') : t('agents.policy.save')}</Button>
        {retryRequest && <Button size="sm" variant="ghost" disabled={pending} onClick={() => void submit(retryRequest)}>{t('agents.policy.retryExact')}</Button>}
        {conflict && <Button size="sm" variant="ghost" disabled={reviewingConflict} onClick={() => void reviewCurrentSelector()}>{reviewingConflict ? t('agents.policy.loadingSelector') : t('agents.policy.reviewSelector')}</Button>}
      </div>

      <V2HistorySection history={v2History} items={v2HistoryItems} />
    </PolicyShell>
  );
}

function V2HistorySection({ history, items }: {
  history: ReturnType<typeof useTeamEscalationPolicyV2History>;
  items: NonNullable<ReturnType<typeof useTeamEscalationPolicyV2History>['data']>['pages'][number]['items'];
}): JSX.Element {
  const { t, locale } = useTranslation();
  return (
    <section aria-labelledby="v2-policy-history-heading" className="border-border-subtle mt-5 border-t pt-4">
      <h4 id="v2-policy-history-heading" className="text-text-primary text-sm font-medium">{t('agents.policy.history.title')}</h4>
      {history.isLoading ? <p className="text-text-muted mt-2 text-xs">{t('agents.policy.history.loading')}</p> : history.isError && !items.length ? <div className="mt-2 flex items-center gap-2"><p role="alert" className="text-tier-red text-xs">{renderAgentError(classifyAgentError(history.error, 'agents.policy.history.error'), t)}</p><Button size="sm" variant="ghost" onClick={() => void history.refetch()}>{t('agents.policy.history.retry')}</Button></div> : !items.length ? <p className="text-text-muted mt-2 text-xs">{t('agents.policy.history.empty')}</p> : <ul className="mt-2 space-y-3">{items.map((item) => <li key={`${item.release_id}-${item.activation?.id ?? 'inactive'}`} className="bg-surface-sunken rounded p-3 text-xs">
        <div className="font-medium">v{item.version} · {item.title}</div>
        <div className="text-text-muted mt-1 break-all font-mono">{t('agents.policy.history.release', { release: item.release_id, policyDigest: item.policy_digest, contract: item.contract_digest, created: formatDateShapeFor(locale, new Date(item.release_created_at), 'dateTime') })}</div>
        <div className="text-text-muted mt-1 break-all font-mono">{item.activation ? t('agents.policy.history.activation', { activation: item.activation.id, epoch: item.activation.selector_epoch, action: item.activation.action, digest: item.activation.digest, created: formatDateShapeFor(locale, new Date(item.activation.created_at), 'dateTime') }) : t('agents.policy.history.neverActivated')}</div>
        <div className="mt-2"><span className="font-medium">{t('agents.policy.whatTo')}</span><p className="mt-1 whitespace-pre-wrap font-mono">{item.what_to_escalate}</p></div>
        <div className="mt-2"><span className="font-medium">{t('agents.policy.whatNot')}</span><p className="mt-1 whitespace-pre-wrap font-mono">{item.what_not_to_escalate}</p></div>
        <div className="text-text-muted mt-2">{item.actor_attribution}</div>
      </li>)}</ul>}
      {history.isError && items.length > 0 && <p role="alert" className="text-tier-red mt-2 text-xs">{t('agents.policy.history.moreError')}</p>}
      {!history.isLoading && items.length > 0 && <Button size="sm" variant="ghost" disabled={!history.hasNextPage || history.isFetchingNextPage} aria-label={history.isError ? t('agents.policy.history.retryMore') : t('agents.policy.history.loadMore')} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? t('agents.policy.history.loadingMore') : history.isError ? t('agents.policy.history.retryMore') : history.hasNextPage ? t('agents.policy.history.loadMore') : t('agents.policy.history.end')}</Button>}
    </section>
  );
}

function draftKey(draft: V2Draft): string {
  return JSON.stringify(draft);
}

function editorFromProjection(data: TeamEscalationPolicyResponse): V2EditorState {
  const draft: V2Draft = data.family === 'v2' ? {
    policyId: data.active.release.policy_id,
    title: data.active.release.title,
    whatToEscalate: data.active.release.what_to_escalate,
    whatNotToEscalate: data.active.release.what_not_to_escalate,
  } : {
    policyId: data.v2_starter.policy_id,
    title: data.v2_starter.title,
    whatToEscalate: data.v2_starter.what_to_escalate,
    whatNotToEscalate: data.v2_starter.what_not_to_escalate,
  };
  return {
    draft,
    baseline: draftKey(draft),
    basedOnSelectorId: data.family === 'empty' ? null : data.selector_id,
    action: data.family === 'empty' ? 'bootstrap' : 'activate',
    sourceKey: data.family === 'v2'
      ? `${data.family}:${data.selector_id}:${data.active.release.id}:${data.active.release.digest}`
      : `${data.team}:${data.family}:${data.selector_id}:${data.selector_epoch}:${data.v2_starter.policy_id}`,
  };
}

const V2_TEXT_MAX = 20_000;

/** A locale-neutral validation failure; `label` names the offending field. */
interface V2ValidationError {
  key: 'agents.policy.validation.required' | 'agents.policy.validation.unsupported' | 'agents.policy.validation.tooLong';
  label: 'agents.policy.whatTo' | 'agents.policy.whatNot';
}

function validateV2Draft(draft: V2Draft): V2ValidationError | null {
  return validateV2Text('agents.policy.whatTo', draft.whatToEscalate)
    ?? validateV2Text('agents.policy.whatNot', draft.whatNotToEscalate);
}

function validateV2Text(label: V2ValidationError['label'], value: string): V2ValidationError | null {
  if (!value.length || !value.trim().length) return { key: 'agents.policy.validation.required', label };
  if (value.includes('\0') || hasUnpairedSurrogate(value)) return { key: 'agents.policy.validation.unsupported', label };
  if ([...value].length > V2_TEXT_MAX) return { key: 'agents.policy.validation.tooLong', label };
  return null;
}

function renderValidation(error: V2ValidationError, t: Translate): string {
  return t(error.key, { label: t(error.label), count: V2_TEXT_MAX });
}

function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function refetchedData(value: unknown): TeamEscalationPolicyResponse | undefined {
  if (typeof value !== 'object' || value === null || !('data' in value)) return undefined;
  return (value as { data?: TeamEscalationPolicyResponse }).data;
}

function receiptMatchesRequest(control: V2AuthorityPolicyControlResponse, request: V2PairedControlRequest): boolean {
  const receipt = control.receipt;
  return control.control === 'v2_create_activate'
    && receipt.kind === control.control
    && receipt.create_request_id === request.create_request_id
    && receipt.activation_request_id === request.activation_request_id
    && receipt.action === request.action
    && receipt.selector_id === control.selector_id
    && receipt.selector_epoch === control.selector_epoch
    && receipt.previous_selector_id === control.previous_selector_id;
}

function readbackMatches(
  control: V2AuthorityPolicyControlResponse,
  request: V2PairedControlRequest,
  readback: TeamEscalationPolicyResponse,
): boolean {
  const receipt = control.receipt;
  return readback.family === 'v2'
    && readback.selector_id === receipt.selector_id
    && readback.selector_epoch === receipt.selector_epoch
    && readback.active.selector_epoch === receipt.selector_epoch
    && readback.active.activation_id === receipt.activation_id
    && readback.active.release.id === receipt.release_id
    && readback.active.release.version === receipt.release_version
    && readback.active.release.digest === receipt.policy_digest
    && readback.active.release.policy_id === request.policy_id
    && readback.active.release.title === request.title
    && readback.active.release.what_to_escalate === request.what_to_escalate
    && readback.active.release.what_not_to_escalate === request.what_not_to_escalate;
}

function PolicyShell({ children }: { children: ReactNode }): JSX.Element {
  return <section data-testid="team-escalation-policy" className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">{children}</section>;
}

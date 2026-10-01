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
  const query = useTeamEscalationPolicy(agent);
  const routes = useAgentsRoutes();
  return (
    <PolicyShell>
      <h3 className="font-display text-text-primary text-base font-medium">Manager-only team escalation policy</h3>
      <p className="text-text-muted mt-1 text-xs">{formatTeam(agent.team)} manager · {agent.name}</p>
      {query.isLoading ? <p className="text-text-muted mt-3 text-xs">Loading active policy status…</p> : query.isError || !query.data ? <p role="alert" className="text-tier-red mt-3 text-xs">Could not load policy status.</p> : query.data.family === 'empty' ? <p className="text-text-muted mt-3 text-xs">No active release. Canonical dual-text bootstrap is available.</p> : <p className="text-text-muted mt-3 text-xs">Active {query.data.family === 'v2' ? 'dual-text ' : 'legacy '}v{query.data.active.release.version} · epoch {authorityPolicyActiveEpoch(query.data.active)} · {query.data.active.release.digest.slice(0, 12)}</p>}
      <Button asChild size="sm" className="mt-3"><Link to={routes.policy(agent.name)}>Open team escalation policy</Link></Button>
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
  const createV2Release = useCreateTeamEscalationPolicyV2Release();
  const v2History = useTeamEscalationPolicyV2History(agent);
  const [editor, setEditor] = useState<V2EditorState | null>(null);
  const [message, setMessage] = useState<string | null>(null);
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

  if (query.isLoading) return <PolicyShell><p className="text-text-muted text-sm">Loading team policy…</p></PolicyShell>;
  if (query.isError || !data) {
    return <PolicyShell><div role="alert" className="text-tier-red flex flex-wrap items-center gap-2 text-sm"><AlertCircle size={14} /><span>Could not load the team policy.</span><Button size="sm" variant="ghost" onClick={() => void query.refetch()}>Retry</Button></div></PolicyShell>;
  }
  if (!editor) {
    return <PolicyShell><p className="text-text-muted text-sm">Preparing dual-text editor…</p></PolicyShell>;
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
    setMessage('Saving and verifying the paired policy…');
    try {
      const control = await createV2Release.mutateAsync({ agentName: agent.name, body: request });
      if (!receiptMatchesRequest(control, request)) {
        setRetryRequest(request);
        setMessage('The server receipt did not match this paired request. No local success was recorded; retry or reload after checking the server.');
        return;
      }
      const refetched = await query.refetch();
      const readback = refetchedData(refetched);
      if (!readback || !readbackMatches(control, request, readback)) {
        setRetryRequest(request);
        setMessage('The paired receipt was returned, but authoritative readback did not match it. No local success was recorded; retry the exact request or reload.');
        return;
      }
      setEditor(editorFromProjection(readback));
      setRetryRequest(null);
      setMessage(`Saved and activated immutable v2 release ${control.receipt.release_id}; activation ${control.receipt.activation_id}; selector ${control.receipt.selector_id}; digest ${control.receipt.policy_digest}.`);
    } catch (error) {
      const api = error instanceof ApiError ? error : null;
      if (api?.status === 409) {
        setRetryRequest(null);
        setConflict(true);
        setMessage('The active selector changed or the request conflicted. Your paired draft was preserved. Review the current selector before choosing whether to save again.');
      } else if (api?.status === 422) {
        setRetryRequest(null);
        setMessage('The server rejected the paired policy contract. Both draft texts were preserved; review the required values and bounds.');
      } else {
        setRetryRequest(request);
        setMessage('The save result is unknown. No local success was recorded; retry the exact paired request to use its idempotency receipt.');
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
        setMessage('The current selector could not be loaded. Your paired draft was preserved.');
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
      setMessage('The authoritative current selector was loaded. Your draft is preserved; review both texts before saving again.');
    } catch {
      setMessage('The current selector could not be loaded. Your paired draft was preserved.');
    } finally {
      setReviewingConflict(false);
    }
  };

  return (
    <PolicyShell>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-text-primary text-base font-medium">Team escalation policy</h3>
          <p className="text-text-muted mt-1 text-xs">Owned by the {formatTeam(data.team)} team, not by this agent.</p>
        </div>
        <span className="bg-accent-soft text-accent-text rounded-full px-2 py-1 text-xs">Team-owned</span>
      </div>
      <div className="bg-tier-amber-soft text-text-secondary mt-3 rounded-md p-3 text-xs">
        Changes are attributed only to the <strong>shared local operator credential</strong>. Individual operator identity is not available.
      </div>

      {data.family === 'empty' ? (
        <p className="text-text-muted mt-3 text-xs">No active release. This genuinely empty selector starts from the approved dual-text policy.</p>
      ) : data.family === 'legacy_v1' ? (
        <p className="text-text-muted mt-3 text-xs">Active legacy v{data.active.release.version} · legacy activation {data.active.activation_id} · selector epoch {data.selector_epoch} · selector {data.selector_id}. Saving replaces the active selector with one atomic dual-text v2 release; legacy history remains read-only.</p>
      ) : (
        <p className="text-text-muted mt-3 break-all text-xs">Active release v{data.active.release.version} · release {data.active.release.id} · activation {data.active.activation_id} · selector epoch {data.selector_epoch} · selector {data.selector_id} · digest {data.active.release.digest}.</p>
      )}

      <dl className="bg-surface-sunken mt-4 rounded-md p-3 text-xs">
        <div><dt className="text-text-muted inline">Title: </dt><dd className="text-text-primary inline">{editor.draft.title}</dd></div>
        <div className="mt-1"><dt className="text-text-muted inline">Policy: </dt><dd className="text-text-primary inline font-mono">{editor.draft.policyId}</dd></div>
      </dl>

      <label className="text-text-secondary mt-4 block text-xs font-medium">What to escalate
        <textarea className="border-border-subtle bg-surface mt-1 min-h-32 w-full rounded-md border px-3 py-2 font-mono text-xs" value={editor.draft.whatToEscalate} onChange={(event) => updateText('whatToEscalate', event.target.value)} />
      </label>
      <label className="text-text-secondary mt-3 block text-xs font-medium">What not to escalate
        <textarea className="border-border-subtle bg-surface mt-1 min-h-32 w-full rounded-md border px-3 py-2 font-mono text-xs" value={editor.draft.whatNotToEscalate} onChange={(event) => updateText('whatNotToEscalate', event.target.value)} />
      </label>

      {(validationError || message) && <p role="status" className={`mt-3 break-all text-xs ${validationError ? 'text-tier-red' : 'text-text-secondary'}`}>{validationError ?? message}</p>}
      <Dialog open={confirm} onOpenChange={(open) => { if (!pending) setConfirm(open); }}>
        <DialogContent aria-label="activate dual-text policy confirmation">
          <DialogHeader>
            <DialogTitle>Save and activate both policy texts?</DialogTitle>
            <DialogDescription>One atomic control request creates one immutable release and selects it. Both texts change together or neither changes.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" disabled={pending} onClick={() => void submit()}>Confirm save &amp; activate</Button>
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>Cancel</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" disabled={!canSave || !!validationError || pending} onClick={() => setConfirm(true)}>{pending ? 'Saving & activating…' : 'Save & activate'}</Button>
        {retryRequest && <Button size="sm" variant="ghost" disabled={pending} onClick={() => void submit(retryRequest)}>Retry exact save &amp; activate</Button>}
        {conflict && <Button size="sm" variant="ghost" disabled={reviewingConflict} onClick={() => void reviewCurrentSelector()}>{reviewingConflict ? 'Loading current selector…' : 'Review current selector'}</Button>}
      </div>

      <V2HistorySection history={v2History} items={v2HistoryItems} />
    </PolicyShell>
  );
}

function V2HistorySection({ history, items }: {
  history: ReturnType<typeof useTeamEscalationPolicyV2History>;
  items: NonNullable<ReturnType<typeof useTeamEscalationPolicyV2History>['data']>['pages'][number]['items'];
}): JSX.Element {
  return (
    <section aria-labelledby="v2-policy-history-heading" className="border-border-subtle mt-5 border-t pt-4">
      <h4 id="v2-policy-history-heading" className="text-text-primary text-sm font-medium">Immutable dual-text history</h4>
      {history.isLoading ? <p className="text-text-muted mt-2 text-xs">Loading dual-text history…</p> : history.isError && !items.length ? <div className="mt-2 flex items-center gap-2"><p role="alert" className="text-tier-red text-xs">Could not load dual-text history.</p><Button size="sm" variant="ghost" onClick={() => void history.refetch()}>Retry dual-text history</Button></div> : !items.length ? <p className="text-text-muted mt-2 text-xs">No immutable dual-text releases yet.</p> : <ul className="mt-2 space-y-3">{items.map((item) => <li key={`${item.release_id}-${item.activation?.id ?? 'inactive'}`} className="bg-surface-sunken rounded p-3 text-xs">
        <div className="font-medium">v{item.version} · {item.title}</div>
        <div className="text-text-muted mt-1 break-all font-mono">release {item.release_id} · policy digest {item.policy_digest} · contract {item.contract_digest} · created {item.release_created_at}</div>
        <div className="text-text-muted mt-1 break-all font-mono">{item.activation ? `activation ${item.activation.id} · selector epoch ${item.activation.selector_epoch} · ${item.activation.action} · activation digest ${item.activation.digest} · activated ${item.activation.created_at}` : 'never activated'}</div>
        <div className="mt-2"><span className="font-medium">What to escalate</span><p className="mt-1 whitespace-pre-wrap font-mono">{item.what_to_escalate}</p></div>
        <div className="mt-2"><span className="font-medium">What not to escalate</span><p className="mt-1 whitespace-pre-wrap font-mono">{item.what_not_to_escalate}</p></div>
        <div className="text-text-muted mt-2">{item.actor_attribution}</div>
      </li>)}</ul>}
      {history.isError && items.length > 0 && <p role="alert" className="text-tier-red mt-2 text-xs">Could not load more dual-text history. Loaded releases are preserved.</p>}
      {!history.isLoading && items.length > 0 && <Button size="sm" variant="ghost" disabled={!history.hasNextPage || history.isFetchingNextPage} aria-label={history.isError ? 'Retry loading dual-text history' : 'Load more dual-text history'} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? 'Loading more dual-text history…' : history.isError ? 'Retry loading dual-text history' : history.hasNextPage ? 'Load more dual-text history' : 'End of dual-text history'}</Button>}
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

function formatTeam(team: string): string {
  return team.split(/[_-]+/).filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function validateV2Draft(draft: V2Draft): string | null {
  return validateV2Text('What to escalate', draft.whatToEscalate)
    ?? validateV2Text('What not to escalate', draft.whatNotToEscalate);
}

function validateV2Text(label: string, value: string): string | null {
  if (!value.length || !value.trim().length) return `${label} is required.`;
  if (value.includes('\0') || hasUnpairedSurrogate(value)) return `${label} contains an unsupported character.`;
  if ([...value].length > 20_000) return `${label} must be at most 20000 characters.`;
  return null;
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

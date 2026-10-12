import { beforeEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router-dom';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { I18nProvider, useI18n } from '@/hooks/i18n';
import { WorkflowReviewsPage } from './WorkflowReviewsPage';
import { base64, exactBytes, sha256 } from '@/lib/workflowReviewSelection';
import type { AuthorHandoffReceipt, WorkflowActivationReceipt, OwnerReviewSelection, WorkflowReviewRequestReceipt, HumanReviewCommand } from '@/lib/api/types';
const transport = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/lib/api/client', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api/client')>(), request: transport.request }));
function fixtureError(status: number, code: string, detail: unknown) { return Object.assign(new Error(code), { status, code, detail }); }
let activation: WorkflowActivationReceipt, selection: OwnerReviewSelection, handoff: AuthorHandoffReceipt, request: WorkflowReviewRequestReceipt;
const documentText = '# Generic 输出\n\n<script>never execute</script>\nCafe\u0301\r\n';
function Controls() { const { setLocale } = useI18n(), go = useNavigate(); return <><button onClick={() => void setLocale('zh-CN')}>locale</button><button onClick={() => go('/orgs/other/reviews/activation-other')}>other</button></>; }
async function setup(n: 1 | 2 = 2) {
  const digest = await sha256(exactBytes(documentText));
  activation = { format: 'workflow-activation-receipt@3', activation_id: 'activation-a', instance_id: 'instance-a', instance_reference: 'instance-a', activation_revision: 1,
    root_task_id: 'TASK-100', intent_id: 'intent-a', template: { identity_id: n === 1 ? 'product-design' : 'different-proposal', version: 1, definition_digest: 'a'.repeat(64), version_id: 'version-a', compiler_pin: 'compiler', validator_pin: 'validator', source_pin: 'source' },
    authority: { namespace: 'org/demo', generation: 1, snapshot_digest: 'b'.repeat(64) }, bindings: { writer: { kind: 'agent', principal: 'writer', team: 'eng' }, approver: { kind: 'human', principal: 'founder', team: null } }, eligible_replacements: {},
    allowed_actions: ['collect-review','return-to-author'], scope_digest: 'c'.repeat(64), context_digest: 'd'.repeat(64), activated_by: { principal_kind: 'human', principal_id: 'founder', proof_kind: 'founder_bearer' }, created_at: 'now', original_request_digest: 'e'.repeat(64), replayed: false, state: 'completed', execution_started: true, pending: false, reconciliation_required: false, cancellation_requested: false, current_eligibility: { eligible: true, blockers: [] }, responsible_owner: 'human:founder' };
  if (n === 1) activation.bindings = { 'product-lead': { kind: 'agent', principal: 'product_lead', team: 'eng' }, founder: { kind: 'human', principal: 'founder', team: null }, implementer: { kind: 'agent', principal: 'dev_agent', team: 'eng' }, tester: { kind: 'agent', principal: 'qa_engineer', team: 'eng' } };
  handoff = { format: 'workflow-author-handoff-receipt@1', core: { handoff_id: 'handoff-a', reserved_submission_id: 'submission-a', instance_id: 'instance-a', revision: 1, candidate_digest: digest, request_digest: 'f'.repeat(64), created_at: 'now' }, replayed: false,
    current: { state: 'consumed', pending: false, reconciliation_required: false, reason: 'consumed', graph: { submission_id: 'submission-a', round_id: 'round-a', request_ids: ['request-a'], reviewer_task_ids: [] } } };
  if (n === 1) { handoff.current.graph!.request_ids = ['request-a','request-peer-a','request-peer-b']; handoff.current.graph!.reviewer_task_ids = ['TASK-101','TASK-102']; }
  selection = { format: 'workflow-owner-review-selection@1', activation_id: 'activation-a', instance_id: 'instance-a', original: { handoff_id: 'handoff-a', human_request_id: 'request-a', collection: { state: 'collecting', approved_use: false, validity: 'current', reason: null, owner_principal: 'human:founder', required_action: null } }, successor: null, return: { eligible: false, reason: 'pending', required_action: null } };
  const scope = { format: `workflow-review-request@${n}`, org_slug: 'demo', instance_id: 'instance-a', handoff_id: 'handoff-a', submission_id: 'submission-a', submission_digest: digest, revision: 1, round_id: 'round-a', request_id: 'request-a', role: n === 1 ? 'founder' : 'approver', kind: 'human', principal: 'founder', team: null, assignment_generation: 1, template: activation.template, activation: { id: 'activation-a', revision: 1, request_digest: activation.original_request_digest }, authorization: 'authorization-a', binding_snapshot_id: 'binding-a', context_id: 'context-a', output: { primitive: n === 1 ? 'immutable-document-revision' : 'immutable-artifact-revision', description: n === 1 ? 'Product requirements' : 'Feasibility assessment', ...(n === 2 ? { accepted_kinds: ['document'] } : {}) }, outcomes: ['approved','changes_requested'], purpose: 'review', allowed_actions: ['collect-review'], inputs: [], ...(n === 2 ? { artifact: { identity: { org_namespace: 'demo', instance_id: 'instance-a', output_slot: 'output' }, submission_id: 'submission-a', revision: 1, kind: 'document', bytes_digest: digest, subject: { content_digest: digest } }, repository_resolution: null } : {}) };
  request = { format: `workflow-review-request-receipt@${n}`, request_id: 'request-a', scope, request_scope_digest: '1'.repeat(64), submission_bytes_base64: base64(exactBytes(documentText)), reconciliation_required: false, status: 'pending', signed: false, pending: true, receipt: null } as unknown as WorkflowReviewRequestReceipt;
  transport.request.mockImplementation(async (path: string, opts?: { body?: HumanReviewCommand }) => {
    if (path.endsWith('/human-receipts')) {
      const b = opts!.body!;
      const receipt = { format: `workflow-review-receipt@${n}`, receipt_id: 'receipt-a', request_id: 'request-a', submission_id: 'submission-a', submission_digest: digest, assignment_generation: 1, request_scope_digest: request.request_scope_digest, outcome: b.outcome, findings_bytes_base64: b.findings_bytes_base64, findings_digest: b.findings_digest, created_at: 'now' };
      request = { ...request, status: b.outcome, signed: true, pending: false, receipt } as unknown as WorkflowReviewRequestReceipt;
      return { format: `workflow-human-review-receipt@${n}`, receipt };
    }
    if (path.endsWith('/review-selection')) return structuredClone(selection);
    if (path.endsWith('/handoffs/handoff-a')) return structuredClone(handoff);
    if (path.endsWith('/requests/request-a')) return structuredClone(request);
    if (path.endsWith('/activations/activation-a')) return structuredClone(activation);
    if (path.endsWith('/activations')) return [structuredClone(activation)];
    throw fixtureError(404, 'not_found', { code: 'not_found' });
  });
}
function mount(path = '/orgs/demo/reviews/activation-a') {
  return render(<MemoryRouter initialEntries={[path]}><I18nProvider mode="full" initialResolution={{ locale: 'en', source: 'saved' }}><AppProvider client={makeQueryClient()}><Routes><Route path="/orgs/:slug/reviews/:activationId" element={<><WorkflowReviewsPage /><Controls /></>} /></Routes></AppProvider></I18nProvider></MemoryRouter>);
}
beforeEach(async () => { cleanup(); sessionStorage.clear(); transport.request.mockReset(); Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true }); await setup(); });
async function ready() { await screen.findByText((_text, node) => node?.tagName === 'PRE' && node.textContent === documentText); await waitFor(() => expect(screen.getByLabelText('Your findings')).toBeEnabled()); }
async function confirm(outcome = 'Sign off') { fireEvent.click(screen.getByRole('button', { name: outcome })); fireEvent.click(await screen.findByRole('button', { name: 'Confirm' })); }
describe('U1–U7 mounted route → real provider → shipping typed clients, intercepted fixture data', () => {
  it.each([1,2] as const)('U1/U2 uses generic labels and exact matching @%s findings command; individual receipt stays collecting', async n => {
    await setup(n); mount(); await ready();
    expect(screen.getByRole('heading', { name: n === 1 ? 'Product requirements' : 'Feasibility assessment' })).toBeInTheDocument();
    expect(screen.getByText(n === 1 ? 'product-design · Version 1' : 'different-proposal · Version 1')).toBeInTheDocument();
    expect(document.querySelector('script:not([src])')).toBeNull();
    fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: '意见\r\nCafe\u0301\n' } });
    await confirm('Request changes');
    await screen.findByText('Your changes request recorded');
    expect(screen.getByText('Waiting for remaining responses')).toBeInTheDocument();
    const post = transport.request.mock.calls.find(([p]) => p.endsWith('/human-receipts'));
    expect(post![1].body.format).toBe(`workflow-human-review-command@${n}`);
    expect(post![1].body.findings_bytes_base64).toBe(base64(exactBytes('意见\nCafe\u0301\n'))); // DOM textarea normalizes CRLF before operator submission.
    expect(transport.request.mock.calls.some(([p]) => p.endsWith('/revisions'))).toBe(false);
  });
  it('U3 freezes one key/body, locks the opposite outcome and explicitly retries after transport loss', async () => {
    const original = transport.request.getMockImplementation()!;
    let count = 0;
    transport.request.mockImplementation(async (...args) => { if (String(args[0]).endsWith('/human-receipts') && ++count === 1) throw new TypeError('connection lost'); return original(...args); });
    mount(); await ready(); fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'exact\n意见' } });
    await confirm(); await screen.findByRole('button', { name: 'Retry same response' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeDisabled();
    expect(count).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry same response' }));
    await screen.findByText('Your sign-off recorded');
    const posts = transport.request.mock.calls.filter(([p]) => p.endsWith('/human-receipts'));
    expect(posts).toHaveLength(2); expect(JSON.stringify(posts[0][1].body)).toBe(JSON.stringify(posts[1][1].body));
  });
  it('U4 cross-org switch hides old content/draft and late hash cannot send', async () => {
    mount(); await ready(); fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'old draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'other' }));
    expect(screen.queryByText((_text, node) => node?.tagName === 'PRE' && node.textContent === documentText)).toBeNull(); expect(screen.queryByDisplayValue('old draft')).toBeNull();
    await screen.findByText('Review unavailable');
    expect(transport.request.mock.calls.filter(([p]) => p.endsWith('/human-receipts'))).toHaveLength(0);
  });
  it('U4 current refetch refusal hides selected bytes and effects; does not treat error as empty', async () => {
    mount(); await ready(); const original = transport.request.getMockImplementation()!;
    transport.request.mockImplementation(async (...args) => String(args[0]).endsWith('/review-selection') ? Promise.reject(fixtureError(403, 'revoked', { code: 'revoked', required_action: 'owner' })) : original(...args));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await screen.findByText('Review unavailable');
    expect(screen.queryByText((_text, node) => node?.tagName === 'PRE' && node.textContent === documentText)).toBeNull(); expect(screen.queryByRole('button', { name: 'Sign off' })).toBeNull();
    expect(screen.getByText(/required_action: owner/)).toBeInTheDocument();
  });
  it('U5 history is read only even for a pending selected request', async () => {
    mount('/orgs/demo/reviews/activation-a?view=history'); await screen.findByText((_text, node) => node?.tagName === 'PRE' && node.textContent === documentText);
    expect(screen.getByText('Revision 1 · History · Read only')).toBeInTheDocument();
    expect(screen.queryByLabelText('Your findings')).toBeNull(); expect(screen.queryByRole('button', { name: 'Sign off' })).toBeNull();
  });
  it('U6 return is separate, requires fresh server readiness and pins the original CAS body', async () => {
    selection.original!.collection!.state = 'needs_revision'; selection.return.eligible = true;
    const original = transport.request.getMockImplementation()!;
    transport.request.mockImplementation(async (...args) => {
      if (String(args[0]).endsWith('/revisions')) throw new TypeError('return response lost');
      return original(...args);
    });
    mount(); await ready(); fireEvent.click(screen.getByRole('button', { name: 'Return to author' }));
    await screen.findByRole('heading', { name: 'Return revision 1 to the author?' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Return to author' }).at(-1)!);
    await screen.findByRole('button', { name: 'Retry same return' });
    await waitFor(() => expect(transport.request.mock.calls.some(([p]) => p.endsWith('/revisions'))).toBe(true));
    const post = transport.request.mock.calls.find(([p]) => p.endsWith('/revisions'));
    expect(post![1].body.predecessor_submission_id).toBe('submission-a'); expect(post![1].body.authority).toEqual(activation.authority);
    expect(transport.request.mock.calls.filter(([p]) => p.endsWith('/human-receipts'))).toHaveLength(0);
  });
  it('U7 locale switch preserves mounted draft, selection and focus with zero requests', async () => {
    mount(); await ready(); const input = screen.getByLabelText('Your findings') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: 'untranslated draft\n中文' } }); input.focus(); input.setSelectionRange(2,7);
    const count = transport.request.mock.calls.length;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'locale' })); });
    expect(screen.getByLabelText('您的评审意见')).toBe(input); expect(input.value).toBe('untranslated draft\n中文');
    expect(document.activeElement).toBe(input); expect(input.selectionStart).toBe(2); expect(input.selectionEnd).toBe(7);
    expect(transport.request.mock.calls.length).toBe(count);
  });
  it('U3 reload restores the same envelope and requires explicit replay, with no automatic POST', async () => {
    const original = transport.request.getMockImplementation()!; let count = 0;
    transport.request.mockImplementation(async (...args) => { if (String(args[0]).endsWith('/human-receipts') && ++count === 1) throw new TypeError('connection lost'); return original(...args); });
    const first = mount(); await ready(); fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'frozen reload findings' } }); await confirm();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull()); first.unmount();
    mount(); await screen.findByRole('button', { name: 'Retry same response' });
    expect(count).toBe(1); fireEvent.click(screen.getByRole('button', { name: 'Retry same response' }));
    await screen.findByText('Your sign-off recorded');
    const posts = transport.request.mock.calls.filter(([path]) => path.endsWith('/human-receipts'));
    expect(JSON.stringify(posts[0][1].body)).toBe(JSON.stringify(posts[1][1].body));
  });
  it('U3/U4 latch precedes delayed hash; duplicate confirm and a switch cannot send an old command', async () => {
    mount(); await ready(); fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'old intent' } });
    const digest = crypto.subtle.digest.bind(crypto.subtle); let release!: (value: ArrayBuffer) => void;
    const slow = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(() => new Promise<ArrayBuffer>(r => { release = r; }));
    fireEvent.click(screen.getByRole('button', { name: 'Sign off' })); const button = await screen.findByRole('button', { name: 'Confirm' });
    fireEvent.click(button); fireEvent.click(button);
    expect(slow).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('other'));
    await act(async () => { release(await digest('SHA-256', exactBytes('old intent') as BufferSource)); }); slow.mockRestore();
    await screen.findByText('Review unavailable');
    expect(transport.request.mock.calls.filter(([path]) => path.endsWith('/human-receipts'))).toHaveLength(0);
    expect(screen.queryByDisplayValue('old intent')).toBeNull();
  });
  it('U5/U6 fresh r2 uses @3 and remains separate from a signed r1 with identical document digest', async () => {
    const first = transport.request.getMockImplementation()!;
    const digest = request.scope.submission_digest;
    const oldReceipt = { format: 'workflow-review-receipt@2', receipt_id: 'receipt-r1', request_id: 'request-a', submission_id: 'submission-a', submission_digest: digest, assignment_generation: 1, request_scope_digest: request.request_scope_digest, outcome: 'changes_requested', findings_bytes_base64: 'b2xk', findings_digest: await sha256(exactBytes('old')), created_at: 'now' };
    const oldRequest = { ...request, status: 'changes_requested', signed: true, pending: false, receipt: oldReceipt };
    selection.successor = { revision_operation_id: 'revision-a', human_request_id: 'request-r2' };
    const scope = { ...request.scope, format: 'workflow-review-request@3', revision_operation_id: 'revision-a', request_id: 'request-r2', revision: 2, assignment_generation: 2, submission_id: 'submission-r2', round_id: 'round-r2', artifact: { identity: { org_namespace: 'demo', instance_id: 'instance-a', output_slot: 'output' }, submission_id: 'submission-r2', revision: 2, kind: 'document', bytes_digest: digest, subject: { content_digest: digest } } };
    delete (scope as { handoff_id?: string }).handoff_id;
    let second = { ...request, format: 'workflow-review-request-receipt@3', request_id: 'request-r2', request_scope_digest: '3'.repeat(64), scope, prior_review: { request_id: 'request-a', request_scope_digest: request.request_scope_digest, submission_id: 'submission-a', submission_digest: digest, submission_bytes_base64: request.submission_bytes_base64, receipt: oldReceipt } };
    const revision = { format: 'workflow-document-revision-receipt@1', replayed: false, core: { revision_operation_id: 'revision-a', instance_id: 'instance-a', activation_id: 'activation-a', predecessor_submission_id: 'submission-a', predecessor_round_id: 'round-a', expected_revision: 1, target_revision: 2, author_task_id: 'TASK-200', created_at: 'now' }, current: { state: 'revision_reviewing', reason: null, reconciliation_required: false, eligible: true, submission_id: 'submission-r2', submission_digest: digest, graph: { submission_id: 'submission-r2', round_id: 'round-r2', request_ids: ['request-r2'], reviewer_task_ids: [] }, collection: { state: 'collecting', approved_use: false, validity: 'current', reason: null, owner_principal: 'human:founder', required_action: null } } };
    transport.request.mockImplementation(async (...args) => {
      const path = String(args[0]);
      if (path.endsWith('/revisions/revision-a')) return structuredClone(revision);
      if (path.endsWith('/requests/request-r2')) return structuredClone(second);
      if (path.endsWith('/requests/request-a')) return structuredClone(oldRequest);
      if (path.endsWith('/human-receipts')) {
        const b = args[1].body as HumanReviewCommand;
        const receipt = { ...oldReceipt, format: 'workflow-review-receipt@3', receipt_id: 'receipt-r2', request_id: b.request_id, request_scope_digest: b.request_scope_digest, submission_id: 'submission-r2', assignment_generation: 2, outcome: b.outcome, findings_bytes_base64: b.findings_bytes_base64, findings_digest: b.findings_digest };
        second = { ...second, signed: true, pending: false, status: b.outcome, receipt } as typeof second;
        return { format: 'workflow-human-review-receipt@3', receipt };
      }
      return first(...args);
    });
    mount(); await ready(); expect(screen.getByText('Immutable document · Revision 2')).toBeInTheDocument(); expect(screen.getByLabelText('Your findings')).toHaveValue('');
    fireEvent.click(screen.getByRole('link', { name: 'History' })); await screen.findByText('Revision 1 · History · Read only');
    expect(screen.getByText('Your receipt: receipt-r1')).toBeInTheDocument(); expect(screen.queryByLabelText('Your findings')).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Current' })); await ready();
    fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'fresh r2' } }); await confirm(); await screen.findByText('Your sign-off recorded');
    const b = transport.request.mock.calls.find(([path]) => path.endsWith('/human-receipts'))![1].body;
    expect(b.format).toBe('workflow-human-review-command@3'); expect(b.expected_revision).toBe(2); expect(b.request_id).toBe('request-r2');
    expect(screen.getByText('Your receipt: receipt-r2')).toBeInTheDocument(); expect(screen.getByText('Waiting for remaining responses')).toBeInTheDocument();
  });

  it('U1/U4 refuses inconsistent public navigation pins before private handoff/request reads', async () => {
    selection.instance_id = 'other-instance';
    mount(); await screen.findByText('Review unavailable');
    expect(transport.request.mock.calls.some(([path]) => path.includes('/handoffs/') || path.includes('/requests/'))).toBe(false);
  });

  it('U1/U4 equal document digests in two instances do not share a selection or draft', async () => {
    const original = transport.request.getMockImplementation()!;
    const a = structuredClone(activation); a.activation_id = 'activation-b'; a.instance_id = 'instance-b'; a.template.identity_id = 'another-template';
    const h = structuredClone(handoff); h.core.handoff_id = 'handoff-b'; h.core.instance_id = 'instance-b'; h.core.reserved_submission_id = 'submission-b'; h.current.graph = { submission_id: 'submission-b', round_id: 'round-b', request_ids: ['request-b'], reviewer_task_ids: [] };
    const d = structuredClone(selection); d.activation_id = 'activation-b'; d.instance_id = 'instance-b'; d.original!.handoff_id = 'handoff-b'; d.original!.human_request_id = 'request-b';
    const q = structuredClone(request); q.request_id = 'request-b'; q.scope.request_id = 'request-b'; q.scope.instance_id = 'instance-b'; q.scope.activation.id = 'activation-b'; q.scope.submission_id = 'submission-b'; q.scope.round_id = 'round-b'; q.scope.template = a.template;
    if ('handoff_id' in q.scope) q.scope.handoff_id = 'handoff-b';
    if ('artifact' in q.scope) { q.scope.artifact.identity.instance_id = 'instance-b'; q.scope.artifact.submission_id = 'submission-b'; }
    let release!: () => void; const selected = new Promise<void>(resolve => { release = resolve; });
    transport.request.mockImplementation(async (...args) => {
      const path = String(args[0]);
      if (path.endsWith('/activations')) return [activation,a];
      if (path.endsWith('/activations/activation-b')) return a;
      if (path.endsWith('/activation-b/review-selection')) { await selected; return d; }
      if (path.endsWith('/handoffs/handoff-b')) return h;
      if (path.endsWith('/requests/request-b')) return q;
      return original(...args);
    });
    mount(); await ready(); fireEvent.change(screen.getByLabelText('Your findings'), { target: { value: 'instance A private draft' } });
    fireEvent.click(screen.getByRole('link', { name: /another-template/ }));
    expect(screen.queryByDisplayValue('instance A private draft')).toBeNull();
    expect(screen.queryByText((_text, node) => node?.tagName === 'PRE' && node.textContent === documentText)).toBeNull();
    await act(async () => { release(); }); await ready();
    expect(screen.getByText('Org: demo · Instance: instance-b · Output: output')).toBeInTheDocument();
    expect(screen.getByLabelText('Your findings')).toHaveValue('');
    expect(q.scope.submission_digest).toBe(request.scope.submission_digest);
  });

});

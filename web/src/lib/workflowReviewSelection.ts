/** Exact selected identity, bytes and finite replay envelope. No transport or authority grant. */
import type {
  AuthorHandoffReceipt, DocumentRevisionCommand, DocumentRevisionReceipt,
  HumanReviewCommand, HumanReviewReceipt, OwnerReviewSelection,
  ReviewReceiptProjection, WorkflowActivationReceipt, WorkflowReviewRequestReceipt,
} from './api/types';

export class ReviewIntegrityError extends Error {}
function fail(): never { throw new ReviewIntegrityError('workflow_review_integrity'); }
const digestPattern = /^[a-f0-9]{64}$/;
const tokenPattern = /^[A-Za-z0-9._:-]{1,128}$/;
export function exactBytes(text: string): Uint8Array {
  if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text)) fail();
  const bytes = new TextEncoder().encode(text);
  if (!bytes.length || bytes.length > 1048576) fail();
  return bytes;
}
export function base64(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}
export async function sha256(bytes: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))), b => b.toString(16).padStart(2, '0')).join('');
}
export async function verifiedText(encoded: string, digest: string): Promise<string> {
  if (!digestPattern.test(digest) || typeof encoded !== 'string' || encoded.length > 1398104) fail();
  let bytes: Uint8Array;
  try { bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0)); } catch { return fail(); }
  if (!bytes.length || bytes.length > 1048576 || base64(bytes) !== encoded || await sha256(bytes) !== digest) fail();
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); } catch { return fail(); }
}
function family(request: WorkflowReviewRequestReceipt): 1 | 2 | 3 {
  const n = request.scope.format === 'workflow-review-request@1' ? 1 : request.scope.format === 'workflow-review-request@2' ? 2 : request.scope.format === 'workflow-review-request@3' ? 3 : fail();
  if (request.format !== `workflow-review-request-receipt@${n}` || request.scope.revision !== (n === 3 ? 2 : 1)) fail();
  return n;
}
export async function validateReceipt(request: WorkflowReviewRequestReceipt, receipt: ReviewReceiptProjection): Promise<string> {
  const n = family(request), s = request.scope;
  if (receipt.format !== `workflow-review-receipt@${n}` || !tokenPattern.test(receipt.receipt_id) ||
      receipt.request_id !== request.request_id || receipt.request_scope_digest !== request.request_scope_digest ||
      receipt.submission_id !== s.submission_id || receipt.submission_digest !== s.submission_digest ||
      receipt.assignment_generation !== s.assignment_generation || !s.outcomes.includes(receipt.outcome)) fail();
  return verifiedText(receipt.findings_bytes_base64, receipt.findings_digest);
}
export interface ReviewDetail {
  slug: string; activation: WorkflowActivationReceipt; selection: OwnerReviewSelection;
  handoff: AuthorHandoffReceipt | null; revision: DocumentRevisionReceipt | null;
  request: WorkflowReviewRequestReceipt | null; history: boolean;
  document: string | null; findings: string | null;
}
/** All cross-object pins must agree before rendering any private bytes. */
export async function validateDetail(detail: Omit<ReviewDetail, 'document' | 'findings'>): Promise<ReviewDetail> {
  const { slug, activation: a, selection: d, handoff: h, revision: rev, request: q, history } = detail;
  if (d.format !== 'workflow-owner-review-selection@1' || d.activation_id !== a.activation_id || d.instance_id !== a.instance_id) fail();
  if (h && (h.core.handoff_id !== d.original?.handoff_id || h.core.instance_id !== a.instance_id || h.core.revision !== 1 ||
      (h.current.graph && h.current.graph.submission_id !== h.core.reserved_submission_id))) fail();
  if (rev && (rev.format !== 'workflow-document-revision-receipt@1' || rev.core.revision_operation_id !== d.successor?.revision_operation_id ||
      rev.core.instance_id !== a.instance_id || rev.core.activation_id !== a.activation_id || rev.core.target_revision !== 2 ||
      rev.core.expected_revision !== 1 || rev.core.predecessor_submission_id !== h?.core.reserved_submission_id ||
      rev.core.predecessor_round_id !== h?.current.graph?.round_id)) fail();
  if (!q) return { ...detail, document: null, findings: null };
  const s = q.scope, n = family(q);
  const original = history || !d.successor;
  const id = original ? d.original?.human_request_id : d.successor?.human_request_id;
  const graph = original ? h?.current.graph : rev?.current.graph;
  const digest = original ? h?.core.candidate_digest : rev?.current.submission_digest;
  if (q.request_id !== id || s.request_id !== id || s.kind !== 'human' || s.org_slug !== slug || s.instance_id !== a.instance_id ||
      s.activation.id !== a.activation_id || s.activation.revision !== a.activation_revision ||
      s.activation.request_digest !== a.original_request_digest || s.template.version_id !== a.template.version_id ||
      s.template.definition_digest !== a.template.definition_digest || s.template.identity_id !== a.template.identity_id ||
      s.template.version !== a.template.version || s.template.compiler_pin !== a.template.compiler_pin || s.template.validator_pin !== a.template.validator_pin ||
      s.template.source_pin !== a.template.source_pin || s.revision !== (original ? 1 : 2) ||
      s.submission_id !== graph?.submission_id || s.round_id !== graph?.round_id || s.submission_digest !== digest ||
      !graph?.request_ids.includes(q.request_id) || !digestPattern.test(q.request_scope_digest) ||
      !Number.isInteger(s.assignment_generation) || s.assignment_generation < 1 ||
      !s.outcomes.length || new Set(s.outcomes).size !== s.outcomes.length || s.outcomes.some(o => o !== 'approved' && o !== 'changes_requested') ||
      (original && 'handoff_id' in s && s.handoff_id !== h?.core.handoff_id) ||
      (!original && (!('revision_operation_id' in s) || s.revision_operation_id !== rev?.core.revision_operation_id))) fail();
  if (n !== 1) {
    if (!('artifact' in s) || s.repository_resolution !== null) fail();
    const art = s.artifact;
    if (art.kind !== 'document' || art.identity.org_namespace !== slug || art.identity.instance_id !== a.instance_id ||
        art.identity.output_slot !== 'output' || art.submission_id !== s.submission_id || art.revision !== s.revision ||
        art.bytes_digest !== s.submission_digest || art.subject.content_digest !== s.submission_digest) fail();
  }
  if ((q.signed && (q.pending || !q.receipt || q.status !== q.receipt.outcome)) ||
      (!q.signed && (!q.pending || q.receipt !== null || q.status !== 'pending'))) fail();
  const document = await verifiedText(q.submission_bytes_base64, s.submission_digest);
  const findings = q.signed ? await validateReceipt(q, q.receipt) : null;
  return { ...detail, document, findings };
}
export async function humanCommand(request: WorkflowReviewRequestReceipt, outcome: HumanReviewCommand['outcome'], text: string): Promise<HumanReviewCommand> {
  const n = family(request);
  if (!request.scope.outcomes.includes(outcome)) fail();
  const bytes = exactBytes(text);
  const body = {
    format: `workflow-human-review-command@${n}`, action: 'sign-review', operation_key: `human-review:${crypto.randomUUID()}`,
    request_id: request.request_id, expected_revision: request.scope.revision, request_scope_digest: request.request_scope_digest,
    outcome, findings_bytes_base64: base64(bytes), findings_digest: await sha256(bytes),
  };
  return body as HumanReviewCommand;
}
export function returnCommand(detail: ReviewDetail): DocumentRevisionCommand {
  const { activation: a, selection: d, handoff: h } = detail;
  if (!d.return.eligible || d.successor || !h?.current.graph || d.original?.collection?.state !== 'needs_revision' ||
      d.original.collection.validity !== 'current' || d.original.collection.approved_use) fail();
  return {
    format: 'workflow-document-revision-command@1', operation_key: `document-return:${crypto.randomUUID()}`,
    instance_id: a.instance_id, activation_id: a.activation_id, expected_revision: 1,
    predecessor_submission_id: h.current.graph.submission_id, predecessor_submission_digest: h.core.candidate_digest,
    predecessor_round_id: h.current.graph.round_id, authority: { namespace: a.authority.namespace, generation: a.authority.generation, snapshot_digest: a.authority.snapshot_digest },
  };
}
export async function validateHumanResponse(detail: ReviewDetail, command: HumanReviewCommand, response: HumanReviewReceipt): Promise<void> {
  if (!detail.request || response.format !== command.format.replace('command', 'receipt')) fail();
  await validateReceipt(detail.request, response.receipt);
  if (response.receipt.outcome !== command.outcome || response.receipt.findings_digest !== command.findings_digest ||
      response.receipt.findings_bytes_base64 !== command.findings_bytes_base64) fail();
}
export function validateReturnResponse(command: DocumentRevisionCommand, response: DocumentRevisionReceipt): void {
  const c = response.core;
  if (response.format !== 'workflow-document-revision-receipt@1' || c.activation_id !== command.activation_id || c.instance_id !== command.instance_id ||
      c.predecessor_submission_id !== command.predecessor_submission_id || c.predecessor_round_id !== command.predecessor_round_id ||
      c.expected_revision !== 1 || c.target_revision !== 2 || !tokenPattern.test(c.revision_operation_id) || !/^TASK-\d+$/.test(c.author_task_id)) fail();
}
export type JournalCommand = HumanReviewCommand | DocumentRevisionCommand;
export interface CommandEnvelope { version: 1; identity: string; serialized: string }
export function journalKey(identity: string): string { return `happyranch.workflow.command@1:${identity}`; }
export function responseIdentity(d: ReviewDetail): string | null {
  return d.request ? JSON.stringify([d.slug, d.activation.activation_id, 'human', d.request.request_id, d.request.scope.revision, d.request.request_scope_digest]) : null;
}
export function ownerIdentity(d: ReviewDetail): string | null {
  return d.handoff?.current.graph ? JSON.stringify([d.slug, d.activation.activation_id, 'owner-human', d.handoff.core.reserved_submission_id, d.handoff.core.candidate_digest, d.handoff.current.graph.round_id]) : null;
}
function parseCommand(serialized: string): JournalCommand {
  const b = JSON.parse(serialized);
  const human = /^workflow-human-review-command@[123]$/.test(b.format);
  const keys = human ? ['format','action','operation_key','request_id','expected_revision','request_scope_digest','outcome','findings_bytes_base64','findings_digest'] :
    ['format','operation_key','instance_id','activation_id','expected_revision','predecessor_submission_id','predecessor_submission_digest','predecessor_round_id','authority'];
  if (JSON.stringify(Object.keys(b)) !== JSON.stringify(keys) || JSON.stringify(b) !== serialized || !tokenPattern.test(b.operation_key)) fail();
  if (human) {
    if (b.action !== 'sign-review' || !tokenPattern.test(b.request_id) || !digestPattern.test(b.request_scope_digest) ||
        !digestPattern.test(b.findings_digest) || (b.outcome !== 'approved' && b.outcome !== 'changes_requested') ||
        b.expected_revision !== (b.format.endsWith('@3') ? 2 : 1) || typeof b.findings_bytes_base64 !== 'string') fail();
  } else if (b.format !== 'workflow-document-revision-command@1' || b.expected_revision !== 1 || !tokenPattern.test(b.instance_id) ||
      !tokenPattern.test(b.activation_id) || !tokenPattern.test(b.predecessor_submission_id) || !tokenPattern.test(b.predecessor_round_id) ||
      !digestPattern.test(b.predecessor_submission_digest) || !b.authority ||
      JSON.stringify(Object.keys(b.authority)) !== JSON.stringify(['namespace','generation','snapshot_digest']) ||
      typeof b.authority.namespace !== 'string' || !Number.isInteger(b.authority.generation) || b.authority.generation < 1 || !digestPattern.test(b.authority.snapshot_digest)) fail();
  return b;
}
export function saveCommand(identity: string, command: JournalCommand): CommandEnvelope {
  const envelope: CommandEnvelope = { version: 1, identity, serialized: JSON.stringify(command) };
  parseCommand(envelope.serialized);
  sessionStorage.setItem(journalKey(identity), JSON.stringify(envelope));
  return envelope;
}
export function loadCommand(identity: string): CommandEnvelope | null {
  const raw = sessionStorage.getItem(journalKey(identity));
  if (raw === null) return null;
  const e = JSON.parse(raw);
  if (JSON.stringify(Object.keys(e)) !== JSON.stringify(['version','identity','serialized']) || e.version !== 1 || e.identity !== identity || typeof e.serialized !== 'string') fail();
  parseCommand(e.serialized);
  return e;
}
export async function admittedCommand(envelope: CommandEnvelope, detail: ReviewDetail): Promise<JournalCommand> {
  const b = parseCommand(envelope.serialized);
  if ('request_id' in b) {
    if (envelope.identity !== responseIdentity(detail) || !detail.request || b.request_id !== detail.request.request_id ||
        b.request_scope_digest !== detail.request.request_scope_digest || b.expected_revision !== detail.request.scope.revision ||
        b.format !== `workflow-human-review-command@${family(detail.request)}` || !detail.request.scope.outcomes.includes(b.outcome)) fail();
    await verifiedText(b.findings_bytes_base64, b.findings_digest);
    if (detail.request.signed && (detail.request.receipt.outcome !== b.outcome || detail.request.receipt.findings_digest !== b.findings_digest || detail.request.receipt.findings_bytes_base64 !== b.findings_bytes_base64)) fail();
  } else {
    const h = detail.handoff, a = detail.activation;
    if (envelope.identity !== ownerIdentity(detail) || b.instance_id !== a.instance_id || b.activation_id !== a.activation_id ||
        b.predecessor_submission_id !== h?.core.reserved_submission_id || b.predecessor_submission_digest !== h?.core.candidate_digest ||
        b.predecessor_round_id !== h?.current.graph?.round_id || JSON.stringify(b.authority) !== JSON.stringify(a.authority)) fail();
  }
  return b;
}
export function clearCommand(identity: string): void { sessionStorage.removeItem(journalKey(identity)); }
export function clearSelectionCommands(slug: string, activationId: string): void {
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const key = sessionStorage.key(i);
    if (!key?.startsWith('happyranch.workflow.command@1:')) continue;
    try { const ids = JSON.parse(key.slice('happyranch.workflow.command@1:'.length)); if (ids[0] === slug && ids[1] === activationId) sessionStorage.removeItem(key); } catch { /* Unrelated corrupt entry is never replayed. */ }
  }
}

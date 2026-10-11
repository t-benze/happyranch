import { beforeEach, expect, test, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/server';
import { __resetTokenCacheForTests } from '../auth';
import { ApiError } from './client';
import { getAuthorHandoff, stageAuthorHandoff } from './workflowSubmissions';
import { getWorkflowReviewRequest } from './workflowReviews';
import type { AuthorHandoffCommand, AuthorHandoffProof, AuthorHandoffReceipt } from './types';

const command: AuthorHandoffCommand = {
  format: 'workflow-author-handoff-command@1', operation_key: 'original-key', activation_id: 'activation:one',
  expected_revision: 1, submission_bytes_base64: 'VTNhIGltbXV0YWJsZSBzdWJqZWN0DQo=',
  submission_digest: '2d24588f7fa9b1a0c4a7e8e2b34740bca0f7f41e5ad811219034b57eb31d05f3',
};
const receipt: AuthorHandoffReceipt = {
  format: 'workflow-author-handoff-receipt@1', replayed: true,
  core: { handoff_id: 'handoff:one', reserved_submission_id: 'submission:one', instance_id: 'instance:one',
    revision: 1, candidate_digest: command.submission_digest, request_digest: 'd'.repeat(64), created_at: '2026-10-11T00:00:00.000000Z' },
  current: { state: 'pending', reason: 'awaiting_terminal_proof', pending: true, reconciliation_required: false, graph: null },
};
const endpoint = '/api/v1/orgs/alpha/workflows/submissions/handoffs';

beforeEach(() => {
  __resetTokenCacheForTests();
  sessionStorage.setItem('happyranch.token', 'cached');
});

test('owner GET forwards exact independent proof selector and returns the fixture unchanged', async () => {
  let observed: URL | undefined;
  server.use(http.get(endpoint + '/handoff:one', ({ request }) => {
    observed = new URL(request.url);
    return HttpResponse.json(receipt);
  }));
  expect(await getAuthorHandoff('alpha', 'handoff:one', { proof_kind: 'owner-human' })).toEqual(receipt);
  expect([...observed!.searchParams]).toEqual([['proof_kind', 'owner-human']]);
  // This checks the fixture's shape; server receipt closure needs public-boundary coverage.
  expect(Object.keys(receipt.core).sort()).toEqual(['candidate_digest', 'created_at', 'handoff_id', 'instance_id', 'request_digest', 'reserved_submission_id', 'revision']);
});

test.each([403, 409])('stage refusal %i preserves exact payload/key, one fetch and no token refresh', async (status) => {
  const seen: unknown[] = [];
  let bootstrap = 0;
  server.use(
    http.get('/api/v1/auth/bootstrap', () => { bootstrap++; return HttpResponse.json({ token: 'new' }); }),
    http.post(endpoint, async ({ request }) => {
      seen.push({ params: [...new URL(request.url).searchParams], body: await request.json() });
      return HttpResponse.json({ detail: { code: status === 403 ? 'auth_path_conflict' : 'workflow_author_handoff_source_not_current' } }, { status });
    }),
  );
  await expect(stageAuthorHandoff('alpha', 'source:session', command)).rejects.toBeInstanceOf(ApiError);
  expect(seen).toEqual([{ params: [['session_id', 'source:session']], body: command }]);
  expect(bootstrap).toBe(0);
});

const proofs: AuthorHandoffProof[] = [
  { session_id: 'source:session', activation_id: 'activation:one' },
  { session_id: 'review:session', request_id: 'request:one' },
];
test.each(proofs)('bearer agent GET refuses independently for %j', async (proof) => {
  let observed: [string, string][] = [];
  server.use(http.get(endpoint + '/handoff:one', ({ request }) => {
    observed = [...new URL(request.url).searchParams];
    return HttpResponse.json({ detail: { code: 'auth_path_conflict' } }, { status: 403 });
  }));
  await expect(getAuthorHandoff('alpha', 'handoff:one', proof)).rejects.toMatchObject({ status: 403, code: 'auth_path_conflict' });
  expect(observed).toEqual(Object.entries(proof));
});

test.each([200, 401, 403, 409])('generic first401 permits exactly one same-operation fetch; second %i is terminal', async (secondStatus) => {
  const seen: { url: string; body: string; authorization: string | null }[] = [];
  let bootstrap = 0;
  server.use(
    http.get('/api/v1/auth/bootstrap', () => { bootstrap++; return HttpResponse.json({ token: 'fresh' }); }),
    http.post(endpoint, async ({ request }) => {
      seen.push({ url: request.url, body: await request.text(), authorization: request.headers.get('authorization') });
      const status = seen.length === 1 ? 401 : secondStatus;
      return HttpResponse.json(status === 200 ? receipt : { detail: { code: 'fixture_transport_refusal' } }, { status });
    }),
  );
  const result = stageAuthorHandoff('alpha', 'source:session', command);
  if (secondStatus === 200) expect(await result).toEqual(receipt);
  else await expect(result).rejects.toMatchObject({ status: secondStatus, code: 'fixture_transport_refusal' });
  expect(seen).toHaveLength(2);
  expect(seen.map(({ url, body }) => ({ url, body }))).toEqual([
    { url: window.location.origin + endpoint + '?session_id=source%3Asession', body: JSON.stringify(command) },
    { url: window.location.origin + endpoint + '?session_id=source%3Asession', body: JSON.stringify(command) },
  ]);
  expect(seen.map((entry) => entry.authorization)).toEqual(['Bearer cached', 'Bearer fresh']);
  expect(bootstrap).toBe(1);
});

test('fetch loss preserves native error and cannot recreate the operation', async () => {
  const loss = new Error('response lost');
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(loss);
  try {
    await expect(stageAuthorHandoff('alpha', 'source:session', command)).rejects.toBe(loss);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally { fetchMock.mockRestore(); }
});

test('selected human request GET forwards exact proof and keeps the unsigned response', async () => {
  const unsigned = { format: 'workflow-review-request-receipt@1', request_id: 'request:one', signed: false, pending: true };
  let observed: [string, string][] = [];
  server.use(http.get('/api/v1/orgs/alpha/workflows/reviews/requests/request:one', ({ request }) => {
    observed = [...new URL(request.url).searchParams];
    return HttpResponse.json(unsigned);
  }));
  expect(await getWorkflowReviewRequest('alpha', 'request:one', { proof_kind: 'human' })).toEqual(unsigned);
  expect(observed).toEqual([['proof_kind', 'human']]);
});

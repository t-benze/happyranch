/** Source-contract assertions; mocked HTTP is not DB proof. Execution receipts live in the maker handoff. */
import { beforeEach, describe, expect, test } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/server';
import { bodyAddresses, isEditableAddressableName, listIdentities, preflightAddresses, renameAgent, renameFounder, resolveIdentities } from './identities';
import type { IdentityView } from './types';

const agent: IdentityView = { canonical_id: 'worker', kind: 'agent', lifecycle: 'active', addressable_name: 'Current', name_revision: 2, canonical_definition_revision: null, naming_status: 'ready' };
const founder: IdentityView = { ...agent, canonical_id: 'founder', kind: 'founder', lifecycle: 'founder' };
describe('naming adapters — mocked source contract', () => {
  beforeEach(() => sessionStorage.setItem('happyranch.token', 'mock-token'));
  test('all four real routes keep canonical IDs, positive CAS and exact body without actor/session proof', async () => {
    const calls: { path: string; body: unknown }[] = [];
    server.use(
      http.get('/api/v1/orgs/one/identities', () => HttpResponse.json({ identities: [agent, founder] })),
      http.post('/api/v1/orgs/one/identities/resolve', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json({ resolutions: [{ address: 'Current', identity: agent, status: 'resolved', eligible: true }] }); }),
      http.put('/api/v1/orgs/one/agents/worker/addressable-name', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json(agent); }),
      http.put('/api/v1/orgs/one/founder/addressable-name', async ({ request }) => { calls.push({ path: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json(founder); }),
    );
    expect((await listIdentities('one')).identities[0].canonical_id).toBe('worker');
    await resolveIdentities('one', { addresses: ['Current'], context: 'thread_recipient' });
    const body = { addressable_name: 'Exact-Name', expected_name_revision: 2 };
    await renameAgent('one', 'worker', body); await renameFounder('one', body);
    expect(calls).toEqual([
      { path: '/api/v1/orgs/one/identities/resolve', body: { addresses: ['Current'], context: 'thread_recipient' } },
      { path: '/api/v1/orgs/one/agents/worker/addressable-name', body },
      { path: '/api/v1/orgs/one/founder/addressable-name', body },
    ]);
  });
  test('editable grammar is exact; historical underscore IDs never widen rename grammar', () => {
    for (const name of ['A', '0_name-Test', 'a'.repeat(64)]) expect(isEditableAddressableName(name)).toBe(true);
    for (const name of ['', '_worker', ' A', 'A ', '张三', 'A.B', 'a'.repeat(65)]) expect(isEditableAddressableName(name)).toBe(false);
    expect(bodyAddresses('> "mail@Former and @Current" @unknown @A.B @_worker')).toEqual(['Former', 'Current', 'unknown', 'A.B']);
  });
  test('recognized former body is refused before resolving recipients; unknown body stays literal', async () => {
    const calls: unknown[] = [];
    server.use(http.post('/api/v1/orgs/one/identities/resolve', async ({ request }) => {
      const body = await request.json() as { addresses: string[]; context: string }; calls.push(body);
      return HttpResponse.json({ resolutions: body.addresses.map((address) => ({ address, status: address === 'Former' ? 'former_name' : 'unknown_identity', identity: address === 'Former' ? agent : null, eligible: false })) });
    }));
    await expect(preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['worker'], body: 'email@Former', canonicalAgentIds: ['worker'] })).rejects.toMatchObject({ resolution: { status: 'former_name' } });
    expect(calls).toEqual([{ addresses: ['Former'], context: 'lookup' }]);
    expect(await preflightAddresses({ slug: 'one', context: 'thread_recipient', body: '@unknown', canonicalAgentIds: [] })).toEqual({ recipients: [] });
  });
  test('unavailable naming retains known permanent IDs but never licenses an alias or retries', async () => {
    let requests = 0;
    server.use(http.post('/api/v1/orgs/one/identities/resolve', () => { requests += 1; return HttpResponse.json({ detail: { code: 'naming_unavailable' } }, { status: 503 }); }));
    expect(await preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['_worker'], canonicalAgentIds: ['_worker'] })).toEqual({ recipients: ['_worker'] });
    await expect(preflightAddresses({ slug: 'one', context: 'thread_recipient', recipients: ['Current'], canonicalAgentIds: ['_worker'] })).rejects.toMatchObject({ resolution: { status: 'naming_unavailable' } });
    expect(requests).toBe(2);
  });
});

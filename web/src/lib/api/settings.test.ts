import { beforeEach, describe, expect, expectTypeOf, it, test, vi } from 'vitest';
import { putDaemonCapacity, type RuntimeRegistrationTokenMintRequest } from './settings';
import type { OrgSettings } from './types';

vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';

describe('RuntimeRegistrationTokenMintRequest', () => {
  it('accepts the optional Slice-1A direct-authority workspace adapter', () => {
    const value: RuntimeRegistrationTokenMintRequest = {
      name: 'custom-cli', purpose: 'adapter', intended_profile_name: 'custom-profile',
      workspace_adapter_id: 'codex',
    };
    expectTypeOf(value.workspace_adapter_id).toEqualTypeOf<
      'claude' | 'codex' | 'opencode' | 'pi' | undefined
    >();
  });
});

describe('OrgSettings response contract', () => {
  it('accepts the server-returned reviewer_agents field without creating UI logic', () => {
    expectTypeOf<OrgSettings['reviewer_agents']>().toEqualTypeOf<string[]>();
  });
});

describe('daemon capacity conditional client', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  test('sends one strong If-Match header and omits revision from the exact body', async () => {
    vi.mocked(request).mockResolvedValue({});
    await putDaemonCapacity('alpha', {
      revision: 'sha256:abc', queue_workers: 6, host_global_session_cap: 13,
      rationale: 'receipts', confirm_environment_shadow: false,
    });
    expect(request).toHaveBeenCalledWith('/orgs/alpha/settings/daemon-capacity', {
      method: 'PUT',
      headers: { 'If-Match': '"sha256:abc"' },
      body: { queue_workers: 6, host_global_session_cap: 13,
        rationale: 'receipts', confirm_environment_shadow: false },
    });
  });
});

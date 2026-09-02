import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest';
import {
  decodeTeamEscalationPolicyResponse,
  getTeamEscalationPolicy,
  type TeamEscalationPolicyResponse,
} from './authorityPolicy';

vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';

const empty = {
  team: 'engineering',
  target_manager: 'engineering_manager',
  can_mutate: false,
  bootstrap_required: true,
  activation_guard: {
    ready: false,
    reason: 'TASK-6335 production verification required',
  },
} as const;

describe('team escalation policy response contract', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  it('narrows can_mutate to literal false and decodes the read-only response', async () => {
    expectTypeOf<TeamEscalationPolicyResponse['can_mutate']>().toEqualTypeOf<false>();
    vi.mocked(request).mockResolvedValue(empty);

    await expect(getTeamEscalationPolicy('alpha', 'engineering_manager'))
      .resolves.toEqual(empty);
  });

  it('rejects a server response that advertises mutation capability', () => {
    expect(() => decodeTeamEscalationPolicyResponse({
      ...empty,
      can_mutate: true,
    })).toThrow('Invalid team escalation policy response');
  });
});

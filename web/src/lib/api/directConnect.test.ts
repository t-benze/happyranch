import { describe, expect, it, vi } from 'vitest';
import * as clientModule from './client';
import { getStatus } from './directConnect';

describe('directConnect.getStatus', () => {
  it('round-trips the additive terminal reason field', async () => {
    const response = {
      wrapper_destination: '/runtime/adapters/my-cli-adapter',
      operation_id: 'op-123',
      profile_state: 'failed' as const,
      reason: 'conformance probe failed',
    };
    const request = vi.spyOn(clientModule, 'request').mockResolvedValue(response);

    await expect(getStatus('my-cli')).resolves.toEqual(response);
    expect(request).toHaveBeenCalledWith('/runtime/custom-cli/status', {
      params: { intended_profile_name: 'my-cli' },
    });
  });
});

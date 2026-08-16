import { describe, expect, it, vi } from 'vitest';
import * as clientModule from './client';
import { getStatus, retry } from './directConnect';

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

describe('directConnect.retry', () => {
  it('uses the dedicated retry-validation endpoint, never commit', async () => {
    const response = {
      operation_id: 'op-123',
      profile_state: 'committed' as const,
      profile_name: 'my-cli',
    };
    const request = vi.spyOn(clientModule, 'request').mockResolvedValue(response);

    await expect(retry('op-123')).resolves.toEqual(response);
    expect(request).toHaveBeenCalledWith('/runtime/custom-cli/op-123/retry', {
      method: 'POST',
    });
    expect(request).not.toHaveBeenCalledWith('/runtime/custom-cli/op-123/commit', expect.anything());
  });
});

import { describe, expect, it, vi } from 'vitest';
import * as clientModule from './client';
import { forget, getStatus, retry } from './directConnect';

describe('directConnect.getStatus', () => {
  it('round-trips the canonical lifecycle state and retry eligibility', async () => {
    const response = {
      wrapper_destination: '/runtime/adapters/my-cli-adapter',
      operation_id: 'op-123',
      profile_state: 'failed' as const,
      reason: 'conformance probe failed',
      state: 'failed_retryable' as const,
      retry_eligible: true,
      historical_projection_state: null,
      historical_projection_reason: null,
      retry_state: null,
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

describe('directConnect.forget', () => {
  it('uses the failed-only cleanup endpoint', async () => {
    const response = {
      operation_id: 'op-123',
      status: 'forgotten' as const,
      wrapper_status: 'preserved_changed' as const,
    };
    const request = vi.spyOn(clientModule, 'request').mockResolvedValue(response);

    await expect(forget('op-123')).resolves.toEqual(response);
    expect(request).toHaveBeenCalledWith('/runtime/custom-cli/op-123/forget', {
      method: 'POST',
    });
  });
});

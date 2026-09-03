import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as runtimeExecutors from './runtime-executors';
import * as clientModule from './client';

describe('runtime-executors api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listRuntimeProfiles GETs the profiles list with no params', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ profiles: [] });
    await runtimeExecutors.listRuntimeProfiles();
    expect(spy).toHaveBeenCalledWith('/executors/runtime/profiles');
  });

  it('removeRuntimeProfile DELETEs the named profile with no body', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ name: 'my-runner', removed: true });
    await runtimeExecutors.removeRuntimeProfile('my-runner');
    expect(spy).toHaveBeenCalledWith('/executors/runtime/profiles/my-runner', {
      method: 'DELETE',
    });
  });

  it('removeRuntimeProfile URL-encodes the profile name', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ name: 'a b/c', removed: true });
    await runtimeExecutors.removeRuntimeProfile('a b/c');
    expect(spy).toHaveBeenCalledWith(
      '/executors/runtime/profiles/a%20b%2Fc',
      { method: 'DELETE' },
    );
  });

  it('RuntimeProfileEntry carries the canonical custom-adapter identity', async () => {
    const mockResponse = {
      profiles: [
        {
          name: 'openclaw',
          adapter: 'pi',
          adapter_id: 'pi',
          workspace_adapter_id: 'pi',
          command_adapter_id: 'custom-adapter:openclaw',
          present: true,
          path: '/usr/local/bin/openclaw',
        },
        {
          name: 'approved-runner',
          adapter: 'pi',
          adapter_id: 'pi',
          workspace_adapter_id: 'pi',
          command_adapter_id: 'custom-adapter:approved-runner',
          present: false,
          path: null,
        },
      ],
    } satisfies import('./runtime-executors').RuntimeProfileList;
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue(mockResponse);
    const result = await runtimeExecutors.listRuntimeProfiles();
    expect(spy).toHaveBeenCalledWith('/executors/runtime/profiles');
    expect(result.profiles).toHaveLength(2);
    expect(result.profiles[0].workspace_adapter_id).toBe('pi');
    expect(result.profiles[0].command_adapter_id).toBe('custom-adapter:openclaw');
    expect(result.profiles[0].adapter).toBe('pi'); // deprecated alias preserved
    expect(result.profiles[0].adapter_id).toBe('pi'); // deprecated alias preserved
    expect(result.profiles[1].workspace_adapter_id).toBe('pi');
    expect(result.profiles[1].command_adapter_id).toBe('custom-adapter:approved-runner');
    expect(result.profiles[1].adapter).toBe('pi');
    expect(result.profiles[1].adapter_id).toBe('pi');
  });

  it('RuntimeProfileEntry type assertion carries canonical fields', async () => {
    // This is a compile-time-only type assertion test.
    // If RuntimeProfileEntry does not have workspace_adapter_id, tsc fails.
    const entry: import('./runtime-executors').RuntimeProfileEntry = {
      name: 'test',
      adapter: 'claude',
      adapter_id: 'claude',
      workspace_adapter_id: 'claude',
      command_adapter_id: 'custom-adapter:test',
      present: true,
      path: '/usr/bin/test-cli',
    };
    expect(entry.workspace_adapter_id).toBe('claude');
    expect(entry.command_adapter_id).toBe('custom-adapter:test');
    expect(entry.adapter).toBe('claude');
    expect(entry.adapter_id).toBe('claude');
  });
});

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

  it('RuntimeProfileEntry carries canonical and deprecated fields in list response', async () => {
    const mockResponse = {
      profiles: [
        {
          name: 'openclaw',
          command: 'openclaw',
          adapter: 'pi',
          adapter_id: 'pi',
          workspace_adapter_id: 'pi',
          command_adapter_id: 'generic-cli',
          command_adapter: 'generic-cli',
          present: true,
          path: '/usr/local/bin/openclaw',
          envelope_policy: null,
        },
        {
          name: 'legacy-runner',
          command: 'legacy-runner',
          adapter: 'pi',
          adapter_id: 'pi',
          workspace_adapter_id: 'pi',
          command_adapter_id: 'generic-cli',
          command_adapter: 'generic-cli',
          present: false,
          path: null,
          envelope_policy: null,
        },
      ],
    } satisfies import('./runtime-executors').RuntimeProfileList;
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue(mockResponse);
    const result = await runtimeExecutors.listRuntimeProfiles();
    expect(spy).toHaveBeenCalledWith('/executors/runtime/profiles');
    // Assert exact field contract: canonical fields present, deprecated aliases preserved
    expect(result.profiles).toHaveLength(2);
    expect(result.profiles[0].workspace_adapter_id).toBe('pi');
    expect(result.profiles[0].command_adapter_id).toBe('generic-cli');
    expect(result.profiles[0].adapter).toBe('pi'); // deprecated alias preserved
    expect(result.profiles[0].adapter_id).toBe('pi'); // deprecated alias preserved
    expect(result.profiles[0].command_adapter).toBe('generic-cli'); // deprecated alias preserved
    expect(result.profiles[1].workspace_adapter_id).toBe('pi');
    expect(result.profiles[1].command_adapter_id).toBe('generic-cli');
    expect(result.profiles[1].adapter).toBe('pi');
    expect(result.profiles[1].adapter_id).toBe('pi');
    expect(result.profiles[1].command_adapter).toBe('generic-cli');
  });

  it('RuntimeProfileEntry type assertion carries canonical fields', async () => {
    // This is a compile-time-only type assertion test.
    // If RuntimeProfileEntry does not have workspace_adapter_id, tsc fails.
    const entry: import('./runtime-executors').RuntimeProfileEntry = {
      name: 'test',
      command: 'test-cli',
      adapter: 'claude',
      adapter_id: 'claude',
      workspace_adapter_id: 'claude',
      command_adapter_id: 'generic-cli',
      command_adapter: 'generic-cli',
      present: true,
      path: '/usr/bin/test-cli',
      envelope_policy: null,
    };
    expect(entry.workspace_adapter_id).toBe('claude');
    expect(entry.command_adapter_id).toBe('generic-cli');
    expect(entry.command_adapter).toBe('generic-cli');
    expect(entry.adapter).toBe('claude');
    expect(entry.adapter_id).toBe('claude');
  });
});

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

  it('RuntimeProfileEntry carries command_adapter field in list response', async () => {
    const mockResponse = {
      profiles: [
        {
          name: 'openclaw',
          command: 'openclaw',
          adapter: 'pi',
          command_adapter: 'generic-cli',
          present: true,
          path: '/usr/local/bin/openclaw',
        },
        {
          name: 'legacy-runner',
          command: 'legacy-runner',
          adapter: 'pi',
          command_adapter: 'generic-cli',
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
    // Assert exact field contract: command_adapter is present and
    // adapter is independent (workspace-only).
    expect(result.profiles).toHaveLength(2);
    expect(result.profiles[0].command_adapter).toBe('generic-cli');
    expect(result.profiles[0].adapter).toBe('pi'); // workspace adapter unchanged
    expect(result.profiles[1].command_adapter).toBe('generic-cli');
    expect(result.profiles[1].adapter).toBe('pi');
  });

  it('RuntimeProfileEntry type assertion requires command_adapter', async () => {
    // This is a compile-time-only type assertion test.
    // If RuntimeProfileEntry does not have command_adapter, tsc fails.
    const entry: import('./runtime-executors').RuntimeProfileEntry = {
      name: 'test',
      command: 'test-cli',
      adapter: 'claude',
      command_adapter: 'generic-cli',
      present: true,
      path: '/usr/bin/test-cli',
    };
    expect(entry.command_adapter).toBe('generic-cli');
    expect(entry.adapter).toBe('claude');
  });
});

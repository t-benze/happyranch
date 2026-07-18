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
});

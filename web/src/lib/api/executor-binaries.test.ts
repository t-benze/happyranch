import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as executorBinaries from './executor-binaries';
import * as clientModule from './client';

describe('executor-binaries api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listExecutorBinaries GETs the registry with no params', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ entries: [] });
    await executorBinaries.listExecutorBinaries();
    expect(spy).toHaveBeenCalledWith('/executor-binaries');
  });

  it('registerExecutorBinary POSTs kind + path', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ kind: 'claude', path: '/usr/bin/claude', valid: true });
    await executorBinaries.registerExecutorBinary({
      kind: 'claude',
      path: '/usr/bin/claude',
    });
    expect(spy).toHaveBeenCalledWith('/executor-binaries/register', {
      method: 'POST',
      body: { kind: 'claude', path: '/usr/bin/claude' },
    });
  });

  it('validateExecutorBinary POSTs the path only', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ path: '/nope', valid: false, error: 'not found' });
    await executorBinaries.validateExecutorBinary({ path: '/nope' });
    expect(spy).toHaveBeenCalledWith('/executor-binaries/validate', {
      method: 'POST',
      body: { path: '/nope' },
    });
  });

  it('exposes the four built-in executor kinds', () => {
    expect(executorBinaries.EXECUTOR_BINARY_KINDS).toEqual([
      'claude',
      'codex',
      'pi',
      'opencode',
    ]);
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as tokens from './tokens';
import * as clientModule from './client';

describe('tokens api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listTokens builds the right URL with no params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ rows: [] });
    await tokens.listTokens('test');
    expect(spy).toHaveBeenCalledWith('/orgs/test/tokens', { params: undefined });
  });

  it('listTokens forwards filter params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ rollup: [] });
    await tokens.listTokens('test', { group_by: 'task', agent: 'dev' });
    expect(spy).toHaveBeenCalledWith('/orgs/test/tokens', {
      params: { group_by: 'task', agent: 'dev' },
    });
  });

  it('listFailedTaskTokens pins group_by=failed_task', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ rollup: [] });
    await tokens.listFailedTaskTokens('test');
    expect(spy).toHaveBeenCalledWith('/orgs/test/tokens', {
      params: { group_by: 'failed_task' },
    });
  });

  it('listFailedTaskTokens AND-composes other filters', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ rollup: [] });
    await tokens.listFailedTaskTokens('test', { agent: 'qa', since: '2026-06-01' });
    expect(spy).toHaveBeenCalledWith('/orgs/test/tokens', {
      params: { agent: 'qa', since: '2026-06-01', group_by: 'failed_task' },
    });
  });
});

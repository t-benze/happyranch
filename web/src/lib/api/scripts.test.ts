import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as scripts from './scripts';
import * as clientModule from './client';

describe('scripts api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listScripts builds the right URL with no params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ scripts: [] });
    await scripts.listScripts('test');
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/', { params: undefined });
  });

  it('listScripts forwards filter params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ scripts: [] });
    await scripts.listScripts('test', { status: 'pending', agent: 'a', limit: 10 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/', {
      params: { status: 'pending', agent: 'a', limit: 10 },
    });
  });

  it('getScript fetches detail', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001' });
    await scripts.getScript('test', 'SR-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001');
  });

  it('runScript POSTs body', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001', status: 'running' });
    await scripts.runScript('test', 'SR-001', { timeout_seconds: 600 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001/run', {
      method: 'POST',
      body: { timeout_seconds: 600 },
    });
  });

  it('rejectScript POSTs reason', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001', status: 'rejected' });
    await scripts.rejectScript('test', 'SR-001', { reason: 'no' });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001/reject', {
      method: 'POST',
      body: { reason: 'no' },
    });
  });

  it('getScriptOutput forwards stream and max_bytes', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ stdout: '', stderr: '' });
    await scripts.getScriptOutput('test', 'SR-001', { stream: 'stdout', max_bytes: 1024 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001/output', {
      params: { stream: 'stdout', max_bytes: 1024 },
    });
  });

  it('scriptEventsPath returns SSE path', () => {
    expect(scripts.scriptEventsPath('test', 'SR-001')).toBe('/orgs/test/scripts/SR-001/events');
  });
});

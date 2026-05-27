import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as jobs from './jobs';
import * as clientModule from './client';

describe('jobs api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listJobs builds the right URL with no params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ jobs: [] });
    await jobs.listJobs('test');
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/', { params: undefined });
  });

  it('listJobs forwards filter params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ jobs: [] });
    await jobs.listJobs('test', {
      status: 'pending',
      agent: 'a',
      review_required: 'true',
      persistent: 'false',
      limit: 10,
    });
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/', {
      params: {
        status: 'pending',
        agent: 'a',
        review_required: 'true',
        persistent: 'false',
        limit: 10,
      },
    });
  });

  it('getJob fetches detail', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'JOB-001' });
    await jobs.getJob('test', 'JOB-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001');
  });

  it('runJob POSTs body', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'JOB-001', status: 'running' });
    await jobs.runJob('test', 'JOB-001', { timeout_seconds: 600 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/run', {
      method: 'POST',
      body: { timeout_seconds: 600 },
    });
  });

  it('rejectJob POSTs reason', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'JOB-001', status: 'rejected' });
    await jobs.rejectJob('test', 'JOB-001', { reason: 'no' });
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/reject', {
      method: 'POST',
      body: { reason: 'no' },
    });
  });

  it('getJobOutput forwards stream and max_bytes', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ stdout: '', stderr: '' });
    await jobs.getJobOutput('test', 'JOB-001', { stream: 'stdout', max_bytes: 1024 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/output', {
      params: { stream: 'stdout', max_bytes: 1024 },
    });
  });

  it('jobEventsPath returns SSE path', () => {
    expect(jobs.jobEventsPath('test', 'JOB-001')).toBe('/orgs/test/jobs/JOB-001/events');
  });

  it('tailJob defaults stream=stdout and lines=50', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ stream: 'stdout', lines: [] });
    await jobs.tailJob('test', 'JOB-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/tail', {
      params: { stream: 'stdout', lines: 50 },
    });
  });

  it('tailJob forwards explicit stream and lines', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ stream: 'stderr', lines: [] });
    await jobs.tailJob('test', 'JOB-001', { stream: 'stderr', lines: 200 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/tail', {
      params: { stream: 'stderr', lines: 200 },
    });
  });

  it('waitJob POSTs with default timeout_seconds=30', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ timed_out: true });
    await jobs.waitJob('test', 'JOB-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/wait', {
      method: 'POST',
      params: { timeout_seconds: 30 },
    });
  });

  it('waitJob forwards explicit timeout_seconds', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ timed_out: true });
    await jobs.waitJob('test', 'JOB-001', 120);
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/wait', {
      method: 'POST',
      params: { timeout_seconds: 120 },
    });
  });

  it('stopJob POSTs with no body', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ ok: true, id: 'JOB-001' });
    await jobs.stopJob('test', 'JOB-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/jobs/JOB-001/stop', { method: 'POST' });
  });
});

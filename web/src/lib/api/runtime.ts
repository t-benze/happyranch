/** Mirror of src/daemon/routes/runtime.py */
import { request } from './client';

export interface RuntimeInfo {
  root: string | null;
  registered: { path: string; active: boolean }[];
}

export const getRuntime = (): Promise<RuntimeInfo> => request('/runtime');

export const initRuntime = (body: { path: string }): Promise<RuntimeInfo> =>
  request('/runtime', { method: 'POST', body });

export const useRuntime = (body: { path: string }): Promise<RuntimeInfo> =>
  request('/runtime/use', { method: 'POST', body });

/** Founder-only one-off operation. Transport loss must not automatically retry. */
export const runManualDiskSweepBacklog = (): Promise<{
  summary_path: string;
  report: Record<string, unknown>;
}> => request('/runtime/disk-sweep/manual-backlog', { method: 'POST', replayOnUnauthorized: false });

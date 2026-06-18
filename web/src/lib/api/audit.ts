/** Mirror of src/daemon/routes/audit.py */
import { request } from './client';
import type { AuditEntry } from './types';

export type { AuditEntry } from './types';

export const listAudit = (
  slug: string,
  params?: {
    task_id?: string;
    agent?: string;
    action?: string;
    since?: string;
    limit?: number;
    /** Enrich Thread-scope entries with _thread_dream_id (A4 marker). */
    include_thread_origin?: boolean;
  },
): Promise<{ entries: AuditEntry[] }> =>
  request(`/orgs/${slug}/audit`, {
    params: { ...params, include_thread_origin: params?.include_thread_origin ?? true },
  });

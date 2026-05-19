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
  },
): Promise<{ entries: AuditEntry[] }> =>
  request(`/orgs/${slug}/audit`, { params });

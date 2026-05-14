/** Mirror of src/daemon/routes/audit.py */
import { request } from './client';

export interface AuditEntry {
  id: number;
  task_id: string | null;
  session_id: string | null;
  agent: string | null;
  action: string;
  payload: Record<string, unknown>;
  created_at: string;
}

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

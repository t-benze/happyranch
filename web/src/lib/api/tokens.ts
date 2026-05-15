/** Mirror of src/daemon/routes/tokens.py */
import { request } from './client';

export interface TokenUsageEntry {
  session_id: string;
  task_id: string | null;
  agent: string;
  input: number;
  output: number;
  cache_read: number;
  cache_creation: number;
  reasoning: number;
  total: number;
  created_at: string;
}

export const listTokens = (
  slug: string,
  params?: {
    task_id?: string;
    agent?: string;
    since?: string;
    limit?: number;
    by_agent?: boolean;
    by_task?: boolean;
  },
): Promise<{ entries: TokenUsageEntry[] }> =>
  request(`/orgs/${slug}/tokens`, { params });

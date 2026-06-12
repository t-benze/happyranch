/** Mirror of src/daemon/routes/tokens.py */
import { request } from './client';

export interface TokenUsageEntry {
  session_id: string;
  task_id: string | null;
  scope_type: 'task' | 'thread' | 'talk';
  scope_id: string | null;
  thread_id: string | null;
  talk_id: string | null;
  invocation_purpose: string | null;
  agent: string;
  executor: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_creation_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number;
  created_at: string;
}

export interface TokenUsageRollup {
  agent?: string;
  task_id?: string | null;
  scope_type?: 'task' | 'thread' | 'talk';
  scope_id?: string | null;
  thread_id?: string;
  talk_id?: string;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
}

export type TokenUsageGroupBy =
  | 'agent'
  | 'task'
  | 'failed_task'
  | 'scope'
  | 'thread'
  | 'talk';

export interface ListTokensParams {
  task_id?: string;
  agent?: string;
  since?: string;
  limit?: number;
  group_by?: TokenUsageGroupBy;
  scope_type?: 'task' | 'thread' | 'talk';
  scope_id?: string;
  thread_id?: string;
  talk_id?: string;
  purpose?: string;
}

export const listTokens = (
  slug: string,
  params?: ListTokensParams,
): Promise<{ rows: TokenUsageEntry[] } | { rollup: TokenUsageRollup[] }> =>
  request(`/orgs/${slug}/tokens`, { params: params ? { ...params } : undefined });

/** Mirror of src/daemon/routes/tokens.py */
import { request } from './client';

export interface TokenUsageEntry {
  session_id: string;
  task_id: string | null;
  scope_type: 'task' | 'thread';
  scope_id: string | null;
  thread_id: string | null;
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
  scope_type?: 'task' | 'thread';
  scope_id?: string | null;
  thread_id?: string;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  // Model-classification primitives — emitted ONLY by the by-agent/by-thread/
  // by-scope/by-purpose omit them, so all seven are optional. A presentation
  // layer derives the Model label from these via the spec §2/§6 precedence;
  // token totals never depend on them.
  model_distinct?: number;
  model_any?: string | null;
  non_null_sessions?: number;
  null_codex_sessions?: number;
  null_claude_sessions?: number;
  null_claude_min_created_at?: string | null;
  null_claude_max_created_at?: string | null;
}

export type TokenUsageGroupBy =
  | 'agent'
  | 'task'
  | 'failed_task'
  | 'scope'
  | 'thread'
  | 'purpose';

export interface ListTokensParams {
  task_id?: string;
  agent?: string;
  since?: string;
  limit?: number;
  group_by?: TokenUsageGroupBy;
  scope_type?: 'task' | 'thread';
  scope_id?: string;
  thread_id?: string;
  purpose?: string;
}

export const listTokens = (
  slug: string,
  params?: ListTokensParams,
): Promise<{ rows: TokenUsageEntry[] } | { rollup: TokenUsageRollup[] }> =>
  request(`/orgs/${slug}/tokens`, { params: params ? { ...params } : undefined });

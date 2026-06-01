/** Mirror of src/daemon/routes/threads.py — founder-facing surface only.
 *
 * Excluded (agent invocation-token only): POST /threads/{id}/reply,
 * POST /threads/{id}/decline, POST /threads/{id}/dispatch,
 * POST /threads/{id}/close-out. See spec §2 + §7.4.
 */
import { request } from './client';
import type {
  ThreadDetailResponse,
  ThreadMessage,
  ThreadRecord,
} from './types';

export interface ComposeThreadBody {
  subject: string;
  recipients: string[];
  body_markdown: string;
  forwarded_from_id?: string;
  forwarded_from_kind?: 'thread' | 'talk';
}

export const composeThread = (
  slug: string,
  body: ComposeThreadBody,
): Promise<{ thread_id: string; started_at: string; pending_replies: number }> =>
  request(`/orgs/${slug}/threads`, { method: 'POST', body });

export const listThreads = (
  slug: string,
  params?: { status?: string; limit?: number },
): Promise<{ threads: ThreadRecord[] }> =>
  request(`/orgs/${slug}/threads`, { params });

export const getThread = (
  slug: string,
  threadId: string,
): Promise<ThreadDetailResponse> =>
  request(`/orgs/${slug}/threads/${threadId}`);

export const listThreadMessages = (
  slug: string,
  threadId: string,
  params?: { since_seq?: number; limit?: number },
): Promise<{ messages: ThreadMessage[] }> =>
  request(`/orgs/${slug}/threads/${threadId}/messages`, { params });

export const sendThreadFollowUp = (
  slug: string,
  threadId: string,
  body: { body_markdown: string },
): Promise<{ seq: number; thread_id: string }> =>
  request(`/orgs/${slug}/threads/${threadId}/send`, { method: 'POST', body });

export const inviteToThread = (
  slug: string,
  threadId: string,
  body: { agent_name: string },
): Promise<{ thread_id: string; agent_name: string; system_message_seq: number }> =>
  request(`/orgs/${slug}/threads/${threadId}/invite`, { method: 'POST', body });

export const extendThreadCap = (
  slug: string,
  threadId: string,
  body: { new_cap: number },
): Promise<{ thread_id: string; turn_cap: number }> =>
  request(`/orgs/${slug}/threads/${threadId}/extend`, { method: 'POST', body });

export const archiveThread = (
  slug: string,
  threadId: string,
  body: { summary: string },
): Promise<{ thread_id: string; status: string }> =>
  request(`/orgs/${slug}/threads/${threadId}/archive`, { method: 'POST', body });

export const resumeThread = (
  slug: string,
  threadId: string,
): Promise<{ thread_id: string; status: string; idempotent?: boolean }> =>
  request(`/orgs/${slug}/threads/${threadId}/resume`, { method: 'POST' });

export const abandonThread = (
  slug: string,
  threadId: string,
  body: { reason: string },
): Promise<{ thread_id: string; status: string }> =>
  request(`/orgs/${slug}/threads/${threadId}/abandon`, { method: 'POST', body });

// SSE paths — pass to subscribeSSE
export const threadInboxEventsPath = (slug: string): string =>
  `/orgs/${slug}/threads/events`;

export const threadTailPath = (
  slug: string,
  threadId: string,
  sinceSeq: number,
): { path: string; query: { since_seq: number } } => ({
  path: `/orgs/${slug}/threads/${threadId}/tail`,
  query: { since_seq: sinceSeq },
});

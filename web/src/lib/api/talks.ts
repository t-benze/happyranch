/** Mirror of src/daemon/routes/talks.py.
 *
 * All talk endpoints are founder-facing (talks are 1:1 founder↔agent), so all
 * are exposed in the TS client.
 */
import { request } from './client';
import type { TalkRecord } from './types';

export interface StartTalkResponse {
  talk_id: string;
  started_at: string;
}

export const startTalk = (
  slug: string,
  body: { agent_name: string },
): Promise<StartTalkResponse> =>
  request(`/orgs/${slug}/talks`, { method: 'POST', body });

export const resumeTalk = (
  slug: string,
  talkId: string,
): Promise<StartTalkResponse> =>
  request(`/orgs/${slug}/talks/${talkId}/resume`, { method: 'POST' });

export const abandonTalk = (
  slug: string,
  talkId: string,
  body: { reason: string },
): Promise<{ talk_id: string; status: 'abandoned' }> =>
  request(`/orgs/${slug}/talks/${talkId}/abandon`, { method: 'POST', body });

export interface EndTalkLearning {
  text: string;
}

export interface EndTalkBody {
  summary: string;
  topic_list?: string[];
  transcript_markdown: string;
  learnings?: EndTalkLearning[];
  kb_slugs?: string[];
}

export interface EndTalkResponse {
  talk_id: string;
  status: 'closed';
  transcript_path: string;
  new_learnings_count: number;
}

export const endTalk = (
  slug: string,
  talkId: string,
  body: EndTalkBody,
): Promise<EndTalkResponse> =>
  request(`/orgs/${slug}/talks/${talkId}/end`, { method: 'POST', body });

export const listTalks = (
  slug: string,
  params?: { agent?: string; status?: string; limit?: number },
): Promise<{ talks: TalkRecord[] }> =>
  request(`/orgs/${slug}/talks`, { params });

export const getTalk = (slug: string, talkId: string): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks/${talkId}`);

export interface DispatchFromTalkBody {
  brief: string;
  target_agent?: string;
  team?: string;
}

export interface DispatchFromTalkResponse {
  task_id: string;
  team: string;
  assigned_agent: string;
  dispatched_from_talk_id: string;
}

export const dispatchFromTalk = (
  slug: string,
  talkId: string,
  body: DispatchFromTalkBody,
): Promise<DispatchFromTalkResponse> =>
  request(`/orgs/${slug}/talks/${talkId}/dispatch`, { method: 'POST', body });

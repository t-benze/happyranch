/** Mirror of src/daemon/routes/talks.py.
 *
 * All talk endpoints are founder-facing (talks are 1:1 founder↔agent), so all
 * are exposed in the TS client.
 */
import { request } from './client';
import type { TalkRecord } from './types';

export const startTalk = (
  slug: string,
  body: { agent: string },
): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks`, { method: 'POST', body });

export const resumeTalk = (
  slug: string,
  talkId: string,
): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks/${talkId}/resume`, { method: 'POST' });

export const abandonTalk = (
  slug: string,
  talkId: string,
  body?: { reason?: string },
): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks/${talkId}/abandon`, { method: 'POST', body });

export const endTalk = (
  slug: string,
  talkId: string,
  body: Record<string, unknown>,
): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks/${talkId}/end`, { method: 'POST', body });

export const listTalks = (
  slug: string,
  params?: { agent?: string; limit?: number },
): Promise<{ talks: TalkRecord[] }> =>
  request(`/orgs/${slug}/talks`, { params });

export const getTalk = (slug: string, talkId: string): Promise<TalkRecord> =>
  request(`/orgs/${slug}/talks/${talkId}`);

export const dispatchFromTalk = (
  slug: string,
  talkId: string,
  body: Record<string, unknown>,
): Promise<{ task_id: string }> =>
  request(`/orgs/${slug}/talks/${talkId}/dispatch`, { method: 'POST', body });

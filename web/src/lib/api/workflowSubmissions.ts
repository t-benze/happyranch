/** Agent proof is server-enforced; the shared bearer transport remains unchanged. */
import { request } from './client';
import type { AuthorHandoffCommand, AuthorHandoffProof, AuthorHandoffReceipt } from './types';

export const stageAuthorHandoff = (slug: string, sessionId: string, body: AuthorHandoffCommand): Promise<AuthorHandoffReceipt> =>
  request(`/orgs/${slug}/workflows/submissions/handoffs`, { method: 'POST', params: { session_id: sessionId }, body });

export const getAuthorHandoff = (slug: string, handoffId: string, proof: AuthorHandoffProof, signal?: AbortSignal): Promise<AuthorHandoffReceipt> =>
  request(`/orgs/${slug}/workflows/submissions/handoffs/${encodeURIComponent(handoffId)}`, { params: proof, signal });

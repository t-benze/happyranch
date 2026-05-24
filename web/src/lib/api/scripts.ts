/** Mirror of src/daemon/routes/scripts.py — founder-facing surface only.
 *
 * Excluded (agent callback): POST /scripts/submit.
 */
import { request } from './client';
import type {
  ScriptRequest,
  ScriptOutput,
  ScriptRunResponse,
  ScriptListResponse,
} from './types';

export const listScripts = (
  slug: string,
  params?: { status?: string; agent?: string; task_id?: string; limit?: number },
): Promise<ScriptListResponse> =>
  request(`/orgs/${slug}/scripts/`, { params });

export const getScript = (slug: string, sr_id: string): Promise<ScriptRequest> =>
  request(`/orgs/${slug}/scripts/${sr_id}`);

export const runScript = (
  slug: string,
  sr_id: string,
  body: { cwd_override?: string; timeout_seconds?: number },
): Promise<ScriptRunResponse> =>
  request(`/orgs/${slug}/scripts/${sr_id}/run`, { method: 'POST', body });

export const rejectScript = (
  slug: string,
  sr_id: string,
  body: { reason: string },
): Promise<ScriptRequest> =>
  request(`/orgs/${slug}/scripts/${sr_id}/reject`, { method: 'POST', body });

export const getScriptOutput = (
  slug: string,
  sr_id: string,
  params?: { stream?: 'stdout' | 'stderr' | 'both'; max_bytes?: number },
): Promise<ScriptOutput> =>
  request(`/orgs/${slug}/scripts/${sr_id}/output`, { params });

export const scriptEventsPath = (slug: string, sr_id: string): string =>
  `/orgs/${slug}/scripts/${sr_id}/events`;

/** Mirror of src/daemon/routes/tasks.py.
 *
 * Excluded (agent-subprocess-only): POST /tasks/{id}/completion,
 * POST /tasks/{id}/progress. See spec §2.
 */
import { API_PREFIX, request } from './client';
import type {
  TaskAttachmentRecord,
  TaskAttachmentRef,
  TaskDetailResponse,
  TaskRecallNode,
  TaskRecord,
} from './types';

export type TaskListItem = TaskRecord;

export const submitTask = (
  slug: string,
  body: {
    team?: string;
    brief: string;
    owner?: string;
    attachments?: TaskAttachmentRef[];
  },
): Promise<TaskRecord> =>
  request(`/orgs/${slug}/tasks`, { method: 'POST', body });

export const listTasks = (
  slug: string,
  params?: {
    limit?: number;
    status?: string;
    assigned_agent?: string;
    before?: string;
    blocked_on_job_id?: string;
  },
): Promise<{ tasks: TaskListItem[]; next_cursor?: string | null }> =>
  request(`/orgs/${slug}/tasks`, { params });

export const listTaskRoots = (
  slug: string,
  params?: {
    limit?: number;
    status?: string;
    assigned_agent?: string;
    before?: string;
    blocked_on_job_id?: string;
  },
): Promise<{ tasks: TaskListItem[]; next_cursor?: string | null }> =>
  request(`/orgs/${slug}/tasks/roots`, { params });

export const getTask = (slug: string, taskId: string): Promise<TaskDetailResponse> =>
  request(`/orgs/${slug}/tasks/${taskId}`);

export const recallTask = (
  slug: string,
  taskId: string,
  params?: { tree?: boolean; include_output?: boolean },
): Promise<TaskRecallNode> =>
  request(`/orgs/${slug}/tasks/${taskId}/recall`, { params });

export const resolveEscalation = (
  slug: string,
  taskId: string,
  body: { decision: 'supersede' | 'continue'; rationale?: string; brief?: string },
): Promise<Record<string, unknown>> =>
  request(`/orgs/${slug}/tasks/${taskId}/resolve-escalation`, {
    method: 'POST',
    body: { ...body, rationale: body.rationale ?? '', brief: body.brief ?? '' },
  });

export const revisitTask = (
  slug: string,
  taskId: string,
  body: { founder_note?: string; session_timeout_seconds?: number },
): Promise<TaskRecord> =>
  request(`/orgs/${slug}/tasks/${taskId}/revisit`, { method: 'POST', body });

export const cancelTask = (
  slug: string,
  taskId: string,
  body?: { rationale?: string },
): Promise<Record<string, unknown>> =>
  request(`/orgs/${slug}/tasks/${taskId}/cancel`, { method: 'POST', body });

/** SSE path — pass to subscribeSSE. */
export const taskEventsPath = (slug: string, taskId: string): string =>
  `/orgs/${slug}/tasks/${taskId}/events`;

// ── Task attachments (THR-109) ───────────────────────────────────────────────

/** Upload a file to the task-attachment private store using multipart.
 *  Uses direct fetch to avoid the JSON Content-Type default on the shared
 *  request() helper (which would break multipart body encoding). */
export const uploadTaskAttachment = async (
  slug: string,
  file: File,
  agent: string = 'founder',
): Promise<{
  storage_key: string;
  display_name: string;
  size_bytes: number;
  content_type: string | null;
  uploaded_by: string;
}> => {
  const { getToken } = await import('../auth');
  const token = await getToken();
  const params = new URLSearchParams({ agent });
  const form = new FormData();
  form.set('file', file, file.name);
  const res = await fetch(
    `${API_PREFIX}/orgs/${slug}/tasks/attachments?${params.toString()}`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
      body: form,
      credentials: 'same-origin',
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const code = (body as Record<string, unknown> | null)?.code;
    const err: Error & { status: number } = Object.assign(
      new Error(`Upload failed: ${res.status}${code ? ` (${code})` : ''}`),
      { status: res.status },
    );
    throw err;
  }
  return res.json();
};

export const listTaskAttachments = (
  slug: string,
  taskId: string,
  includeInherited: boolean = false,
): Promise<{ task_id: string; attachments: TaskAttachmentRecord[] }> =>
  request(`/orgs/${slug}/tasks/${taskId}/attachments`, {
    params: includeInherited ? { include_inherited: true } : undefined,
  });

export const downloadTaskAttachmentUrl = (
  slug: string,
  taskId: string,
  storageKey: string,
): string =>
  `/orgs/${slug}/tasks/${taskId}/attachments/${encodeURIComponent(storageKey)}`;

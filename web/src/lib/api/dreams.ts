import { request } from './client'

export interface DreamRecord {
  dream_id: string
  agent_name: string
  local_date: string
  scheduled_for: string
  window_start: string | null
  window_end: string
  started_at: string | null
  ended_at: string | null
  status: string
  summary: string | null
  transcript_path: string | null
  new_learnings_count: number
  kb_candidate_count: number
  founder_thread_id: string | null
  error: string | null
}

export interface DreamStatusResponse {
  recent: DreamRecord[]
}

export interface DreamListResponse {
  dreams: DreamRecord[]
}

export interface DreamDetailResponse extends DreamRecord {
  transcript?: string
  kb_candidates?: unknown[]
}

export async function getDreamStatus(
  org: string,
  params: { agent?: string } = {},
): Promise<DreamStatusResponse> {
  return request<DreamStatusResponse>(`/orgs/${org}/dreams/status`, {
    params: { agent: params.agent },
  })
}

export async function listDreams(
  org: string,
  params: { agent?: string; limit?: number } = {},
): Promise<DreamListResponse> {
  return request<DreamListResponse>(`/orgs/${org}/dreams`, {
    params: { agent: params.agent, limit: params.limit },
  })
}

export async function getDream(org: string, dreamId: string): Promise<DreamDetailResponse> {
  return request<DreamDetailResponse>(`/orgs/${org}/dreams/${dreamId}`)
}

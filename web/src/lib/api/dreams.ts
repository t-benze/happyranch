import { apiFetch } from './client'

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
  const qs = new URLSearchParams()
  if (params.agent) qs.set('agent', params.agent)
  return apiFetch<DreamStatusResponse>(`/api/v1/orgs/${org}/dreams/status?${qs}`)
}

export async function listDreams(
  org: string,
  params: { agent?: string; limit?: number } = {},
): Promise<DreamListResponse> {
  const qs = new URLSearchParams()
  if (params.agent) qs.set('agent', params.agent)
  if (params.limit) qs.set('limit', String(params.limit))
  return apiFetch<DreamListResponse>(`/api/v1/orgs/${org}/dreams?${qs}`)
}

export async function getDream(org: string, dreamId: string): Promise<DreamDetailResponse> {
  return apiFetch<DreamDetailResponse>(`/api/v1/orgs/${org}/dreams/${dreamId}`)
}

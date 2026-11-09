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

export interface DreamListRecord extends DreamRecord {
  pending_kb_candidate_count: number
  pending_kb_candidates?: DreamKbCandidate[]
}

export interface DreamListParams {
  agent?: string
  limit?: number
  include_candidates?: 'pending'
}

export interface DreamListResponse {
  dreams: DreamListRecord[]
}

export interface DreamDetailResponse extends DreamRecord {
  transcript?: string
  kb_candidates?: DreamKbCandidate[]
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
  params: DreamListParams = {},
): Promise<DreamListResponse> {
  return request<DreamListResponse>(`/orgs/${org}/dreams`, {
    params: { agent: params.agent, limit: params.limit, include_candidates: params.include_candidates },
  })
}

export async function getDream(org: string, dreamId: string): Promise<DreamDetailResponse> {
  return request<DreamDetailResponse>(`/orgs/${org}/dreams/${dreamId}`)
}

export interface DreamKbCandidate {
  id: number
  dream_id: string
  agent_name: string
  slug: string
  title: string
  topic: string
  rationale: string
  body_markdown: string
  status: string
  promoted_kb_slug: string | null
  created_at: string
  updated_at: string
}

export async function acceptDreamKbCandidate(
  org: string,
  candidateId: number,
): Promise<DreamKbCandidate> {
  return request<DreamKbCandidate>(
    `/orgs/${org}/dreams/candidates/${candidateId}/accept`,
    { method: 'POST' },
  )
}

export async function dismissDreamKbCandidate(
  org: string,
  candidateId: number,
): Promise<DreamKbCandidate> {
  return request<DreamKbCandidate>(
    `/orgs/${org}/dreams/candidates/${candidateId}/dismiss`,
    { method: 'POST' },
  )
}

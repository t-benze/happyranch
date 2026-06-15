import { request } from './client'

export interface WorkHourRecord {
  work_hour_id: string
  agent_name: string
  local_date: string
  slot: string
  mode: string
  scheduled_for: string
  started_at: string | null
  ended_at: string | null
  status: string
  routine_count: number
  spawned_task_ids: string[]
  spawned_task_count: number
  summary: string | null
  transcript_path: string | null
  session_id: string | null
  error: string | null
  created_at: string
}

export interface WorkHourStatusResponse {
  recent: WorkHourRecord[]
}

export interface WorkHourListResponse {
  work_hours: WorkHourRecord[]
}

export async function getWorkHoursStatus(
  org: string,
  params: { agent?: string } = {},
): Promise<WorkHourStatusResponse> {
  return request<WorkHourStatusResponse>(`/orgs/${org}/work-hours/status`, {
    params: { agent: params.agent },
  })
}

export async function listWorkHours(
  org: string,
  params: { agent?: string; limit?: number } = {},
): Promise<WorkHourListResponse> {
  return request<WorkHourListResponse>(`/orgs/${org}/work-hours`, {
    params: { agent: params.agent, limit: params.limit },
  })
}

export async function getWorkHour(org: string, workHourId: string): Promise<WorkHourRecord> {
  return request<WorkHourRecord>(`/orgs/${org}/work-hours/${workHourId}`)
}

import { request } from './client'
import type { WorkHourRecord, WorkHourListResponse, WorkHourStatusResponse } from './types'

export type { WorkHourRecord, WorkHourListResponse, WorkHourStatusResponse }

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

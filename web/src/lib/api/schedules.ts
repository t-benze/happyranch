import { request } from './client'
import type {
  ScheduleRecord,
  ScheduleListResponse,
  ScheduleEditFields,
} from './types'

export type { ScheduleRecord, ScheduleListResponse, ScheduleEditFields }

export async function listSchedules(
  org: string,
  params: {
    agent?: string
    status?: string
    limit?: number
  } = {},
): Promise<ScheduleListResponse> {
  return request<ScheduleListResponse>(`/orgs/${org}/schedules`, {
    params: { agent: params.agent, status: params.status, limit: params.limit },
  })
}

export async function getSchedule(
  org: string,
  scheduleId: string,
): Promise<ScheduleRecord> {
  return request<ScheduleRecord>(`/orgs/${org}/schedules/${scheduleId}`)
}

export async function pauseSchedule(
  org: string,
  scheduleId: string,
): Promise<ScheduleRecord> {
  return request<ScheduleRecord>(`/orgs/${org}/schedules/${scheduleId}/pause`, {
    method: 'POST',
  })
}

export async function cancelSchedule(
  org: string,
  scheduleId: string,
): Promise<ScheduleRecord> {
  return request<ScheduleRecord>(
    `/orgs/${org}/schedules/${scheduleId}/cancel`,
    { method: 'POST' },
  )
}

export async function editSchedule(
  org: string,
  scheduleId: string,
  fields: ScheduleEditFields,
): Promise<ScheduleRecord> {
  return request<ScheduleRecord>(`/orgs/${org}/schedules/${scheduleId}`, {
    method: 'PATCH',
    body: fields,
  })
}

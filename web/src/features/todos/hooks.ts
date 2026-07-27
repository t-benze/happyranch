/**
 * Todos hooks — React Query wrappers over the shipped schedules API client.
 * All data-fetching is on the browser-callable REST /api surface; no new
 * backend or contract surface is introduced.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes schedule mutations; THR-011 option 3
import {
  listSchedules,
  getSchedule,
  pauseSchedule,
  cancelSchedule,
  editSchedule,
  type ScheduleRecord,
  type ScheduleEditFields,
} from '@/lib/api/schedules'

export type { ScheduleRecord, ScheduleEditFields }
export { listSchedules, getSchedule, pauseSchedule, cancelSchedule, editSchedule }

/** Query key factory scoped to the active org. */
export const scheduleKeys = {
  all: (org: string) => ['schedules', org] as const,
  list: (org: string, filters: Record<string, unknown>) =>
    [...scheduleKeys.all(org), 'list', filters] as const,
  detail: (org: string, id: string) =>
    [...scheduleKeys.all(org), 'detail', id] as const,
}

export interface TodoListFilters {
  agent?: string
  status?: string
  limit?: number
}

export function useTodoList(org: string, filters: TodoListFilters = {}) {
  return useQuery({
    queryKey: scheduleKeys.list(org, filters as Record<string, unknown>),
    queryFn: () =>
      listSchedules(org, {
        agent: filters.agent,
        status: filters.status,
        limit: filters.limit,
      }),
    enabled: !!org,
    staleTime: 30_000,
  })
}

export function useTodoDetail(org: string, scheduleId: string) {
  return useQuery({
    queryKey: scheduleKeys.detail(org, scheduleId),
    queryFn: () => getSchedule(org, scheduleId),
    enabled: !!org && !!scheduleId,
    staleTime: 15_000,
  })
}

export function usePauseTodo(org: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (scheduleId: string) => pauseSchedule(org, scheduleId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scheduleKeys.all(org) })
    },
  })
}

export function useCancelTodo(org: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (scheduleId: string) => cancelSchedule(org, scheduleId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scheduleKeys.all(org) })
    },
  })
}

export function useEditTodo(org: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      scheduleId,
      fields,
    }: {
      scheduleId: string
      fields: ScheduleEditFields
    }) => editSchedule(org, scheduleId, fields),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scheduleKeys.all(org) })
    },
  })
}

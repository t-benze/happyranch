import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { subscribeSSE, tasks as tasksApi } from '@/lib/api';
import type { TaskEvent } from '@/lib/api/types';
import type {
  CancelTaskArgs,
  CancelTaskResult,
  InfiniteQueryLike,
  MutationLike,
  QueryLike,
  ResolveEscalationArgs,
  ResolveEscalationResult,
  RevisitTaskArgs,
  RevisitTaskResult,
  TasksApi,
  TasksListPage,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useTasksList(params?: { status?: string; limit?: number; roots_only?: boolean }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['tasks', slug, params],
    queryFn: () => tasksApi.listTasks(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<Awaited<ReturnType<typeof tasksApi.listTasks>>>;
}

function useTasksInfiniteList(
  params?: { status?: string; roots_only?: boolean },
): InfiniteQueryLike<TasksListPage> {
  const slug = useRealOrgSlug();
  // Cap the per-page payload — 50 keeps SSR + initial paint cheap while still
  // covering most viewports in a single fetch. Backend default is 20 but we
  // ask for more to reduce round-trips during scroll.
  const PAGE_SIZE = 50;
  const q = useInfiniteQuery<TasksListPage>({
    queryKey: ['tasks-infinite', slug, params],
    initialPageParam: undefined,
    queryFn: ({ pageParam }) =>
      tasksApi.listTasks(slug, {
        ...(params?.status ? { status: params.status } : {}),
        ...(params?.roots_only ? { roots_only: true } : {}),
        limit: PAGE_SIZE,
        ...(pageParam ? { before: pageParam as string } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!slug,
    // Foreground polling is disabled for the infinite list — pages would
    // re-fetch independently and confuse the cursor chain. The SSE
    // invalidation on `['tasks', slug]` already wakes the bounded list;
    // separate keying isolates the two surfaces.
  });
  return {
    data: q.data ? { pages: q.data.pages } : undefined,
    isLoading: q.isLoading,
    isError: q.isError,
    error: (q.error as Error | null) ?? null,
    fetchNextPage: () => { void q.fetchNextPage(); },
    hasNextPage: !!q.hasNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
  };
}

function useTask(taskId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['task', slug, taskId],
    queryFn: () => tasksApi.getTask(slug, taskId as string),
    // Daemon returns an envelope; consumers want the bare TaskRecord.
    select: (response) => response.task,
    enabled: !!slug && !!taskId,
  });
}

function useTaskRecall(taskId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['task-recall', slug, taskId],
    // tree=true expands children into nested TaskRecallNode payloads;
    // without it, `children` is a flat list of task-ID strings that the
    // TaskRecallTree component cannot render.
    queryFn: () => tasksApi.recallTask(slug, taskId as string, { tree: true }),
    enabled: !!slug && !!taskId,
  });
}

function useTaskTailSSE(
  taskId: string | undefined,
  onEvent: (ev: TaskEvent) => void,
): void {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  useEffect(() => {
    if (!slug || !taskId) return;
    const ctl = new AbortController();
    subscribeSSE<TaskEvent>(tasksApi.taskEventsPath(slug, taskId), {
      signal: ctl.signal,
      onMessage: (ev) => {
        onEvent(ev);
        if (ev.type === 'task_complete' || ev.type === 'task_failed' || ev.type === 'task_blocked') {
          qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
          qc.invalidateQueries({ queryKey: ['tasks', slug] });
        }
      },
    }).catch(() => { /* swallow */ });
    return () => ctl.abort();
  }, [slug, taskId, qc, onEvent]);
}

function useCancelTask(taskId: string): MutationLike<CancelTaskArgs, CancelTaskResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CancelTaskArgs) => tasksApi.cancelTask(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

function useRevisitTask(taskId: string): MutationLike<RevisitTaskArgs, RevisitTaskResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RevisitTaskArgs) => tasksApi.revisitTask(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

function useResolveEscalation(taskId: string): MutationLike<ResolveEscalationArgs, ResolveEscalationResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ResolveEscalationArgs) =>
      tasksApi.resolveEscalation(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

export const realTasksApi: TasksApi = {
  useTasksList,
  useTasksInfiniteList,
  useTask: useTask as TasksApi['useTask'],
  useTaskRecall: useTaskRecall as TasksApi['useTaskRecall'],
  useTaskTailSSE,
  useCancelTask,
  useRevisitTask,
  useResolveEscalation,
};

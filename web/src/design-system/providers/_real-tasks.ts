import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef } from 'react';
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
  TaskListStream,
  TaskListStreams,
  TaskTailSSECallbacks,
  RevisitTaskArgs,
  RevisitTaskResult,
  TasksApi,
  TasksListPage,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

// Shared invalidation for every surface that reads task status. The task LIST
// is served by FOUR query families — 'tasks', 'tasks-infinite', 'tasks-roots',
// and 'tasks-roots-infinite' (TasksPage reads the last one) — each keyed as
// [family, slug, params]. React Query's invalidateQueries prefix-matches array
// keys element-by-element, so invalidating ['tasks', slug] alone never reaches
// the other three families (different first element), leaving the list showing
// a stale status after a detail-page mutation (THR-069 msg78). Foreground
// polling on the infinite lists is disabled, so onSuccess invalidation is the
// only refresh path. A predicate on key[0].startsWith('tasks') covers all four
// families in one shot; 'task' and 'task-recall' don't start with 'tasks', so
// the detail/recall keys are correctly excluded and invalidated explicitly.
function invalidateTaskViews(
  qc: ReturnType<typeof useQueryClient>,
  slug: string,
  taskId?: string,
  allDetails = false,
): Promise<void> {
  const refreshes = [qc.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey;
      return (
        Array.isArray(key) &&
        typeof key[0] === 'string' &&
        (key[0].startsWith('tasks') || (allDetails && key[0] === 'task')) &&
        key[1] === slug
      );
    },
  })];
  if (taskId) {
    refreshes.push(qc.invalidateQueries({ queryKey: ['task', slug, taskId] }));
  }
  return Promise.all(refreshes).then(() => undefined);
}

function useTasksList(params?: { status?: string; limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['tasks', slug, params],
    queryFn: () => tasksApi.listTasks(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<Awaited<ReturnType<typeof tasksApi.listTasks>>>;
}

function useTasksInfiniteList(
  params?: { status?: string },
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
    fetchNextPage: () => q.fetchNextPage(),
    hasNextPage: !!q.hasNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
  };
}

function useTasksRoots(
  params?: { status?: string; limit?: number; assigned_agent?: string },
) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['tasks-roots', slug, params],
    queryFn: () => tasksApi.listTaskRoots(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<Awaited<ReturnType<typeof tasksApi.listTaskRoots>>>;
}

function useTasksRootsInfinite(
  params?: { status?: string; assigned_agent?: string },
): InfiniteQueryLike<TasksListPage> {
  const slug = useRealOrgSlug();
  const PAGE_SIZE = 50;
  const q = useInfiniteQuery<TasksListPage>({
    queryKey: ['tasks-roots-infinite', slug, params],
    initialPageParam: undefined,
    queryFn: ({ pageParam }) =>
      tasksApi.listTaskRoots(slug, {
        ...(params?.status ? { status: params.status } : {}),
        ...(params?.assigned_agent ? { assigned_agent: params.assigned_agent } : {}),
        limit: PAGE_SIZE,
        ...(pageParam ? { before: pageParam as string } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!slug,
  });
  return {
    data: q.data ? { pages: q.data.pages } : undefined,
    isLoading: q.isLoading,
    isError: q.isError,
    error: (q.error as Error | null) ?? null,
    fetchNextPage: () => q.fetchNextPage(),
    hasNextPage: !!q.hasNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
  };
}

// Tasks list owners. Ordinary partitions use their own key family so an
// ordinary `status=escalated` traversal never shares a cache entry, cursor or
// error with the attention traversal, even when both send identical requests.
// Both families start with 'tasks', so invalidateTaskViews reaches them.
const TASK_LIST_ORDINARY_FAMILY = 'tasks-list-ordinary';
const TASK_LIST_ATTENTION_FAMILY = 'tasks-roots-infinite';
const TASK_LIST_PAGE_SIZE = 50;

function useTaskListStream(
  slug: string,
  family: string,
  owner: string,
  status: string,
  assignedAgent: string | undefined,
  enabled: boolean,
): TaskListStream {
  const qc = useQueryClient();
  const params = assignedAgent ? { status, assigned_agent: assignedAgent } : { status };
  const queryKey = [family, slug, params];
  const keyHash = JSON.stringify(queryKey);
  const active = enabled && !!slug;
  const q = useInfiniteQuery<TasksListPage>({
    queryKey,
    initialPageParam: undefined,
    queryFn: ({ pageParam }) =>
      tasksApi.listTaskRoots(slug, {
        status,
        ...(assignedAgent ? { assigned_agent: assignedAgent } : {}),
        limit: TASK_LIST_PAGE_SIZE,
        ...(pageParam ? { before: pageParam as string } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: active,
  });
  const latest = useRef(q);
  latest.current = q;
  // One accepted operation per owner: a second advance/refresh/retry while this
  // owner's query is fetching (for any reason) is coalesced, never queued.
  const operation = useRef<{ key: string } | null>(null);
  const run = useCallback(async (work: () => Promise<unknown>) => {
    if (!active) return;
    if (operation.current?.key === keyHash) return;
    if (qc.getQueryState(JSON.parse(keyHash) as unknown[])?.fetchStatus === 'fetching') return;
    const token = { key: keyHash };
    operation.current = token;
    try {
      await work();
    } finally {
      if (operation.current === token) operation.current = null;
    }
  }, [active, keyHash, qc]);
  const fetchNextPage = useCallback((options?: { explicit?: boolean }) => run(async () => {
    const current = latest.current;
    if (!current.hasNextPage) return;
    // A failed continuation resumes only on explicit Retry; a failed initial
    // load or refresh must reconcile before its stale chain advances again.
    if (current.isFetchNextPageError ? !options?.explicit : current.isError) return;
    // An invalidation that lands while this continuation is in flight is
    // otherwise cleared by the continuation's own success (for example after
    // remounting onto the in-flight fetch). Finish, then reconcile this owner
    // by its own key: the observer may have moved to another org/filter while
    // awaiting, so re-mark exactly this key (refetching it only if active).
    const hash = keyHash;
    let invalidated = false;
    const unsubscribe = qc.getQueryCache().subscribe((event) => {
      if (event.type === 'updated' && event.action.type === 'invalidate' &&
        JSON.stringify(event.query.queryKey) === hash) invalidated = true;
    });
    try {
      await current.fetchNextPage({ cancelRefetch: false });
    } finally {
      unsubscribe();
    }
    if (invalidated) {
      await qc.invalidateQueries({ queryKey: JSON.parse(hash) as unknown[], exact: true }, { cancelRefetch: false });
    }
  }), [keyHash, qc, run]);
  const refresh = useCallback(() => run(() => latest.current.refetch({ cancelRefetch: false })), [run]);
  const retry = useCallback(
    () => (latest.current.isFetchNextPageError ? fetchNextPage({ explicit: true }) : refresh()),
    [fetchNextPage, refresh],
  );
  const tasks = useMemo(() => {
    const seen = new Set<string>();
    return (q.data?.pages.flatMap((page) => page.tasks) ?? []).filter((task) => {
      if (
        task.status !== status ||
        (assignedAgent && task.assigned_agent !== assignedAgent) ||
        task.parent_task_id ||
        seen.has(task.task_id)
      ) return false;
      seen.add(task.task_id);
      return true;
    });
  }, [q.data, status, assignedAgent]);
  const isInitialPending = active && q.isPending && !q.isError;
  return useMemo(() => ({
    owner,
    status,
    tasks,
    isInitialPending,
    isInitialError: q.isLoadingError,
    isStale: q.isRefetchError,
    isContinuationError: q.isFetchNextPageError,
    isRefreshing: q.isRefetching,
    isFetchingNextPage: q.isFetchingNextPage,
    hasNextPage: !!q.hasNextPage,
    fetchNextPage,
    refresh,
    retry,
  }), [owner, status, tasks, isInitialPending, q.isLoadingError, q.isRefetchError, q.isFetchNextPageError,
    q.isRefetching, q.isFetchingNextPage, q.hasNextPage, fetchNextPage, refresh, retry]);
}

function useTasksListStreams(
  filters?: { status?: string; assigned_agent?: string },
): TaskListStreams {
  const slug = useRealOrgSlug();
  const agent = filters?.assigned_agent || undefined;
  const selected = (status: string) => !filters?.status || filters.status === status;
  const ordinary = (status: string) => [slug, TASK_LIST_ORDINARY_FAMILY, status, status, agent, selected(status)] as const;
  // Fixed, finite owners (hook order never varies); unselected owners are disabled.
  const pending = useTaskListStream(...ordinary('pending'));
  const inProgress = useTaskListStream(...ordinary('in_progress'));
  const escalated = useTaskListStream(...ordinary('escalated'));
  const completed = useTaskListStream(...ordinary('completed'));
  const failed = useTaskListStream(...ordinary('failed'));
  const cancelled = useTaskListStream(...ordinary('cancelled'));
  const superseded = useTaskListStream(...ordinary('superseded'));
  // Waiting on you keeps its established independent, filter-free traversal.
  const attention = useTaskListStream(slug, TASK_LIST_ATTENTION_FAMILY, 'attention', 'escalated', undefined, true);
  const status = filters?.status;
  const ordinaryStreams = useMemo(
    () => [pending, inProgress, escalated, completed, failed, cancelled, superseded]
      .filter((stream) => !status || stream.status === status),
    [pending, inProgress, escalated, completed, failed, cancelled, superseded, status],
  );
  return useMemo(() => ({ ordinary: ordinaryStreams, attention }), [ordinaryStreams, attention]);
}

function useTask(taskId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['task', slug, taskId],
    queryFn: () => tasksApi.getTask(slug, taskId as string),
    // Daemon returns an envelope; consumers want the bare TaskRecord.
    select: (response) => ({ ...response.task, pause: response.pause ?? response.task.pause }),
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
  callbacks?: TaskTailSSECallbacks,
): void {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  useEffect(() => {
    if (!slug || !taskId) return;
    const ctl = new AbortController();
    subscribeSSE<TaskEvent>(tasksApi.taskEventsPath(slug, taskId), {
      signal: ctl.signal,
      onOpen: callbacks?.onOpen,
      onMessage: (ev) => {
        onEvent(ev);
        if (ev.type === 'task_complete' || ev.type === 'task_failed' || ev.type === 'task_blocked') {
          invalidateTaskViews(qc, slug, taskId);
        }
      },
    }).catch((error: unknown) => {
      if (!ctl.signal.aborted) callbacks?.onError?.(error);
    });
    return () => ctl.abort();
  }, [slug, taskId, qc, onEvent]);
}

function useCancelTask(taskId: string): MutationLike<CancelTaskArgs, CancelTaskResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CancelTaskArgs) => tasksApi.cancelTask(slug, taskId, body),
    onSuccess: () => {
      invalidateTaskViews(qc, slug, taskId);
    },
  });
}

function useRevisitTask(taskId: string): MutationLike<RevisitTaskArgs, RevisitTaskResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RevisitTaskArgs) => tasksApi.revisitTask(slug, taskId, body),
    onSuccess: () => {
      invalidateTaskViews(qc, slug, taskId);
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
      invalidateTaskViews(qc, slug, taskId);
    },
  });
}

function usePauseTask(taskId: string): ReturnType<TasksApi['usePauseTask']> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const mutationKey = ['task-pause-control', slug, taskId];
  const active = useIsMutating({ mutationKey });
  const mutation = useMutation({
    mutationKey,
    mutationFn: (body: { expected_generation: number }) => tasksApi.pauseTask(slug, taskId, body.expected_generation),
    retry: false,
    onSuccess: () => invalidateTaskViews(qc, slug, undefined, true),
    onError: () => invalidateTaskViews(qc, slug, undefined, true),
  });
  return { mutateAsync: mutation.mutateAsync, isPending: mutation.isPending || active > 0 };
}

function useResumeTask(taskId: string): ReturnType<TasksApi['useResumeTask']> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const mutationKey = ['task-pause-control', slug, taskId];
  const active = useIsMutating({ mutationKey });
  const mutation = useMutation({
    mutationKey,
    mutationFn: (body: { expected_generation: number }) => tasksApi.resumeTask(slug, taskId, body.expected_generation),
    retry: false,
    onSuccess: () => invalidateTaskViews(qc, slug, undefined, true),
    onError: () => invalidateTaskViews(qc, slug, undefined, true),
  });
  return { mutateAsync: mutation.mutateAsync, isPending: mutation.isPending || active > 0 };
}

export const realTasksApi: TasksApi = {
  usePauseTask,
  useResumeTask,
  useTasksList,
  useTasksInfiniteList,
  useTasksRoots,
  useTasksRootsInfinite,
  useTasksListStreams,
  useTask: useTask as TasksApi['useTask'],
  useTaskRecall: useTaskRecall as TasksApi['useTaskRecall'],
  useTaskTailSSE,
  useCancelTask,
  useRevisitTask,
  useResolveEscalation,
};

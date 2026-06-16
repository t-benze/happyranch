import type { TaskRecord, TaskRecallNode } from '@/lib/api/types';
import type {
  InfiniteQueryLike,
  QueryLike,
  TasksApi,
  TasksListPage,
  TasksRoutes,
  MutationLike,
} from './DataContext';

const FIXTURES: TaskRecord[] = [
  {
    task_id: 'TASK-0091',
    team: 'content',
    brief: 'Draft Hong Kong visa guide v2',
    status: 'in_progress',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
  },
  {
    task_id: 'TASK-0090',
    team: 'ops',
    brief: 'Vet partner hotel candidates',
    status: 'blocked',
    block_kind: 'escalated',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T09:00:00Z',
    updated_at: '2026-05-18T09:30:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
  },
] as TaskRecord[];

const RECALL_TREE: TaskRecallNode = {
  task_id: 'TASK-0091',
  assigned_agent: 'content_writer',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  output_summary: null,
  children: [
    {
      task_id: 'TASK-0092',
      assigned_agent: 'content_writer',
      brief: 'Section 4: currency policy',
      status: 'completed',
      output_summary: 'Wrote section 4 (245 words).',
      children: [],
    },
  ],
};

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function noopMutation<TArgs, TResult>(): MutationLike<TArgs, TResult> {
  return {
    mutateAsync: async () => ({}) as TResult,
    isPending: false,
  };
}

function staticInfinite(page: TasksListPage): InfiniteQueryLike<TasksListPage> {
  return {
    data: { pages: [page] },
    isLoading: false,
    isError: false,
    error: null,
    fetchNextPage: () => { /* no-op: prototype fixtures fit in one page */ },
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

export const mockTasksApi: TasksApi = {
  useTasksList: (_params?: { status?: string; limit?: number; roots_only?: boolean }) => ok({ tasks: FIXTURES }),
  useTasksInfiniteList: (_params?: { status?: string; roots_only?: boolean }) =>
    staticInfinite({ tasks: FIXTURES, next_cursor: null }),
  useTask: (taskId) =>
    ok(FIXTURES.find((t) => t.task_id === taskId) ?? FIXTURES[0]),
  useTaskRecall: () => ok(RECALL_TREE),
  useTaskTailSSE: () => { /* no-op */ },
  useCancelTask: () => noopMutation(),
  useRevisitTask: () => noopMutation(),
  useResolveEscalation: () => noopMutation(),
};

export function useMockTasksRoutes(): TasksRoutes {
  return {
    inbox: () => '/__prototypes/tasks/inbox',
    detail: (taskId: string) => `/__prototypes/tasks/${taskId}`,
    inboxForOrg: () => '/__prototypes/tasks/inbox',
  };
}

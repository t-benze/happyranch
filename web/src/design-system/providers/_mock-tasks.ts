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
    status: 'escalated',
    block_kind: null,
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
    fetchNextPage: async () => { /* no-op: prototype fixtures fit in one page */ },
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

const ROOT_FIXTURES: TaskRecord[] = [
  {
    task_id: 'TASK-0092',
    team: 'engineering',
    brief: 'Fix broken auth flow on login page',
    status: 'completed',
    block_kind: null,
    assigned_agent: 'dev_agent',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-19T10:00:00Z',
    updated_at: '2026-05-19T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'escalated',
  },
  {
    task_id: 'TASK-0090',
    team: 'ops',
    brief: 'Vet partner hotel candidates',
    status: 'escalated',
    block_kind: null,
    assigned_agent: 'qa_engineer',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T09:00:00Z',
    updated_at: '2026-05-18T09:30:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'escalated',
  },
  {
    task_id: 'TASK-0088',
    team: 'content',
    brief: 'Write Thailand guide\u00a0\u2192 supersedes TASK-0091',
    status: 'superseded',
    block_kind: null,
    assigned_agent: 'content_writer',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-17T08:00:00Z',
    updated_at: '2026-05-18T08:00:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'superseded',
  },
] as TaskRecord[];

export const mockTasksApi: TasksApi = {
  useTasksList: () => ok({ tasks: FIXTURES }),
  useTasksInfiniteList: () =>
    staticInfinite({ tasks: FIXTURES, next_cursor: null }),
  useTasksRoots: () => ok({ tasks: ROOT_FIXTURES }),
  useTasksRootsInfinite: () =>
    staticInfinite({ tasks: ROOT_FIXTURES, next_cursor: null }),
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

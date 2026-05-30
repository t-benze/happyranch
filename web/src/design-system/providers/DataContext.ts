/**
 * Provider-agnostic data layer for compositions.
 *
 * Compositions never call TanStack Query or `lib/api` directly. They call
 * provider-aware hooks from `@/hooks/`, which forward to whatever
 * `ThreadsApi` (and future feature APIs) the surrounding provider installs.
 *
 * Two implementations exist:
 *
 * - `<AppProvider>` (production) — wires the real TanStack Query bodies in
 *   `_real-threads.ts` against the daemon.
 * - `<PrototypeProvider>` (designer sandbox) — wires the canned fixtures in
 *   `_mock-threads.ts` from `@/mocks/`.
 *
 * The hook signatures intentionally drop the `slug` argument so the same
 * composition file can render against either provider. The active slug is a
 * concern of the provider, not the consumer.
 *
 * See `web/DESIGN_SYSTEM.md` §8.
 */
import { createContext, useContext } from 'react';
import type {
  HealthResponse,
  OrgsListResponse,
  ThreadDetailResponse,
  ThreadMessage,
  ThreadRecord,
} from '@/lib/api/types';
import type { threads as threadsApi } from '@/lib/api';
import type { tasks as tasksApi } from '@/lib/api';
import type { kb as kbApi } from '@/lib/api';
import type { talks as talksApi } from '@/lib/api';
import type { audit as auditApi } from '@/lib/api';
import type { agents as agentsApi } from '@/lib/api';
import type { jobs as jobsApi } from '@/lib/api';
import type {
  JobListResponse,
  JobRecord,
  KBEntry,
  TalkRecord,
  TaskEvent,
  TaskRecord,
  TaskRecallNode,
} from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Hook-shape primitives
// ---------------------------------------------------------------------------

export interface QueryLike<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

/** Minimal subset of TanStack's UseInfiniteQueryResult that consumers need.
 *  `data.pages` is the per-page payload; callers flatten as appropriate. */
export interface InfiniteQueryLike<TPage> {
  data: { pages: TPage[] } | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  fetchNextPage: () => void;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
}

export interface MutationLike<TArgs, TResult> {
  mutateAsync: (args: TArgs) => Promise<TResult>;
  isPending: boolean;
}

// ---------------------------------------------------------------------------
// ThreadsApi — covers every hook ThreadsPage + its dialogs consume.
// ---------------------------------------------------------------------------

export type ComposeArgs = Parameters<typeof threadsApi.composeThread>[1];
export type ComposeResult = Awaited<ReturnType<typeof threadsApi.composeThread>>;

export type SendFollowUpArgs = Parameters<typeof threadsApi.sendThreadFollowUp>[2];
export type SendFollowUpResult = Awaited<ReturnType<typeof threadsApi.sendThreadFollowUp>>;

export type InviteArgs = Parameters<typeof threadsApi.inviteToThread>[2];
export type InviteResult = Awaited<ReturnType<typeof threadsApi.inviteToThread>>;

export type ArchiveArgs = Parameters<typeof threadsApi.archiveThread>[2];
export type ArchiveResult = Awaited<ReturnType<typeof threadsApi.archiveThread>>;

export type AbandonArgs = Parameters<typeof threadsApi.abandonThread>[2];
export type AbandonResult = Awaited<ReturnType<typeof threadsApi.abandonThread>>;

export type ExtendArgs = Parameters<typeof threadsApi.extendThreadCap>[2];
export type ExtendResult = Awaited<ReturnType<typeof threadsApi.extendThreadCap>>;

export interface ThreadsApi {
  // Reads
  useThreadsList: (
    params?: { status?: string; limit?: number },
  ) => QueryLike<{ threads: ThreadRecord[] }>;
  useThread: (threadId: string | undefined) => QueryLike<ThreadDetailResponse>;
  useThreadMessages: (
    threadId: string | undefined,
  ) => QueryLike<{ messages: ThreadMessage[] }>;

  // SSE (no-op under mocks)
  useThreadsInboxSSE: () => void;
  useThreadTailSSE: (threadId: string | undefined) => void;

  // Mutations — `threadId` is a per-hook argument so the call shape mirrors
  // the existing TanStack Query hooks, just with `slug` stripped.
  useComposeThread: () => MutationLike<ComposeArgs, ComposeResult>;
  useSendFollowUp: (threadId: string) => MutationLike<SendFollowUpArgs, SendFollowUpResult>;
  useInviteAgent: (threadId: string) => MutationLike<InviteArgs, InviteResult>;
  useArchiveThread: (threadId: string) => MutationLike<ArchiveArgs, ArchiveResult>;
  useAbandonThread: (threadId: string) => MutationLike<AbandonArgs, AbandonResult>;
  useExtendCap: (threadId: string) => MutationLike<ExtendArgs, ExtendResult>;
}

// ---------------------------------------------------------------------------
// TasksApi — covers every hook TasksPage + its dialogs consume.
// ---------------------------------------------------------------------------

export type CancelTaskArgs = Parameters<typeof tasksApi.cancelTask>[2];
export type CancelTaskResult = Awaited<ReturnType<typeof tasksApi.cancelTask>>;

export type RevisitTaskArgs = Parameters<typeof tasksApi.revisitTask>[2];
export type RevisitTaskResult = Awaited<ReturnType<typeof tasksApi.revisitTask>>;

export type ResolveEscalationArgs = Parameters<typeof tasksApi.resolveEscalation>[2];
export type ResolveEscalationResult = Awaited<ReturnType<typeof tasksApi.resolveEscalation>>;

export type TasksListPage = { tasks: TaskRecord[]; next_cursor?: string | null };

export interface TasksApi {
  useTasksList: (params?: {
    status?: string;
    limit?: number;
  }) => QueryLike<{ tasks: TaskRecord[] }>;
  /** Cursor-paginated variant used by the inbox page's infinite scroll.
   *  Each page is the raw server payload (`tasks` + `next_cursor`); the
   *  caller flattens `data.pages` and asks `fetchNextPage()` from a
   *  scroll-observer sentinel. */
  useTasksInfiniteList: (params?: {
    status?: string;
  }) => InfiniteQueryLike<TasksListPage>;
  useTask: (taskId: string | undefined) => QueryLike<TaskRecord>;
  useTaskRecall: (taskId: string | undefined) => QueryLike<TaskRecallNode>;

  /** Subscribes; passes each event to `onEvent`. No-op under mocks. */
  useTaskTailSSE: (
    taskId: string | undefined,
    onEvent: (ev: TaskEvent) => void,
  ) => void;

  useCancelTask: (taskId: string) => MutationLike<CancelTaskArgs, CancelTaskResult>;
  useRevisitTask: (taskId: string) => MutationLike<RevisitTaskArgs, RevisitTaskResult>;
  useResolveEscalation: (
    taskId: string,
  ) => MutationLike<ResolveEscalationArgs, ResolveEscalationResult>;
}

export interface TasksRoutes {
  inbox: () => string;
  detail: (taskId: string) => string;
  inboxForOrg: (slug: string) => string;
}

// KbApi — covers every hook KbPage + its drawer + (optional) compose dialog
// consume.
// ---------------------------------------------------------------------------

export type AddKBEntryArgs = Parameters<typeof kbApi.addKBEntry>[1];
export type AddKBEntryResult = Awaited<ReturnType<typeof kbApi.addKBEntry>>;

export interface KbApi {
  useKBList: (params?: {
    type?: string;
  }) => QueryLike<{ entries: KBEntry[] }>;
  useKBSearch: (
    q: string,
    params?: { limit?: number },
  ) => QueryLike<{ entries: KBEntry[] }>;
  useKBEntry: (entrySlug: string | undefined) => QueryLike<KBEntry>;
  /** Mutation is wired only under the real provider; mocks no-op. */
  useAddKBEntry: () => MutationLike<AddKBEntryArgs, AddKBEntryResult>;
}

export interface KbRoutes {
  inbox: () => string;
  detail: (entrySlug: string) => string;
  inboxForOrg: (slug: string) => string;
}

// ---------------------------------------------------------------------------
// TalksApi — covers every hook TalksPage + its dialogs consume.
// ---------------------------------------------------------------------------

export type StartTalkArgs = Parameters<typeof talksApi.startTalk>[1];
export type StartTalkResult = Awaited<ReturnType<typeof talksApi.startTalk>>;

export type AbandonTalkArgs = Parameters<typeof talksApi.abandonTalk>[2];
export type AbandonTalkResult = Awaited<ReturnType<typeof talksApi.abandonTalk>>;

export type EndTalkArgs = Parameters<typeof talksApi.endTalk>[2];
export type EndTalkResult = Awaited<ReturnType<typeof talksApi.endTalk>>;

export type DispatchFromTalkArgs = Parameters<typeof talksApi.dispatchFromTalk>[2];
export type DispatchFromTalkResult = Awaited<ReturnType<typeof talksApi.dispatchFromTalk>>;

export interface TalksApi {
  useTalksList: (
    params?: { status?: string; agent?: string; limit?: number },
  ) => QueryLike<{ talks: TalkRecord[] }>;
  useTalk: (talkId: string | undefined) => QueryLike<TalkRecord>;

  useStartTalk: () => MutationLike<StartTalkArgs, StartTalkResult>;
  useAbandonTalk: (talkId: string) => MutationLike<AbandonTalkArgs, AbandonTalkResult>;
  useEndTalk: (talkId: string) => MutationLike<EndTalkArgs, EndTalkResult>;
  useDispatchFromTalk: (
    talkId: string,
  ) => MutationLike<DispatchFromTalkArgs, DispatchFromTalkResult>;
}

export interface TalksRoutes {
  inbox: () => string;
  detail: (talkId: string) => string;
  inboxForOrg: (slug: string) => string;
}

// ---------------------------------------------------------------------------
// Context shape — one bag per feature domain. Future PRs add `kb`…
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// OrgsApi — minimal read-only surface so the TopBar org dropdown works
// under both providers without TopBar reaching into `@/lib/api` itself.
// ---------------------------------------------------------------------------

export interface OrgsApi {
  useOrgsList: () => QueryLike<OrgsListResponse>;
}

// ---------------------------------------------------------------------------
// HealthApi — minimal daemon liveness probe consumed by the Dashboard page.
// ---------------------------------------------------------------------------

export interface HealthApi {
  useHealth: () => QueryLike<HealthResponse>;
}

// ---------------------------------------------------------------------------
// AgentsApi — minimal read-only roster used by the Composer for
// @-mention autocomplete. Lives on DataContext so prototypes can swap
// in canned fixtures.
// ---------------------------------------------------------------------------

export type ApproveAgentArgs = Parameters<typeof agentsApi.approveAgent>[1];
export type ApproveAgentResult = Awaited<ReturnType<typeof agentsApi.approveAgent>>;

export type RejectAgentArgs = Parameters<typeof agentsApi.rejectAgent>[2];
export type RejectAgentResult = Awaited<ReturnType<typeof agentsApi.rejectAgent>>;

export interface AgentsApi {
  useAgentsList: () => QueryLike<{ agents: import('@/lib/api/agents').AgentSummary[] }>;
  /** Pending enrollments — `status` filter narrows the file scan. */
  useEnrollmentsList: (
    params?: { status?: 'pending' | 'approved' },
  ) => QueryLike<{ enrollments: import('@/lib/api/agents').AgentEnrollment[] }>;
  /** Read-only learnings list for the agent detail drawer. */
  useAgentLearnings: (
    agentName: string | undefined,
  ) => QueryLike<{ entries: import('@/lib/api/agents').LearningEntrySummary[] }>;
  /** Tasks where this agent was the assigned (manager) agent. */
  useAgentTasks: (
    agentName: string | undefined,
  ) => QueryLike<{ tasks: TaskRecord[] }>;

  useApproveAgent: () => MutationLike<ApproveAgentArgs, ApproveAgentResult>;
  useRejectAgent: () => MutationLike<
    { agentName: string; body?: { reason?: string } },
    RejectAgentResult
  >;
}

export interface AgentsRoutes {
  inbox: () => string;
  pending: () => string;
  detail: (agentName: string) => string;
  inboxForOrg: (slug: string) => string;
}

// ---------------------------------------------------------------------------
// JobsApi — founder-facing surface for background jobs (formerly script requests).
// ---------------------------------------------------------------------------

export type RejectJobArgs = Parameters<typeof jobsApi.rejectJob>[2];
export type RejectJobResult = Awaited<ReturnType<typeof jobsApi.rejectJob>>;

export type RunJobArgs = Parameters<typeof jobsApi.runJob>[2];
export type RunJobResult = Awaited<ReturnType<typeof jobsApi.runJob>>;

export type StopJobResult = Awaited<ReturnType<typeof jobsApi.stopJob>>;

export type JobOutputResult = Awaited<ReturnType<typeof jobsApi.getJobOutput>>;

export interface JobsApi {
  useJobsList: (params?: {
    status?: string;
    agent?: string;
    task_id?: string;
    review_required?: string;
    persistent?: string;
    limit?: number;
  }) => QueryLike<JobListResponse>;
  useJob: (jobId: string | undefined) => QueryLike<JobRecord>;
  useJobOutput: (jobId: string | undefined) => QueryLike<JobOutputResult>;
  useRejectJob: () => MutationLike<{ jobId: string; body: RejectJobArgs }, RejectJobResult>;
  useRunJob: () => MutationLike<{ jobId: string; body: RunJobArgs }, RunJobResult>;
  useStopJob: () => MutationLike<{ jobId: string }, StopJobResult>;
}

export interface JobsRoutes {
  inbox: () => string;
  detail: (jobId: string) => string;
  inboxForOrg: (slug: string) => string;
}

// ---------------------------------------------------------------------------
// AuditApi — read-only audit-log surface for the Audit feature page.
// ---------------------------------------------------------------------------

export interface AuditApi {
  useAuditList: (params?: {
    task_id?: string | null;
    agent?: string | null;
    action?: string | null;
    since?: string | null;
    limit?: number;
  }) => QueryLike<Awaited<ReturnType<typeof auditApi.listAudit>>>;
}

/**
 * Per-feature URL builders. Compositions consume these via the
 * provider-aware `useThreadRoutes()` hook in `@/hooks/threads` instead of
 * hardcoding `/orgs/${slug}/...` — so the same JSX renders under
 * `/orgs/:slug/threads/...` (production) AND
 * `/__prototypes/threads-v2/...` (sandbox) without forking.
 */
export interface ThreadRoutes {
  /** Detail-pane URL for a given thread_id. */
  detail: (threadId: string) => string;
  /** Inbox URL for the active context. */
  inbox: () => string;
  /**
   * Inbox URL when switching to a specific org. Used by the TopBar org
   * dropdown so the user lands in the right place regardless of which
   * provider is mounted. Under the real provider this is
   * `/orgs/<slug>/threads`; under the prototype it stays inside the
   * sandbox subtree, ignoring the slug.
   */
  inboxForOrg: (slug: string) => string;
}

export interface DataContextValue {
  orgs: OrgsApi;
  agents: AgentsApi;
  audit: AuditApi;
  threads: ThreadsApi;
  tasks: TasksApi;
  kb: KbApi;
  talks: TalksApi;
  health: HealthApi;
  jobs: JobsApi;
  /**
   * Provider-supplied React hook that returns the active feature's route
   * builders. A hook (not a plain object) so the implementation can read
   * the current URL via `useParams` / `useLocation`.
   */
  useThreadRoutes: () => ThreadRoutes;
  useTasksRoutes: () => TasksRoutes;
  useKbRoutes: () => KbRoutes;
  useTalksRoutes: () => TalksRoutes;
  useAgentsRoutes: () => AgentsRoutes;
  useJobsRoutes: () => JobsRoutes;
}

export const DataContext = createContext<DataContextValue | null>(null);

export function useData(): DataContextValue {
  const ctx = useContext(DataContext);
  if (!ctx) {
    throw new Error(
      'useData must be inside <AppProvider> or <PrototypeProvider>.',
    );
  }
  return ctx;
}

/**
 * Mock implementation of `ThreadsApi` for the prototype harness.
 *
 * Backed by fixtures from `@/mocks/`. Reads return synchronously after a
 * brief setTimeout-driven loading flash on the first call per session, so
 * compositions still render the loading state when navigating in. SSE hooks
 * are no-ops — there's no live source under the harness.
 *
 * Mutations write to a module-level in-memory store and trigger a
 * `queryClient.invalidateQueries()` so query consumers re-read. Refresh
 * intentionally resets — fixtures are the canonical state.
 */
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { ThreadDetailResponse, ThreadMessage, ThreadRecord } from '@/lib/api/types';
import type { ThreadTaskSummary } from '@/lib/api/threads';
import { MOCK_MESSAGES, MOCK_PARTICIPANTS, MOCK_THREADS } from '@/mocks';
import type {
  ArchiveArgs,
  ArchiveResult,
  ComposeArgs,
  ComposeResult,
  InviteArgs,
  InviteResult,
  MutationLike,
  QueryLike,
  ResumeArgs,
  ResumeResult,
  SendFollowUpArgs,
  SendFollowUpResult,
  ThreadsApi,
} from './DataContext';

// ---------------------------------------------------------------------------
// In-memory mutable store
// ---------------------------------------------------------------------------

interface MockStore {
  threads: ThreadRecord[];
  messages: Record<string, ThreadMessage[]>;
  participants: Record<string, string[]>;
  nextThreadIdx: number;
}

function freshStore(): MockStore {
  return {
    threads: MOCK_THREADS.map((t) => ({ ...t })),
    messages: Object.fromEntries(
      Object.entries(MOCK_MESSAGES).map(([k, v]) => [k, v.map((m) => ({ ...m }))]),
    ),
    participants: Object.fromEntries(
      Object.entries(MOCK_PARTICIPANTS).map(([k, v]) => [k, [...v]]),
    ),
    nextThreadIdx: MOCK_THREADS.length + 1,
  };
}

let store: MockStore = freshStore();

function nextSeq(threadId: string): number {
  const list = store.messages[threadId] ?? [];
  return list.reduce((max, m) => Math.max(max, m.seq), 0) + 1;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Loading flash — fires once per (query-key, session) so navigations show
// a momentary "Loading…" state. Persisted via module-scoped Set; deliberately
// not in the QueryClient so refresh keeps the experience.
// ---------------------------------------------------------------------------

const seenKeys = new Set<string>();

function useLoadingFlash(key: string, delayMs = 180): boolean {
  const [done, setDone] = useState(() => seenKeys.has(key));
  useEffect(() => {
    if (done) return;
    const t = setTimeout(() => {
      seenKeys.add(key);
      setDone(true);
    }, delayMs);
    return () => clearTimeout(t);
  }, [done, delayMs, key]);
  return !done;
}

// ---------------------------------------------------------------------------
// Reads — mock data wrapped in TanStack queries so invalidation works.
// ---------------------------------------------------------------------------

function useThreadsList(
  params?: { status?: string; limit?: number },
): QueryLike<{ threads: ThreadRecord[] }> {
  const flashing = useLoadingFlash(`threads:${params?.status ?? 'all'}`);
  const q = useQuery({
    queryKey: ['mock-threads', params?.status ?? 'all'],
    queryFn: async () => {
      await sleep(0);
      const status = params?.status;
      const filtered = status
        ? store.threads.filter((t) => t.status === status)
        : [...store.threads];
      return { threads: filtered };
    },
  });
  return {
    data: flashing ? undefined : q.data,
    isLoading: flashing || q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}

function useThread(threadId: string | undefined): QueryLike<ThreadDetailResponse> {
  const flashing = useLoadingFlash(`thread:${threadId ?? ''}`);
  const q = useQuery({
    queryKey: ['mock-thread', threadId],
    enabled: !!threadId,
    queryFn: async (): Promise<ThreadDetailResponse> => {
      await sleep(0);
      const id = threadId as string;
      const rec = store.threads.find((t) => t.thread_id === id);
      if (!rec) throw new Error(`mock thread not found: ${id}`);
      return {
        ...rec,
        participants: store.participants[id] ?? [],
        messages: store.messages[id] ?? [],
      };
    },
  });
  return {
    data: flashing ? undefined : q.data,
    isLoading: flashing || q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}

function useThreadMessages(
  threadId: string | undefined,
): QueryLike<{ messages: ThreadMessage[] }> {
  const flashing = useLoadingFlash(`thread-messages:${threadId ?? ''}`);
  const q = useQuery({
    queryKey: ['mock-thread-messages', threadId],
    enabled: !!threadId,
    queryFn: async () => {
      await sleep(0);
      const id = threadId as string;
      return { messages: store.messages[id] ?? [] };
    },
  });
  return {
    data: flashing ? undefined : q.data,
    isLoading: flashing || q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}

// Canned thread-dispatched tasks (THR-061). Newest-first, mirroring the
// server's ORDER BY created_at DESC so the composition never re-sorts.
function mockThreadTasks(threadId: string): ThreadTaskSummary[] {
  return [
    {
      id: `${threadId}-T2`,
      status: 'in_progress',
      brief: 'Build the tasks-from-thread section in the thread detail rail.',
      assigned_agent: 'frontend_engineer',
      created_at: '2026-05-15T12:30:00Z',
      parent_task_id: null,
    },
    {
      id: `${threadId}-T1`,
      status: 'completed',
      brief: 'Add GET /threads/{thread_id}/tasks and the DB query behind it.',
      assigned_agent: 'backend_engineer',
      created_at: '2026-05-15T12:00:00Z',
      parent_task_id: null,
    },
  ];
}

function useThreadTasks(
  threadId: string | undefined,
): QueryLike<ThreadTaskSummary[]> {
  const flashing = useLoadingFlash(`thread-tasks:${threadId ?? ''}`);
  const q = useQuery({
    queryKey: ['mock-thread-tasks', threadId],
    enabled: !!threadId,
    queryFn: async () => {
      await sleep(0);
      return mockThreadTasks(threadId as string);
    },
  });
  return {
    data: flashing ? undefined : q.data,
    isLoading: flashing || q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}

// ---------------------------------------------------------------------------
// SSE — no-op under the prototype harness.
// ---------------------------------------------------------------------------

function useThreadsInboxSSE(): void {
  /* deliberately empty */
}

function useThreadTailSSE(_threadId: string | undefined): void {
  /* deliberately empty */
  void _threadId;
}

// ---------------------------------------------------------------------------
// Mutations — write to the in-memory store + invalidate so reads re-fire.
// ---------------------------------------------------------------------------

function useComposeThread(): MutationLike<ComposeArgs, ComposeResult> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ComposeArgs) => {
      await sleep(150);
      const idx = store.nextThreadIdx++;
      const newId = `THR-${String(idx).padStart(3, '0')}`;
      const startedAt = '2026-05-15T12:00:00Z'; // stable for fixtures
      const rec: ThreadRecord = {
        thread_id: newId,
        subject: body.subject,
        status: 'open',
        started_at: startedAt,
        archived_at: null,
        forwarded_from_id: body.forwarded_from_id ?? null,
        forwarded_from_kind: body.forwarded_from_kind ?? null,
        turn_cap: 500,
        turns_used: 1,
        summary: null,
        transcript_path: null,
        composed_from_dream_id: null,
        last_speaker: 'founder',
      };
      store.threads = [rec, ...store.threads];
      store.participants[newId] = ['founder', ...body.recipients];
      store.messages[newId] = [
        {
          seq: 1,
          speaker: 'founder',
          kind: 'message',
          body_markdown: body.body_markdown,
          decline_reason: null,
          system_payload: null,
          attachments: body.attachments?.map((a) => ({
            artifact_name: a.artifact_name,
            display_name: a.display_name ?? a.artifact_name,
            size_bytes: null,
            content_type: a.content_type ?? null,
            uploaded_by: 'founder',
          })) ?? [],
          created_at: startedAt,
          responder_status: [],
        },
      ];
      return { thread_id: newId, started_at: startedAt, pending_replies: body.recipients.length };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-threads'] });
    },
  });
}

function useSendFollowUp(threadId: string): MutationLike<SendFollowUpArgs, SendFollowUpResult> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: SendFollowUpArgs) => {
      await sleep(120);
      const seq = nextSeq(threadId);
      const msg: ThreadMessage = {
        seq,
        speaker: 'founder',
        kind: 'message',
        body_markdown: body.body_markdown,
        decline_reason: null,
        system_payload: null,
        attachments: body.attachments?.map((a) => ({
          artifact_name: a.artifact_name,
          display_name: a.display_name ?? a.artifact_name,
          size_bytes: null,
          content_type: a.content_type ?? null,
          uploaded_by: 'founder',
        })) ?? [],
        created_at: '2026-05-15T12:00:00Z',
        responder_status: [],
      };
      const prev = store.messages[threadId] ?? [];
      store.messages[threadId] = [...prev, msg];
      const idx = store.threads.findIndex((t) => t.thread_id === threadId);
      if (idx >= 0) {
        store.threads[idx] = {
          ...store.threads[idx],
          turns_used: store.threads[idx].turns_used + 1,
        };
      }
      return { thread_id: threadId, seq };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-thread-messages', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-thread', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-threads'] });
    },
  });
}

function useInviteAgent(threadId: string): MutationLike<InviteArgs, InviteResult> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: InviteArgs) => {
      await sleep(120);
      const list = store.participants[threadId] ?? [];
      if (!list.includes(body.agent_name)) {
        store.participants[threadId] = [...list, body.agent_name];
      }
      const seq = nextSeq(threadId);
      store.messages[threadId] = [
        ...(store.messages[threadId] ?? []),
        {
          seq,
          speaker: 'founder',
          kind: 'system',
          body_markdown: null,
          decline_reason: null,
          system_payload: { event: 'invited', agent: body.agent_name },
          attachments: [],
          created_at: '2026-05-15T12:00:00Z',
          responder_status: [],
        },
      ];
      return { thread_id: threadId, agent_name: body.agent_name, system_message_seq: seq };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-thread', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-thread-messages', threadId] });
    },
  });
}

function useArchiveThread(threadId: string): MutationLike<ArchiveArgs, ArchiveResult> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ArchiveArgs) => {
      await sleep(150);
      const idx = store.threads.findIndex((t) => t.thread_id === threadId);
      if (idx >= 0) {
        store.threads[idx] = {
          ...store.threads[idx],
          status: 'archived',
          archived_at: '2026-05-15T12:00:00Z',
          summary: body.summary,
        };
      }
      return { thread_id: threadId, status: 'archived' };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-thread', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-threads'] });
    },
  });
}

function useResumeThread(threadId: string): MutationLike<ResumeArgs, ResumeResult> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await sleep(120);
      const idx = store.threads.findIndex((t) => t.thread_id === threadId);
      if (idx >= 0) {
        store.threads[idx] = { ...store.threads[idx], status: 'open' };
      }
      const seq = nextSeq(threadId);
      store.messages[threadId] = [
        ...(store.messages[threadId] ?? []),
        {
          seq,
          speaker: 'founder',
          kind: 'system',
          body_markdown: null,
          decline_reason: null,
          system_payload: { kind_tag: 'resumed' },
          attachments: [],
          created_at: '2026-06-01T12:00:00Z',
          responder_status: [],
        },
      ];
      return { thread_id: threadId, status: 'open' };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-thread', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-thread-messages', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-threads'] });
    },
  });
}

function useAbortReplies(threadId: string): MutationLike<void, { thread_id: string; aborted_count: number }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await sleep(120);
      // Clear responder_status from all messages for this thread.
      const msgs = store.messages[threadId];
      if (msgs) {
        store.messages[threadId] = msgs.map((m) => ({
          ...m,
          responder_status: [],
        }));
      }
      return { thread_id: threadId, aborted_count: 1 };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mock-thread-messages', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-thread', threadId] });
      qc.invalidateQueries({ queryKey: ['mock-threads'] });
    },
  });
}

// ---------------------------------------------------------------------------
// Exposed surface
// ---------------------------------------------------------------------------

export const mockThreadsApi: ThreadsApi = {
  useThreadsList,
  useThread,
  useThreadMessages,
  useThreadTasks,
  useThreadsInboxSSE,
  useThreadTailSSE,
  useComposeThread,
  useSendFollowUp,
  useInviteAgent,
  useArchiveThread,
  useResumeThread,
  useAbortReplies,
};

/** Test-only: reset the in-memory store to the canonical fixtures. */
export function __resetMockStoreForTests(): void {
  store = freshStore();
  seenKeys.clear();
}

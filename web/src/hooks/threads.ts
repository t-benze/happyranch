/**
 * Public, provider-aware threads hooks.
 *
 * Every hook is a one-liner that reads `useData().threads` and forwards.
 * Compositions in `features/` and `prototypes/` import from this file —
 * they never reach into `design-system/providers/` directly.
 *
 * The slug is intentionally not an argument; the provider knows its own
 * active org. Compositions that need the slug for URL navigation should
 * use `useThreadRoutes()` (provider-aware) below.
 */
import { useData } from '@/design-system/providers/DataContext';

/**
 * Provider-aware route builder for the threads feature. Compositions
 * use this instead of hardcoding `/orgs/${slug}/threads/...` paths,
 * so the same JSX works under both the real and the prototype routes.
 */
export const useThreadRoutes = () => useData().useThreadRoutes();

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const useThreadsList: ReturnType<typeof useData>['threads']['useThreadsList'] = (
  params,
) => useData().threads.useThreadsList(params);

export const useThread: ReturnType<typeof useData>['threads']['useThread'] = (
  threadId,
) => useData().threads.useThread(threadId);

export const useThreadMessages: ReturnType<typeof useData>['threads']['useThreadMessages'] = (
  threadId,
) => useData().threads.useThreadMessages(threadId);

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export const useThreadsInboxSSE: ReturnType<typeof useData>['threads']['useThreadsInboxSSE'] = () =>
  useData().threads.useThreadsInboxSSE();

export const useThreadTailSSE: ReturnType<typeof useData>['threads']['useThreadTailSSE'] = (
  threadId,
) => useData().threads.useThreadTailSSE(threadId);

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useComposeThread: ReturnType<typeof useData>['threads']['useComposeThread'] = () =>
  useData().threads.useComposeThread();

export const useSendFollowUp: ReturnType<typeof useData>['threads']['useSendFollowUp'] = (
  threadId,
) => useData().threads.useSendFollowUp(threadId);

export const useInviteAgent: ReturnType<typeof useData>['threads']['useInviteAgent'] = (
  threadId,
) => useData().threads.useInviteAgent(threadId);

export const useArchiveThread: ReturnType<typeof useData>['threads']['useArchiveThread'] = (
  threadId,
) => useData().threads.useArchiveThread(threadId);

export const useAbandonThread: ReturnType<typeof useData>['threads']['useAbandonThread'] = (
  threadId,
) => useData().threads.useAbandonThread(threadId);

export const useResumeThread: ReturnType<typeof useData>['threads']['useResumeThread'] = (
  threadId,
) => useData().threads.useResumeThread(threadId);

export const useExtendCap: ReturnType<typeof useData>['threads']['useExtendCap'] = (
  threadId,
) => useData().threads.useExtendCap(threadId);

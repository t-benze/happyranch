/**
 * Mock implementation of `AssistantApi` for the prototype sandbox.
 *
 * Reports a configured assistant; mutations no-op back to the same fixture and
 * the PTY opener rejects (there is no daemon behind the sandbox).
 */
import type { AssistantApi, MutationLike, QueryLike } from './DataContext';
import type { ConversationSummary } from '@/lib/api/assistant';
import type { AssistantStatus } from '@/lib/api/types';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: AssistantStatus = {
  state: 'configured',
  selected_executor: 'claude',
  workspace_path: '/mock/runtime/system/assistant/workspace',
  detail: null,
};

function noopMutation<TArgs>(): MutationLike<TArgs, AssistantStatus> {
  return { mutateAsync: async () => FIXTURE, isPending: false };
}

const CONVERSATIONS_FIXTURE: ConversationSummary[] = [
  {
    id: 'conv-active',
    title: 'Ranch status check',
    created_at: '2026-07-04T10:00:00Z',
    active: true,
  },
  {
    id: 'conv-older',
    title: 'Weekly spend review',
    created_at: '2026-07-03T09:00:00Z',
    active: false,
  },
];

export const mockAssistantApi: AssistantApi = {
  useAssistantStatus: () => ok(FIXTURE),
  useInitAssistant: () => noopMutation(),
  useRegisterAssistant: () => noopMutation(),
  useRepairAssistant: () => noopMutation(),
  openAModeSession: () =>
    Promise.reject(
      new Error('assistant is unavailable in the prototype sandbox'),
    ),
  useListConversations: () => ok(CONVERSATIONS_FIXTURE),
  useCreateConversation: () => ({
    mutateAsync: async () => CONVERSATIONS_FIXTURE[0],
    isPending: false,
  }),
  useActivateConversation: () => ({
    mutateAsync: async () => ({ success: true }),
    isPending: false,
  }),
  useRenameConversation: () => ({
    mutateAsync: async () => ({ success: true }),
    isPending: false,
  }),
  useDeleteConversation: () => ({
    mutateAsync: async () => ({ success: true }),
    isPending: false,
  }),
};

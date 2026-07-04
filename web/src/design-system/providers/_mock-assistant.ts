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

const CONVERSATION_FIXTURE: ConversationSummary[] = [
  { id: 'mock-conv-1', title: 'Ranch status check', created_at: null, active: true },
];

const FIXTURE: AssistantStatus = {
  state: 'configured',
  selected_executor: 'claude',
  workspace_path: '/mock/runtime/system/assistant/workspace',
  detail: null,
};

function noopMutation<TArgs>(): MutationLike<TArgs, AssistantStatus> {
  return { mutateAsync: async () => FIXTURE, isPending: false };
}

export const mockAssistantApi: AssistantApi = {
  useAssistantStatus: () => ok(FIXTURE),
  useInitAssistant: () => noopMutation(),
  useRegisterAssistant: () => noopMutation(),
  useRepairAssistant: () => noopMutation(),
  openSession: () =>
    Promise.reject(
      new Error('assistant terminal is unavailable in the prototype sandbox'),
    ),
  openAModeSession: () =>
    Promise.reject(
      new Error('assistant is unavailable in the prototype sandbox'),
    ),
  useConversations: () => ok(CONVERSATION_FIXTURE),
  useCreateConversation: () => ({
    mutateAsync: async () => CONVERSATION_FIXTURE[0],
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

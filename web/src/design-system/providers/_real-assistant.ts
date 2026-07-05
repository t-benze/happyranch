/**
 * Real (daemon-backed) implementation of `AssistantApi`.
 *
 * The assistant is a single global surface (not org-scoped): status polls
 * `/assistant/status` every 5 s; each mutation primes the status cache from
 * its response so the UI reflects the new state without waiting for the poll.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { assistant as assistantApi } from '@/lib/api';
import type { ConversationSummary } from '@/lib/api/assistant';
import type { AssistantRegisterBody, AssistantStatus } from '@/lib/api/types';
import type { AssistantApi, MutationLike, QueryLike } from './DataContext';

const STATUS_KEY = ['assistant', 'status'];
const CONVERSATIONS_KEY = ['assistant', 'conversations'];

function useAssistantStatus(): QueryLike<AssistantStatus> {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => assistantApi.getAssistantStatus(),
    refetchInterval: 5_000,
  });
}

function useInitAssistant(): MutationLike<{ reconfigure: boolean }, AssistantStatus> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reconfigure: boolean }) => assistantApi.initAssistant(body),
    onSuccess: (data) => qc.setQueryData(STATUS_KEY, data),
  });
}

function useRegisterAssistant(): MutationLike<AssistantRegisterBody, AssistantStatus> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AssistantRegisterBody) => assistantApi.registerAssistant(body),
    onSuccess: (data) => qc.setQueryData(STATUS_KEY, data),
  });
}

function useRepairAssistant(): MutationLike<void, AssistantStatus> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => assistantApi.repairAssistant(),
    onSuccess: (data) => qc.setQueryData(STATUS_KEY, data),
  });
}

// ---- Multi-conversation switcher (THR-056 STEP-B) ----

function useListConversations(enabled: boolean): QueryLike<ConversationSummary[]> {
  return useQuery({
    queryKey: CONVERSATIONS_KEY,
    queryFn: () => assistantApi.listConversations(),
    enabled,
  });
}

function useCreateConversation(): MutationLike<void, ConversationSummary> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => assistantApi.createConversation(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
  });
}

function useActivateConversation(): MutationLike<string, { success: boolean }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => assistantApi.activateConversation(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
  });
}

function useRenameConversation(): MutationLike<
  { id: string; title: string },
  { success: boolean }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      assistantApi.renameConversation(id, title),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
  });
}

function useDeleteConversation(): MutationLike<string, { success: boolean }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => assistantApi.deleteConversation(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
  });
}

export const realAssistantApi: AssistantApi = {
  useAssistantStatus,
  useInitAssistant,
  useRegisterAssistant,
  useRepairAssistant,
  openAModeSession: assistantApi.openAssistantAModeSession,
  useListConversations,
  useCreateConversation,
  useActivateConversation,
  useRenameConversation,
  useDeleteConversation,
};

/**
 * Public, provider-aware System Assistant hooks.
 *
 * Each hook forwards to `useData().assistant.*`, so the SystemAssistant
 * composition renders against either the real (daemon-backed) provider or the
 * prototype mock without reaching into `@/lib/api` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useAssistantStatus: ReturnType<
  typeof useData
>['assistant']['useAssistantStatus'] = () => useData().assistant.useAssistantStatus();

export const useInitAssistant: ReturnType<
  typeof useData
>['assistant']['useInitAssistant'] = () => useData().assistant.useInitAssistant();

export const useRegisterAssistant: ReturnType<
  typeof useData
>['assistant']['useRegisterAssistant'] = () => useData().assistant.useRegisterAssistant();

export const useRepairAssistant: ReturnType<
  typeof useData
>['assistant']['useRepairAssistant'] = () => useData().assistant.useRepairAssistant();

/** Imperative opener for the PTY WebSocket (bearer-subprotocol auth). */
export const useAssistantSessionOpener = (): (() => Promise<WebSocket>) =>
  useData().assistant.openSession;

/** Imperative opener for the A-mode structured-TurnFrame WebSocket. */
export const useAssistantAModeSessionOpener = (): (() => Promise<WebSocket>) =>
  useData().assistant.openAModeSession;

// ---- Multi-conversation switcher (THR-056 STEP-B) ----

// Re-exported so compositions get the conversation shape without a restricted
// deep import into `@/lib/api/assistant` (features may only pull `@/lib/api/types`).
export type { ConversationSummary } from '@/lib/api/assistant';

export const useConversations: ReturnType<
  typeof useData
>['assistant']['useConversations'] = (enabled) =>
  useData().assistant.useConversations(enabled);

export const useCreateConversation: ReturnType<
  typeof useData
>['assistant']['useCreateConversation'] = () =>
  useData().assistant.useCreateConversation();

export const useActivateConversation: ReturnType<
  typeof useData
>['assistant']['useActivateConversation'] = () =>
  useData().assistant.useActivateConversation();

export const useRenameConversation: ReturnType<
  typeof useData
>['assistant']['useRenameConversation'] = () =>
  useData().assistant.useRenameConversation();

export const useDeleteConversation: ReturnType<
  typeof useData
>['assistant']['useDeleteConversation'] = () =>
  useData().assistant.useDeleteConversation();

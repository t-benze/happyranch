/**
 * Mirror of runtime/daemon/routes/assistant.py — the System Assistant
 * status / init / register / repair HTTP routes, plus the A-mode structured
 * WS helpers.
 *
 * The browser cannot set the Authorization header on `new WebSocket()`, so the
 * bearer token is offered via the `Sec-WebSocket-Protocol` subprotocol
 * `happyranch.bearer.<token>` (THR-006 Option A). The daemon validates and
 * echoes it back.
 */
import { getToken } from '../auth';
import { API_PREFIX, request } from './client';
import type { AssistantRegisterBody, AssistantStatus } from './types';

export const getAssistantStatus = (): Promise<AssistantStatus> =>
  request('/assistant/status');

export const getAssistantAModeStatus = (): Promise<{
  available: boolean;
  executor?: string | null;
  reason?: string | null;
}> => request('/assistant/a-mode/status');

export const initAssistant = (body: {
  reconfigure: boolean;
}): Promise<AssistantStatus> => request('/assistant/init', { method: 'POST', body });

export const registerAssistant = (
  body: AssistantRegisterBody,
): Promise<AssistantStatus> =>
  request('/assistant/register', { method: 'POST', body });

export const repairAssistant = (): Promise<AssistantStatus> =>
  request('/assistant/repair', { method: 'POST' });

/** Subprotocol the daemon accepts in place of the Authorization header. */
export const ASSISTANT_BEARER_SUBPROTOCOL_PREFIX = 'happyranch.bearer.';

export const assistantBearerSubprotocol = (token: string): string =>
  `${ASSISTANT_BEARER_SUBPROTOCOL_PREFIX}${token}`;

/** Absolute `ws(s)://` URL for the A-mode (structured TurnFrame) WebSocket. */
export const assistantAModeWsUrl = (): string => {
  const { protocol, host } = window.location;
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
  return `${wsProtocol}//${host}${API_PREFIX}/assistant/a-mode`;
};

// ---- Multi-conversation routes (THR-056 STEP-A) ----

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string | null;
  active: boolean;
};

export const listConversations = (): Promise<ConversationSummary[]> =>
  request('/assistant/a-mode/conversations');

export const createConversation = (): Promise<ConversationSummary> =>
  request('/assistant/a-mode/conversations', { method: 'POST' });

export const activateConversation = (
  convId: string,
): Promise<{ success: boolean }> =>
  request(`/assistant/a-mode/conversations/${convId}/activate`, {
    method: 'POST',
  });

export const renameConversation = (
  convId: string,
  title: string,
): Promise<{ success: boolean }> =>
  request(`/assistant/a-mode/conversations/${convId}`, {
    method: 'PATCH',
    body: { title },
  });

export const deleteConversation = (
  convId: string,
): Promise<{ success: boolean }> =>
  request(`/assistant/a-mode/conversations/${convId}`, {
    method: 'DELETE',
  });

/**
 * Open the A-mode WebSocket — the structured `TurnFrame` stream that drives the
 * thread-style dock. Bearer-subprotocol auth (THR-006 Option A); only the
 * route differs (`/assistant/a-mode`). Resolves once the socket is constructed;
 * the caller wires `onopen` / `onmessage`.
 */
export const openAssistantAModeSession = async (): Promise<WebSocket> => {
  const token = await getToken();
  return new WebSocket(assistantAModeWsUrl(), [assistantBearerSubprotocol(token)]);
};

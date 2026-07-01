/**
 * Mirror of runtime/daemon/routes/assistant.py — the System Assistant
 * status / init / register / repair HTTP routes, plus the helpers a browser
 * needs to attach to the WebSocket PTY at /assistant/session.
 *
 * The browser cannot set the Authorization header on `new WebSocket()`, so the
 * bearer token is offered via the `Sec-WebSocket-Protocol` subprotocol
 * `happyranch.bearer.<token>` (THR-006 Option A). The daemon validates and
 * echoes it back — see `_websocket_token_is_valid` in routes/assistant.py.
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

/** Absolute `ws(s)://` URL for the assistant PTY WebSocket. */
export const assistantSessionWsUrl = (): string => {
  const { protocol, host } = window.location;
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
  return `${wsProtocol}//${host}${API_PREFIX}/assistant/session`;
};

/**
 * Open the assistant PTY WebSocket, authenticating via the bearer subprotocol
 * (THR-006 Option A). Resolves once the socket is constructed (still
 * connecting); the caller wires `onopen` / `onmessage`.
 */
export const openAssistantSession = async (): Promise<WebSocket> => {
  const token = await getToken();
  return new WebSocket(assistantSessionWsUrl(), [assistantBearerSubprotocol(token)]);
};

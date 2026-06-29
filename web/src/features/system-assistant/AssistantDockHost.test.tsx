/**
 * Tests for AssistantDockHost — pre-ack raw-frame tolerance (OPTION 3).
 *
 * The WebSocket message handler is tested by mocking the assistant hooks
 * and driving a mock WebSocket.  Key behaviours:
 *   1. Pre-ack raw (non-JSON) frames → buffered/ignored, never surfaced.
 *   2. "ready" ack transition → exit connecting state, start accepting output.
 *   3. Post-ack structured {"type":"output","text":"..."} → displayed.
 *   4. Post-ack raw frames (unexpected) → handled gracefully as output.
 */
import { waitFor, act, screen } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { AssistantDockHost } from './AssistantDockHost';

// ---------------------------------------------------------------------------
// Shared mock state (hoisted so vi.mock factories can close over it)
// ---------------------------------------------------------------------------

function configuredStatus() {
  return {
    data: { state: 'configured' as const, selected_executor: 'claude' as string | null, workspace_path: '/ws', detail: null },
    isLoading: false,
    isError: false,
    error: null,
  };
}

interface MockSocket {
  readyState: number;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
}

const h = vi.hoisted(() => {
  const result: {
    socket: MockSocket | null;
    openSession: ReturnType<typeof vi.fn<() => Promise<MockSocket>>>;
    status: {
      data: {
        state: 'configured' | 'unconfigured' | 'error';
        selected_executor: string | null;
        workspace_path: string | null;
        detail: string | null;
      } | undefined;
      isLoading: boolean;
      isError: boolean;
      error: unknown;
    };
  } = {
    socket: null,
    openSession: vi.fn<() => Promise<MockSocket>>(),
    status: {
      data: { state: 'configured', selected_executor: 'claude', workspace_path: '/ws', detail: null },
      isLoading: false,
      isError: false,
      error: null,
    },
  };
  return result;
});

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/assistant', () => ({
  useAssistantStatus: () => h.status,
  useInitAssistant: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegisterAssistant: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRepairAssistant: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAssistantSessionOpener: () => h.openSession,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockSocket(): MockSocket {
  const socket: MockSocket = {
    readyState: WebSocket.OPEN,
    send: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
  };
  h.socket = socket;
  h.openSession = vi.fn().mockResolvedValue(socket);
  return socket;
}

function fireOnmessage(data: string) {
  if (!h.socket?.onmessage) {
    throw new Error('socket.onmessage not set yet');
  }
  act(() => {
    h.socket!.onmessage!(new MessageEvent('message', { data }));
  });
}

async function renderOpen() {
  createMockSocket();
  const result = renderWithProviders(<AssistantDockHost />, {
    route: '/orgs/test-org',
  });

  // Open the dock via [data-assistant-open].
  const trigger = document.createElement('span');
  trigger.setAttribute('data-assistant-open', '');
  document.body.appendChild(trigger);
  trigger.click();
  document.body.removeChild(trigger);

  // Wait for the WS connection to be established (openSession called, then
  // onopen callback fires).
  await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(1));
  // The component sets onopen inside the .then() callback, which happens
  // after the opener resolves.  Fire onopen so the handshake is sent.
  act(() => {
    h.socket!.onopen?.();
  });

  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.socket = null;
  h.status = configuredStatus();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AssistantDockHost — pre-ack raw-frame tolerance (OPTION 3)', () => {
  test('pre-ack raw frame: ignored, not surfaced to the user', async () => {
    await renderOpen();

    // The handshake is sent on open.
    expect(h.socket!.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'handshake', protocol: 'json-chat', version: 1 }),
    );

    // Simulate a raw PTY frame arriving BEFORE the "ready" ack.
    // This is the "assistant ready" greeting that the PTY emits at startup.
    fireOnmessage('assistant ready\n');

    // The dock is still in "connecting" state (loading skeleton shown).
    // The pre-ack raw frame was ignored — it doesn't appear in the DOM.
    await waitFor(() => {
      expect(screen.getByLabelText('Loading')).toBeInTheDocument();
    });
    expect(screen.queryByText('assistant ready')).toBeNull();
  });

  test('ready ack: exits connecting state', async () => {
    await renderOpen();

    // Send the "ready" ack (server-side handshake response).
    fireOnmessage(JSON.stringify({ type: 'status', code: 'ready' }));

    // The loading skeleton should be gone.
    await waitFor(() => {
      expect(screen.queryByLabelText('Loading')).toBeNull();
    });
  });

  test('post-ack structured output: displayed as a message', async () => {
    await renderOpen();

    // Ack first.
    fireOnmessage(JSON.stringify({ type: 'status', code: 'ready' }));

    // Then structured output.
    fireOnmessage(
      JSON.stringify({ type: 'output', text: 'Hello from assistant' }),
    );

    // The message should appear in the DOM.
    await waitFor(() => {
      expect(screen.getByText('Hello from assistant')).toBeInTheDocument();
    });
    // The empty-state prompt should be gone.
    expect(screen.queryByText(/Ask the assistant anything/)).toBeNull();
  });

  test('post-ack raw frame: handled gracefully as assistant output', async () => {
    await renderOpen();

    // Ack first.
    fireOnmessage(JSON.stringify({ type: 'status', code: 'ready' }));

    // A non-JSON frame after the ack (should never happen in structured mode
    // but handled gracefully if it does).
    fireOnmessage('unexpected raw text');

    await waitFor(() => {
      expect(screen.getByText('unexpected raw text')).toBeInTheDocument();
    });
  });

  test('pre-ack raw + ack + structured output: full sequence', async () => {
    await renderOpen();

    // 1. Pre-ack raw frame — must NOT surface.  Dock is in connecting/loading state.
    fireOnmessage('assistant ready\n');

    // Confirm loading state is shown (not the raw text).
    await waitFor(() => {
      expect(screen.getByLabelText('Loading')).toBeInTheDocument();
    });
    expect(screen.queryByText('assistant ready')).toBeNull();

    // 2. "ready" ack.
    fireOnmessage(JSON.stringify({ type: 'status', code: 'ready' }));

    await waitFor(() => {
      expect(screen.queryByLabelText('Loading')).toBeNull();
    });

    // 3. Structured output.
    fireOnmessage(
      JSON.stringify({ type: 'output', text: 'analysis complete' }),
    );

    // The structured output appears.
    await waitFor(() => {
      expect(screen.getByText('analysis complete')).toBeInTheDocument();
    });

    // The pre-ack raw frame is NOT visible.
    expect(screen.queryByText('assistant ready')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// DOCK-02 (THR-030) — header: title alignment, connected-status line,
// data-backed executor pill.
// ---------------------------------------------------------------------------

describe('AssistantDockHost — DOCK-02 header (THR-030)', () => {
  test('title reads "Ranch Assistant", not the old "System Assistant"', () => {
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    expect(screen.getByText('Ranch Assistant')).toBeInTheDocument();
    expect(screen.queryByText('System Assistant')).toBeNull();
  });

  test('connected-status line is rendered in the header', () => {
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    // The static descriptor anchors the status line and is always present.
    expect(screen.getByText(/operates your runtime/)).toBeInTheDocument();
  });

  test('status line shows a live "Connected" label after the ready ack', async () => {
    await renderOpen();
    fireOnmessage(JSON.stringify({ type: 'status', code: 'ready' }));
    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument();
    });
  });

  test('executor pill renders the data-backed selected_executor', () => {
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    expect(screen.getByText('claude')).toBeInTheDocument();
  });

  test('executor pill is OMITTED when selected_executor is unbacked (null)', () => {
    h.status = {
      data: { state: 'configured', selected_executor: null, workspace_path: '/ws', detail: null },
      isLoading: false,
      isError: false,
      error: null,
    };
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    // No hardcoded executor name is fabricated when the field is null.
    expect(screen.queryByText('claude')).toBeNull();
    expect(screen.queryByText('codex')).toBeNull();
  });
});

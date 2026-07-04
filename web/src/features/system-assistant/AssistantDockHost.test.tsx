/**
 * Tests for AssistantDockHost — A-mode (structured TurnFrame) dock.
 *
 * The dock rides the /assistant/a-mode WS and renders the conversation exactly
 * like a thread. These tests mock the assistant hooks and drive a mock
 * WebSocket through the normalized TurnFrame protocol. Key behaviours:
 *   1. `history` frame → hydrates the persisted conversation into bubbles.
 *   2. `status{ready}` → exits the connecting/loading state.
 *   3. `turn_start` + `text_delta`* → aggregate into ONE assistant bubble;
 *      a TypingBubble shows while in flight, cleared on `turn_end`.
 *   4. Sending a message → optimistic user bubble + a `{type:"start"}` frame,
 *      with NO server input-echo.
 *   5. `error` frame → inline error alert.
 *   6. DOCK-02 header (title, live status line, data-backed executor pill).
 */
import { waitFor, act, fireEvent, screen } from '@testing-library/react';
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

interface MockConversation {
  id: string;
  title: string;
  created_at: string | null;
  active: boolean;
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
    // Multi-conversation switcher (THR-056 STEP-B) mock surface.
    conversations: MockConversation[];
    createFn: ReturnType<typeof vi.fn>;
    activateFn: ReturnType<typeof vi.fn>;
    renameFn: ReturnType<typeof vi.fn>;
    deleteFn: ReturnType<typeof vi.fn>;
    refetchFn: ReturnType<typeof vi.fn>;
  } = {
    socket: null,
    openSession: vi.fn<() => Promise<MockSocket>>(),
    status: {
      data: { state: 'configured', selected_executor: 'claude', workspace_path: '/ws', detail: null },
      isLoading: false,
      isError: false,
      error: null,
    },
    conversations: [],
    createFn: vi.fn(),
    activateFn: vi.fn(),
    renameFn: vi.fn(),
    deleteFn: vi.fn(),
    refetchFn: vi.fn(),
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
  useAssistantAModeSessionOpener: () => h.openSession,
  useConversations: () => ({
    data: h.conversations,
    isLoading: false,
    isError: false,
    error: null,
    refetch: h.refetchFn,
  }),
  useCreateConversation: () => ({ mutateAsync: h.createFn, isPending: false }),
  useActivateConversation: () => ({ mutateAsync: h.activateFn, isPending: false }),
  useRenameConversation: () => ({ mutateAsync: h.renameFn, isPending: false }),
  useDeleteConversation: () => ({ mutateAsync: h.deleteFn, isPending: false }),
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

function fireFrame(frame: Record<string, unknown>) {
  if (!h.socket?.onmessage) {
    throw new Error('socket.onmessage not set yet');
  }
  act(() => {
    h.socket!.onmessage!(new MessageEvent('message', { data: JSON.stringify(frame) }));
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

  // Wait for the WS connection to be established and the message handler wired
  // (the component sets onmessage inside the opener's .then()).
  await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(h.socket?.onmessage).not.toBeNull());

  return result;
}

async function ready() {
  fireFrame({ type: 'status', code: 'ready' });
  await waitFor(() => {
    expect(screen.queryByLabelText('Loading')).toBeNull();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  h.socket = null;
  h.status = configuredStatus();
  // Two conversations, newest-first, with the first active.
  h.conversations = [
    { id: 'conv-a', title: 'Ranch status', created_at: null, active: true },
    { id: 'conv-b', title: 'Deploy plan', created_at: null, active: false },
  ];
  h.createFn = vi
    .fn()
    .mockResolvedValue({ id: 'conv-new', title: 'New conversation', created_at: null, active: true });
  h.activateFn = vi.fn().mockResolvedValue({ success: true });
  h.renameFn = vi.fn().mockResolvedValue({ success: true });
  h.deleteFn = vi.fn().mockResolvedValue({ success: true });
  h.refetchFn = vi.fn();
});

// ---------------------------------------------------------------------------
// Tests — A-mode protocol
// ---------------------------------------------------------------------------

describe('AssistantDockHost — A-mode TurnFrame protocol', () => {
  test('opens the A-mode WS when the dock opens', async () => {
    await renderOpen();
    expect(h.openSession).toHaveBeenCalledTimes(1);
  });

  test('status{ready}: exits the connecting/loading state', async () => {
    await renderOpen();
    fireFrame({ type: 'status', code: 'ready' });
    await waitFor(() => {
      expect(screen.queryByLabelText('Loading')).toBeNull();
    });
  });

  test('history frame hydrates the persisted conversation into bubbles', async () => {
    await renderOpen();
    fireFrame({
      type: 'history',
      turns: [
        {
          id: 't1',
          prompt: 'what is my ranch status',
          started_at: '2026-07-02T10:00:00Z',
          frames: [
            { type: 'turn_start', role: 'assistant' },
            { type: 'text_delta', text: 'All systems ' },
            { type: 'text_delta', text: 'nominal.' },
            { type: 'turn_end', role: 'assistant' },
          ],
        },
      ],
    });

    await waitFor(() => {
      expect(screen.getByText('what is my ranch status')).toBeInTheDocument();
    });
    expect(screen.getByText('All systems nominal.')).toBeInTheDocument();
  });

  test('turn_start + text_delta* aggregate into ONE assistant bubble', async () => {
    await renderOpen();
    await ready();

    fireFrame({ type: 'turn_start', role: 'assistant' });
    fireFrame({ type: 'text_delta', text: 'Hel' });
    fireFrame({ type: 'text_delta', text: 'lo world' });

    // A single aggregated bubble — not one bubble per delta.
    await waitFor(() => {
      expect(screen.getByText('Hello world')).toBeInTheDocument();
    });
    expect(screen.queryByText('Hel')).toBeNull();

    // TypingBubble is shown while the turn is in flight.
    expect(screen.getByLabelText('claude is replying')).toBeInTheDocument();

    // turn_end clears the typing indicator.
    fireFrame({ type: 'turn_end', role: 'assistant' });
    await waitFor(() => {
      expect(screen.queryByLabelText('claude is replying')).toBeNull();
    });
    // The aggregated text survives after turn_end.
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  test('tool_call/tool_result surface transparently within the turn', async () => {
    await renderOpen();
    await ready();

    fireFrame({ type: 'turn_start', role: 'assistant' });
    fireFrame({ type: 'tool_call', name: 'bash', input: { cmd: 'ls' } });
    fireFrame({ type: 'text_delta', text: 'done' });
    fireFrame({ type: 'tool_result', name: 'bash', ok: true });
    fireFrame({ type: 'turn_end', role: 'assistant' });

    await waitFor(() => {
      expect(screen.getByText('bash')).toBeInTheDocument();
    });
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  test('sending: optimistic user bubble + start frame, no echo', async () => {
    await renderOpen();
    await ready();

    const composer = screen.getByLabelText('Assistant composer');
    fireEvent.change(composer, { target: { value: 'deploy the web' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    // Optimistic user bubble rendered immediately.
    await waitFor(() => {
      expect(screen.getByText('deploy the web')).toBeInTheDocument();
    });

    // A start frame is sent to the server (NOT a legacy chat frame), targeting
    // the active conversation so the turn lands on the right conversation.
    expect(h.socket!.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'start', text: 'deploy the web', conversation_id: 'conv-a' }),
    );

    // Only ONE copy of the message exists — the server does not echo the user
    // turn back, so no duplicate appears.
    expect(screen.getAllByText('deploy the web')).toHaveLength(1);
  });

  test('error frame: surfaced as an inline alert', async () => {
    await renderOpen();
    fireFrame({ type: 'error', message: 'a-mode-unavailable: use full session.' });
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('a-mode-unavailable');
    });
  });

  test('non-JSON frames are dropped, never surfaced as chat', async () => {
    await renderOpen();
    await ready();
    act(() => {
      h.socket!.onmessage!(new MessageEvent('message', { data: 'raw pty noise' }));
    });
    expect(screen.queryByText('raw pty noise')).toBeNull();
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

  test('status line shows a live "Connected" label after the ready status', async () => {
    await renderOpen();
    fireFrame({ type: 'status', code: 'ready' });
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

// ---------------------------------------------------------------------------
// THR-056 STEP-B — multi-conversation switcher: list, new, switch, rename,
// delete, and the switch->WS-reattach wiring.
// ---------------------------------------------------------------------------

async function expandSwitcher() {
  fireEvent.click(screen.getByRole('button', { name: 'Switch conversation' }));
  await waitFor(() => {
    expect(screen.getByLabelText('Conversations')).toBeInTheDocument();
  });
}

describe('AssistantDockHost — conversation switcher (THR-056 STEP-B)', () => {
  test('shows the active conversation title and a New conversation action', async () => {
    await renderOpen();
    await ready();
    // Active conversation title anchors the collapsed switcher strip.
    expect(
      screen.getByRole('button', { name: 'Switch conversation' }),
    ).toHaveTextContent('Ranch status');
    expect(
      screen.getByRole('button', { name: 'New conversation' }),
    ).toBeInTheDocument();
  });

  test('expanding lists every conversation (newest-first, active marked)', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();

    const list = screen.getByLabelText('Conversations');
    const rows = list.querySelectorAll('li');
    expect(rows).toHaveLength(2);
    // Order mirrors the backend list (newest-first): conv-a then conv-b.
    expect(rows[0]).toHaveTextContent('Ranch status');
    expect(rows[1]).toHaveTextContent('Deploy plan');
    // The active conversation's switch button carries aria-current.
    expect(screen.getByRole('button', { name: 'Ranch status' })).toHaveAttribute(
      'aria-current',
      'true',
    );
  });

  test('+ New conversation calls createConversation and re-attaches the WS', async () => {
    await renderOpen();
    await ready();
    expect(h.openSession).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));

    await waitFor(() => expect(h.createFn).toHaveBeenCalledTimes(1));
    // Re-attaches so the (empty) new conversation is loaded.
    await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(2));
  });

  test('switching a conversation activates it and re-attaches to replay history', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();
    expect(h.openSession).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Deploy plan' }));

    await waitFor(() => expect(h.activateFn).toHaveBeenCalledWith('conv-b'));
    await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(2));

    // After re-attach, the server replays conv-b's history on the new socket.
    await waitFor(() => expect(h.socket?.onmessage).not.toBeNull());
    fireFrame({
      type: 'history',
      turns: [
        {
          id: 'tb',
          prompt: 'prompt from B',
          started_at: '2026-07-04T10:00:00Z',
          frames: [
            { type: 'turn_start', role: 'assistant' },
            { type: 'text_delta', text: 'response from B' },
            { type: 'turn_end', role: 'assistant' },
          ],
        },
      ],
    });
    await waitFor(() => {
      expect(screen.getByText('prompt from B')).toBeInTheDocument();
    });
    expect(screen.getByText('response from B')).toBeInTheDocument();
  });

  test('switching to the already-active conversation does not re-attach', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();

    fireEvent.click(screen.getByRole('button', { name: 'Ranch status' }));
    // No activate + no reconnect for the current conversation.
    expect(h.activateFn).not.toHaveBeenCalled();
    expect(h.openSession).toHaveBeenCalledTimes(1);
  });

  test('inline rename commits via renameConversation on Enter', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();

    fireEvent.click(screen.getAllByLabelText('Rename conversation')[0]);
    const input = screen.getByLabelText('Conversation title');
    expect(input).toHaveValue('Ranch status');
    fireEvent.change(input, { target: { value: 'Ranch health' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(h.renameFn).toHaveBeenCalledWith({ id: 'conv-a', title: 'Ranch health' }),
    );
  });

  test('delete calls deleteConversation for the targeted conversation', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();

    // Delete the non-active conversation (conv-b, second row).
    fireEvent.click(screen.getAllByLabelText('Delete conversation')[1]);
    await waitFor(() => expect(h.deleteFn).toHaveBeenCalledWith('conv-b'));
    // Deleting a non-active conversation leaves the active pointer untouched,
    // so no re-attach is triggered.
    expect(h.openSession).toHaveBeenCalledTimes(1);
  });

  test('deleting the active conversation re-attaches to the new active', async () => {
    await renderOpen();
    await ready();
    await expandSwitcher();

    fireEvent.click(screen.getAllByLabelText('Delete conversation')[0]);
    await waitFor(() => expect(h.deleteFn).toHaveBeenCalledWith('conv-a'));
    await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(2));
  });

  test('a 409 (in-flight) delete surfaces a non-destructive message', async () => {
    h.deleteFn = vi.fn().mockRejectedValue(new Error('409 conflict'));
    await renderOpen();
    await ready();
    await expandSwitcher();

    fireEvent.click(screen.getAllByLabelText('Delete conversation')[1]);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        "Can't delete a conversation while a turn is running.",
      );
    });
    // The failed delete must NOT re-attach or drop the conversation view.
    expect(h.openSession).toHaveBeenCalledTimes(1);
  });

  test('a completed turn refetches conversations (backend auto-title)', async () => {
    await renderOpen();
    await ready();

    fireFrame({ type: 'turn_start', role: 'assistant' });
    fireFrame({ type: 'text_delta', text: 'ok' });
    fireFrame({ type: 'turn_end', role: 'assistant' });

    await waitFor(() => expect(h.refetchFn).toHaveBeenCalled());
  });
});

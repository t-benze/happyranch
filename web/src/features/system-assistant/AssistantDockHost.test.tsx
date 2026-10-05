/**
 * Tests for AssistantDockHost — A-mode (structured TurnFrame) dock.
 *
 * The dock rides the /assistant/a-mode WS and renders the conversation exactly
 * like a thread. These tests use the real AssistantApi provider (via
 * AppProvider) + MSW for HTTP network evidence and a mocked
 * openAssistantAModeSession for WebSocket control.
 *
 * Key behaviours:
 *   1. `history` frame → hydrates the persisted conversation into bubbles.
 *   2. `status{ready}` → exits the connecting/loading state.
 *   3. `turn_start` + `text_delta`* → aggregate into ONE assistant bubble;
 *      a TypingBubble shows while in flight, cleared on `turn_end`.
 *   4. Sending a message → optimistic user bubble + a `{type:"start"}` frame,
 *      with NO server input-echo.
 *   5. `error` frame → inline error alert.
 *   6. DOCK-02 header — title, no decorative connection-status line.
 *   7. Network-evidence: closed dock → 0 requests, open → exactly 1.
 */
import { waitFor, act, fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { renderWithProviders, I18nTestBoundary, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { AssistantDockHost } from './AssistantDockHost';
import type { AssistantStatus } from '@/lib/api/types';
import type { ConversationSummary } from '@/hooks/assistant';

// ---------------------------------------------------------------------------
// Shared mock state (hoisted so vi.mock factories can close over it)
// ---------------------------------------------------------------------------

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
  const socket: { current: MockSocket | null } = { current: null };
  const openSessionMock = vi.fn<() => Promise<MockSocket>>();
  return { socket, openSessionMock };
});

// ---------------------------------------------------------------------------
// Mock only the WebSocket opener; all HTTP functions remain real for MSW.
// ---------------------------------------------------------------------------

vi.mock('@/lib/api/assistant', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('@/lib/api/assistant')>();
  return {
    ...original,
    openAssistantAModeSession: h.openSessionMock,
  };
});

// ---------------------------------------------------------------------------
// Helpers — mock WebSocket
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
  h.socket.current = socket;
  h.openSessionMock.mockResolvedValue(socket);
  return socket;
}

function fireFrame(frame: Record<string, unknown>) {
  if (!h.socket.current?.onmessage) {
    throw new Error('socket.onmessage not set yet');
  }
  act(() => {
    h.socket.current!.onmessage!(
      new MessageEvent('message', { data: JSON.stringify(frame) }),
    );
  });
}

// ---------------------------------------------------------------------------
// Helpers — MSW stubs
// ---------------------------------------------------------------------------

/** Shared configured-status fixture for the happy path. */
const CONFIGURED: AssistantStatus = {
  state: 'configured',
  selected_executor: 'claude',
  workspace_path: '/rt/system/assistant/workspace',
  detail: null,
};

const EMPTY_CONVERSATIONS: ConversationSummary[] = [];

function stubStatus(status: AssistantStatus) {
  server.use(
    http.get('/api/v1/assistant/status', () => HttpResponse.json(status)),
  );
}

function stubConversations(convs: ConversationSummary[] = []) {
  server.use(
    http.get('/api/v1/assistant/a-mode/conversations', () =>
      HttpResponse.json(convs),
    ),
  );
}

/** Status stub that counts every GET. */
function countingStatusStub(status?: AssistantStatus): { count: () => number } {
  let count = 0;
  const payload = status ?? CONFIGURED;
  server.use(
    http.get('/api/v1/assistant/status', () => {
      count += 1;
      return HttpResponse.json(payload);
    }),
  );
  return { count: () => count };
}

// ---------------------------------------------------------------------------
// Helpers — open / close
// ---------------------------------------------------------------------------

/** Configure MSW for the happy path (open dock → configured assistant). */
function stubHappy() {
  stubStatus(CONFIGURED);
  stubConversations(EMPTY_CONVERSATIONS);
}

async function openDock(sock: MockSocket) {
  const trigger = document.createElement('span');
  trigger.setAttribute('data-assistant-open', '');
  document.body.appendChild(trigger);
  trigger.click();
  document.body.removeChild(trigger);
  // Wait for the WS connection to be established.
  await waitFor(() => expect(h.openSessionMock).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(sock.onmessage).not.toBeNull());
}

async function openAndReady(sock: MockSocket) {
  await openDock(sock);
  fireFrame({ type: 'status', code: 'ready' });
  await waitFor(() => expect(screen.queryByLabelText('Loading')).toBeNull());
}

// ---------------------------------------------------------------------------
// beforeEach
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  h.socket.current = null;
  h.openSessionMock.mockReset();
  sessionStorage.setItem('happyranch.token', 'tok');
  stubHappy();
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Helper — fake-timer-aware async flush
// ---------------------------------------------------------------------------

/** Advance fake timers by `ms` inside act, flushing microtasks + macrotasks. */
async function flushTimers(ms: number) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

// ============================================================================
// NETWORK EVIDENCE — real provider + MSW request counting
// ============================================================================

describe('AssistantDockHost — network evidence (real provider + MSW)', () => {
  test('1. closed dock: zero assistant status requests', async () => {
    const counter = countingStatusStub();
    createMockSocket(); // still needed so the provider doesn't NPE

    // Install fake timers BEFORE mounting so any refetchInterval is captured
    // by the fake clock and not by a native setInterval.
    vi.useFakeTimers();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });

    // Advance past the old 5 000 ms refetchInterval so this assertion
    // FAILS if polling is restored.
    await flushTimers(6_000);
    expect(counter.count()).toBe(0);
  });

  test('2. open dock: exactly one fresh status request', async () => {
    const counter = countingStatusStub();
    const sock = createMockSocket();

    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });

    // Before open: zero requests.
    expect(counter.count()).toBe(0);

    await openDock(sock);

    // After open: exactly one.
    await waitFor(() => expect(counter.count()).toBe(1));
  });

  test('3. no interval request is scheduled after dock opens', async () => {
    const counter = countingStatusStub();
    createMockSocket();

    // Install fake timers BEFORE mounting.
    vi.useFakeTimers();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });

    // Open dock under fake timers: click trigger, then flush state + fetch.
    const trigger = document.createElement('span');
    trigger.setAttribute('data-assistant-open', '');
    document.body.appendChild(trigger);
    trigger.click();
    document.body.removeChild(trigger);
    await flushTimers(200);
    expect(counter.count()).toBe(1);

    // Advance past the old 5 000 ms refetchInterval.
    await flushTimers(6_000);
    expect(counter.count()).toBe(1);
  });

  test('4. close + reopen: staleTime prevents refetch in same tree, no interval', async () => {
    // NOTE: The production QueryClient has staleTime: 30_000. Reopening the
    // dock while cached data is fresh reuses it — this is the idiomatic
    // TanStack Query behaviour. For a proof that reopening DOES issue a fresh
    // request when data is stale, see test 5 below.
    const counter = countingStatusStub();
    createMockSocket();

    // Install fake timers BEFORE mounting.
    vi.useFakeTimers();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });

    // Open dock under fake timers.
    let triggerEl = document.createElement('span');
    triggerEl.setAttribute('data-assistant-open', '');
    document.body.appendChild(triggerEl);
    triggerEl.click();
    document.body.removeChild(triggerEl);
    await flushTimers(200);
    expect(counter.count()).toBe(1);

    // Close via the dialog's close button.
    fireEvent.click(screen.getByRole('button', { name: 'Close assistant' }));
    await flushTimers(200);
    const dialog = screen.getByRole('dialog', { name: 'Ranch Assistant' });
    expect(dialog.className).toContain('translate-x-full');

    // Advance past the old 5 000 ms refetchInterval.
    await flushTimers(6_000);
    expect(counter.count()).toBe(1);

    // Re-open — data is still fresh (staleTime 30s), no refetch.
    triggerEl = document.createElement('span');
    triggerEl.setAttribute('data-assistant-open', '');
    document.body.appendChild(triggerEl);
    triggerEl.click();
    document.body.removeChild(triggerEl);
    await flushTimers(6_000);
    expect(counter.count()).toBe(1); // cache hit, not a second request
  });

  test('5. staleTime-zero QueryClient: reopen issues a second fresh request', async () => {
    // Same-instance proof: when data IS stale, reopen does refetch.
    // This is the nearest behaviorally equivalent proof for the staleTime gate.
    const counter = countingStatusStub();
    const client = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 0, refetchOnWindowFocus: false, retry: false },
      },
    });
    createMockSocket();
    const route = '/orgs/test-org';

    // Install fake timers BEFORE mounting.
    vi.useFakeTimers();
    render(
      <MemoryRouter initialEntries={[route]}>
        <I18nTestBoundary>
          <AppProvider client={client}>
            <AssistantDockHost />
          </AppProvider>
        </I18nTestBoundary>
      </MemoryRouter>,
    );

    // Open: 1 request.
    let triggerEl = document.createElement('span');
    triggerEl.setAttribute('data-assistant-open', '');
    document.body.appendChild(triggerEl);
    triggerEl.click();
    document.body.removeChild(triggerEl);
    await flushTimers(200);
    expect(counter.count()).toBe(1);

    // Close via the dialog's close button.
    fireEvent.click(screen.getByRole('button', { name: 'Close assistant' }));
    await flushTimers(200);
    const dialog = screen.getByRole('dialog', { name: 'Ranch Assistant' });
    expect(dialog.className).toContain('translate-x-full');

    // Re-open: data is stale → 2nd request.
    triggerEl = document.createElement('span');
    triggerEl.setAttribute('data-assistant-open', '');
    document.body.appendChild(triggerEl);
    triggerEl.click();
    document.body.removeChild(triggerEl);
    await flushTimers(200);
    expect(counter.count()).toBe(2);

    // Advance past the old 5 000 ms refetchInterval.
    await flushTimers(6_000);
    expect(counter.count()).toBe(2);
  });
});

// ============================================================================
// A-mode TurnFrame protocol (mock WebSocket, real provider + MSW)
// ============================================================================

describe('AssistantDockHost — A-mode TurnFrame protocol', () => {
  test('opens the A-mode WS when the dock opens', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openDock(sock);
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
  });

  test('status{ready}: exits the connecting/loading state', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);
    expect(screen.queryByLabelText('Loading')).toBeNull();
  });

  test('history frame hydrates the persisted conversation into bubbles', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openDock(sock);

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
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);

    fireFrame({ type: 'turn_start', role: 'assistant' });
    fireFrame({ type: 'text_delta', text: 'Hel' });
    fireFrame({ type: 'text_delta', text: 'lo world' });

    await waitFor(() => {
      expect(screen.getByText('Hello world')).toBeInTheDocument();
    });
    expect(screen.queryByText('Hel')).toBeNull();

    // TypingBubble while turn is in flight.
    expect(screen.getByLabelText('claude is replying')).toBeInTheDocument();

    // turn_end clears the typing indicator.
    fireFrame({ type: 'turn_end', role: 'assistant' });
    await waitFor(() => {
      expect(
        screen.queryByLabelText('claude is replying'),
      ).toBeNull();
    });
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  test('tool_call/tool_result surface transparently within the turn', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);

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
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);

    const composer = screen.getByLabelText('Assistant composer');
    fireEvent.change(composer, { target: { value: 'deploy the web' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText('deploy the web')).toBeInTheDocument();
    });

    expect(sock.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'start', text: 'deploy the web' }),
    );

    expect(screen.getAllByText('deploy the web')).toHaveLength(1);
  });

  test('error frame: surfaced as an inline alert', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openDock(sock);

    fireFrame({
      type: 'error',
      message: 'a-mode-unavailable: use full session.',
    });
    await waitFor(() => {
      expect(
        screen.getByRole('alert'),
      ).toHaveTextContent('a-mode-unavailable');
    });
  });

  test('non-JSON frames are dropped, never surfaced as chat', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);

    act(() => {
      sock.onmessage!(new MessageEvent('message', { data: 'raw pty noise' }));
    });
    expect(screen.queryByText('raw pty noise')).toBeNull();
  });
});

// ============================================================================
// DOCK-02 (THR-030 / THR-078) — header
// ============================================================================

describe('AssistantDockHost — DOCK-02 header', () => {
  test('title reads "Ranch Assistant"', () => {
    createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    const header = screen.getByRole('dialog', { name: 'Ranch Assistant' });
    expect(header).toBeInTheDocument();
    expect(screen.getByText('Ranch Assistant')).toBeInTheDocument();
  });

  // THR-078: No decorative connection-status line — the dot, label,
  // "operates your runtime" descriptor, and executor pill are removed.
  test('header has no connection-status dot, label, descriptor, or executor pill', () => {
    createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });

    expect(screen.queryByText(/operates your runtime/)).toBeNull();
    expect(screen.queryByText('Connected')).toBeNull();
    expect(screen.queryByText('Connecting…')).toBeNull();
    expect(screen.queryByText('Not configured')).toBeNull();
    expect(screen.queryByText('Idle')).toBeNull();
    expect(screen.queryByText('Checking…')).toBeNull();
    expect(screen.queryByText('Disconnected')).toBeNull();
  });

  test('executor name is NOT rendered as a header pill', () => {
    createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    const header = screen.getByRole('dialog', { name: 'Ranch Assistant' });
    const headerDiv = header.querySelector('.border-b');
    const spans = headerDiv?.querySelectorAll('span');
    const textOnly = Array.from(spans ?? []).every(
      (el) =>
        el.textContent === 'Ranch Assistant' ||
        el.textContent === 'Conversations',
    );
    expect(textOnly).toBe(true);
  });
});

// ============================================================================
// Conversation switcher (THR-056 STEP-B)
// ============================================================================

describe('AssistantDockHost — conversation switcher', () => {
  async function openSwitcher() {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { route: '/orgs/test-org' });
    await openAndReady(sock);
    fireEvent.click(screen.getByRole('button', { name: 'Conversations' }));
    await waitFor(() => {
      expect(
        screen.getByRole('region', { name: 'Conversations' }),
      ).toBeInTheDocument();
    });
  }

  test('header toggle opens the switcher and lists conversations', async () => {
    stubConversations([
      {
        id: 'c1',
        title: 'Ranch status',
        created_at: '2026-07-04T10:00:00Z',
        active: true,
      },
      {
        id: 'c2',
        title: 'Spend review',
        created_at: '2026-07-03T10:00:00Z',
        active: false,
      },
    ]);
    await openSwitcher();
    expect(
      screen.getByRole('button', { name: 'Ranch status' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Spend review' }),
    ).toBeInTheDocument();
  });

  test('"New conversation" creates one and reconnects the WS', async () => {
    server.use(
      http.post('/api/v1/assistant/a-mode/conversations', () =>
        HttpResponse.json({
          id: 'conv-new',
          title: 'New conversation',
          created_at: '2026-07-04T12:00:00Z',
          active: true,
        }),
      ),
    );
    await openSwitcher();
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    await waitFor(() => expect(h.openSessionMock).toHaveBeenCalledTimes(2));
  });

  test('selecting a non-active conversation activates it and reconnects', async () => {
    stubConversations([
      {
        id: 'c1',
        title: 'Ranch status',
        created_at: '2026-07-04T10:00:00Z',
        active: true,
      },
      {
        id: 'c2',
        title: 'Spend review',
        created_at: '2026-07-03T10:00:00Z',
        active: false,
      },
    ]);
    server.use(
      http.post('/api/v1/assistant/a-mode/conversations/:id/activate', () =>
        HttpResponse.json({ success: true }),
      ),
    );
    await openSwitcher();
    fireEvent.click(screen.getByRole('button', { name: 'Spend review' }));
    await waitFor(() => expect(h.openSessionMock).toHaveBeenCalledTimes(2));
  });

  test('selecting the ACTIVE conversation just closes the switcher (no reconnect)', async () => {
    stubConversations([
      {
        id: 'c1',
        title: 'Ranch status',
        created_at: '2026-07-04T10:00:00Z',
        active: true,
      },
    ]);
    await openSwitcher();
    fireEvent.click(screen.getByRole('button', { name: 'Ranch status' }));
    await waitFor(() => {
      expect(
        screen.queryByRole('region', { name: 'Conversations' }),
      ).toBeNull();
    });
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
  });

  test('rename commits the new title', async () => {
    stubConversations([
      {
        id: 'c1',
        title: 'Ranch status',
        created_at: '2026-07-04T10:00:00Z',
        active: true,
      },
    ]);
    server.use(
      http.patch('/api/v1/assistant/a-mode/conversations/:id', () =>
        HttpResponse.json({ success: true }),
      ),
    );
    await openSwitcher();
    fireEvent.click(
      screen.getByRole('button', { name: 'Rename Ranch status' }),
    );
    const input = screen.getByLabelText('Conversation title');
    fireEvent.change(input, { target: { value: 'Renamed thread' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // The rename mutation fires; we verify the switcher still exists
    // (the mock returns success, the invalidation re-fetches).
    await waitFor(() => {
      expect(input).not.toBeInTheDocument();
    });
  });

  test('delete requires inline confirm, then calls delete and reconnects', async () => {
    stubConversations([
      {
        id: 'c1',
        title: 'Ranch status',
        created_at: '2026-07-04T10:00:00Z',
        active: true,
      },
      {
        id: 'c2',
        title: 'Spend review',
        created_at: '2026-07-03T10:00:00Z',
        active: false,
      },
    ]);
    server.use(
      http.delete('/api/v1/assistant/a-mode/conversations/:id', () =>
        HttpResponse.json({ success: true }),
      ),
    );
    await openSwitcher();
    fireEvent.click(
      screen.getByRole('button', { name: 'Delete Spend review' }),
    );
    // A bare trash click must NOT delete — it asks to confirm first.
    await new Promise((r) => setTimeout(r, 500));
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(h.openSessionMock).toHaveBeenCalledTimes(2));
  });
});

// THR-118: real data/provider/query path, only external WS transport doubled.
function changeDockLocale(locale: 'en' | 'zh-CN') {
  act(() => window.dispatchEvent(new StorageEvent('storage', {
    key: 'happyranch.ui.locale', newValue: locale,
  })));
}

describe('Assistant dock mounted localization', () => {
  test('localized history and tool/inflight chrome preserve draft, focus, selection and connection ledger', async () => {
    let statusReads = 0;
    let conversationReads = 0;
    server.use(
      http.get('/api/v1/assistant/status', () => {
        statusReads += 1;
        return HttpResponse.json({ ...CONFIGURED, selected_executor: null });
      }),
      http.get('/api/v1/assistant/a-mode/conversations', () => {
        conversationReads += 1;
        return HttpResponse.json([{ id: 'raw-id', title: 'Raw «会话»', active: true, created_at: null }]);
      }),
    );
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />);
    await openAndReady(sock);
    fireFrame({ type: 'history', turns: [{ prompt: 'Raw prompt /任务', started_at: '2026-07-04T10:00:00Z', frames: [
      { type: 'turn_start' }, { type: 'text_delta', text: '**Raw reply** /答复' }, { type: 'turn_end' },
    ] }] });
    fireFrame({ type: 'turn_start' });
    fireFrame({ type: 'tool_call', name: 'raw_tool/工具' });
    fireFrame({ type: 'tool_call' });
    fireFrame({ type: 'tool_call', name: 'tool' });
    const input = screen.getByRole('textbox', { name: 'Assistant composer' }) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '  original /prompt  ' } });
    // Let the existing open-focus timer finish before establishing focus.
    await new Promise((resolve) => setTimeout(resolve, 120));
    input.focus(); input.setSelectionRange(2, 10);
    const dialog = screen.getByRole('dialog', { name: 'Ranch Assistant' });
    const before = [statusReads, conversationReads, h.openSessionMock.mock.calls.length, sock.close.mock.calls.length, sock.send.mock.calls.length];
    changeDockLocale('zh-CN');
    expect(screen.getByRole('dialog', { name: '牧场助手' })).toBe(dialog);
    expect(screen.getByRole('textbox', { name: '助手输入框' })).toBe(input);
    expect(input).toHaveValue('  original /prompt  ');
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([2, 10]);
    expect(screen.getByText('你')).toBeInTheDocument();
    expect(screen.getByText('Raw prompt /任务')).toBeInTheDocument();
    expect(screen.getByText('Raw reply')).toBeInTheDocument();
    expect(screen.getByLabelText('工具活动')).toHaveTextContent('raw_tool/工具');
    expect(screen.getByText('工具')).toBeInTheDocument();
    expect(screen.getByText('tool')).toBeInTheDocument();
    expect(screen.getByLabelText('助手正在回复')).toHaveTextContent('正在回复…');
    expect(screen.getByRole('button', { name: '会话' })).toHaveTextContent('Raw «会话»');
    changeDockLocale('en');
    expect(screen.getByRole('textbox', { name: 'Assistant composer' })).toBe(input);
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([2, 10]);
    expect([statusReads, conversationReads, h.openSessionMock.mock.calls.length, sock.close.mock.calls.length, sock.send.mock.calls.length]).toEqual(before);
    fireFrame({ type: 'tool_result', name: 'raw_tool/工具', ok: false });
    fireFrame({ type: 'turn_end' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(sock.send).toHaveBeenCalledTimes(1);
    expect(sock.send).toHaveBeenCalledWith(JSON.stringify({ type: 'start', text: 'original /prompt' }));
    expect(input).toHaveValue('');
  });

  test.each([
    ['status', 'Assistant error.', '助手出错。'],
    ['error', 'Unknown error', '未知错误'],
    ['socket', 'WebSocket connection failed.', 'WebSocket 连接失败。'],
    ['disconnected', 'Not connected. Reopen the dock to reconnect.', '尚未连接。请重新打开助手面板以连接。'],
  ])('visible %s fallback updates immediately without reopening the socket', async (kind, english, chinese) => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />);
    await openAndReady(sock);
    if (kind === 'status') fireFrame({ type: 'status', code: 'error' });
    if (kind === 'error') fireFrame({ type: 'error' });
    if (kind === 'socket') act(() => sock.onerror!());
    if (kind === 'disconnected') {
      sock.readyState = WebSocket.CLOSED;
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'draft' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    }
    expect(screen.getByRole('alert').textContent).toBe(english);
    changeDockLocale('zh-CN');
    expect(screen.getByRole('alert').textContent).toBe(chinese);
    changeDockLocale('en');
    expect(screen.getByRole('alert').textContent).toBe(english);
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
    expect(sock.close).not.toHaveBeenCalled();
  });

  test.each(['Assistant error.', 'Unknown error', '', 'Raw 错误\n<tag>'])('raw detail/message %j remains byte-exact through locale switches', async (raw) => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />);
    await openAndReady(sock);
    for (const frame of [{ type: 'status', code: 'error', detail: raw }, { type: 'error', message: raw }]) {
      fireFrame(frame);
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toBe(raw);
      changeDockLocale('zh-CN');
      expect(screen.getByRole('alert')).toBe(alert);
      expect(alert.textContent).toBe(raw);
      changeDockLocale('en');
      expect(alert.textContent).toBe(raw);
    }
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
    expect(sock.close).not.toHaveBeenCalled();
  });

  test.each(['Connection failed: diagnostic', 'Assistant error.', ''])('caught connection diagnostic %j keeps exact detail under localized wrapper', async (raw) => {
    h.openSessionMock.mockRejectedValue(raw);
    renderWithProviders(<AssistantDockHost />);
    const trigger = document.createElement('button');
    trigger.setAttribute('data-assistant-open', '');
    document.body.appendChild(trigger); trigger.click(); trigger.remove();
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(`Connection failed: ${raw}`));
    changeDockLocale('zh-CN');
    expect(screen.getByRole('alert').textContent).toBe(`连接失败：${raw}`);
    changeDockLocale('en');
    expect(screen.getByRole('alert').textContent).toBe(`Connection failed: ${raw}`);
    expect(h.openSessionMock).toHaveBeenCalledTimes(1);
  });

  test('Chinese connecting, empty, composer/help, raw executor and keyboard behavior use existing controls', async () => {
    const sock = createMockSocket();
    renderWithProviders(<AssistantDockHost />, { i18n: { adapter: savedLocaleAdapter('zh-CN') } });
    await openDock(sock);
    expect(screen.getByLabelText('加载中')).toBeInTheDocument();
    const send = screen.getByRole('button', { name: '发送' });
    expect(send).toBeDisabled();
    fireFrame({ type: 'status', code: 'ready' });
    expect(screen.getByText('向助手提问，或输入 / 运行命令。')).toBeInTheDocument();
    const input = screen.getByRole('textbox', { name: '助手输入框' });
    expect(input).toHaveAttribute('placeholder', '向助手提问，或输入 / 运行命令…');
    expect(screen.getByText(/发送 ·/)).toHaveTextContent('Enter 发送 · Shift+Enter 换行');
    fireFrame({ type: 'turn_start' });
    expect(screen.getByLabelText('claude正在回复')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'payload' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(sock.send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(sock.send).toHaveBeenCalledWith(JSON.stringify({ type: 'start', text: 'payload' }));
    expect(screen.getByRole('button', { name: '关闭助手' })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'next draft' } });
    const first = screen.getByRole('button', { name: '会话' });
    first.focus(); fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(send);
    fireEvent.keyDown(send, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '会话' }));
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.getByRole('dialog', { name: '牧场助手' }).className).toContain('translate-x-full');
  });

  test.each(['loading', 'unconfigured', 'status-error'])('Chinese status %s renders owned fallback without a session', async (state) => {
    server.use(http.get('/api/v1/assistant/status', () => state === 'loading'
      ? new Promise(() => {})
      : state === 'status-error' ? HttpResponse.json({ detail: 'raw backend' }, { status: 500 })
        : HttpResponse.json({ ...CONFIGURED, state: 'unconfigured' })));
    renderWithProviders(<AssistantDockHost />, { i18n: { adapter: savedLocaleAdapter('zh-CN') } });
    const trigger = document.createElement('button'); trigger.setAttribute('data-assistant-open', '');
    document.body.appendChild(trigger); trigger.click(); trigger.remove();
    const expected = state === 'loading' ? '加载中…' : state === 'status-error' ? '无法加载助手状态。' : '助手尚未就绪。请在设置 → 助手中进行配置。';
    await waitFor(() => expect(screen.getByText(expected)).toBeInTheDocument());
    expect(h.openSessionMock).not.toHaveBeenCalled();
    if (state !== 'loading') {
      const fallback = screen.getByRole('alert');
      expect(fallback.textContent).toBe('助手尚未配置。请在设置中进行配置。');
      changeDockLocale('en');
      expect(fallback.textContent).toBe('Assistant not configured. Set it up in Settings.');
    }
  });
});


test.each(['assistant', ''])('raw executor %j is not mistaken for the app speaker fallback', async (executor) => {
  stubStatus({ ...CONFIGURED, selected_executor: executor });
  const sock = createMockSocket();
  renderWithProviders(<AssistantDockHost />);
  await openAndReady(sock);
  fireFrame({ type: 'turn_start' });
  changeDockLocale('zh-CN');
  expect(screen.getByLabelText(`${executor}正在回复`)).toBeInTheDocument();
  changeDockLocale('en');
  expect(screen.getByLabelText(`${executor} is replying`.trim())).toHaveAttribute('aria-label', `${executor} is replying`);
  expect(h.openSessionMock).toHaveBeenCalledTimes(1);
  expect(sock.close).not.toHaveBeenCalled();
});

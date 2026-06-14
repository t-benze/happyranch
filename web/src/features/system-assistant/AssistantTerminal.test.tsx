/**
 * Unit coverage for AssistantTerminal — the in-browser PTY terminal.
 *
 * The component pulls in xterm + a live WebSocket; SystemAssistantPage.test.tsx
 * mocks it away, so this file is the only place the PTY protocol wiring is
 * actually exercised. We mock @xterm/xterm (Terminal), @xterm/addon-fit
 * (FitAddon), and the session opener hook so no real socket or canvas is
 * touched, then drive the two load-bearing behaviours:
 *
 *   1. RESIZE FRAME — on resize the component sends EXACTLY
 *      "__HAPPYRANCH_ASSISTANT_RESIZE__ <rows> <cols>" (rows then cols),
 *      matching the CLI control string the server parses at
 *      routes/assistant.py:_parse_resize_control.
 *   2. CLEAN TEARDOWN — on unmount the resize + data subscriptions are
 *      disposed, ws.close(1000) is called, and term.dispose() is called.
 */
import { render, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { AssistantTerminal } from './AssistantTerminal';

const RESIZE_CONTROL_PREFIX = '__HAPPYRANCH_ASSISTANT_RESIZE__';

// Shared, hoisted mock state so the module-mock factories below and the test
// bodies reach the same Terminal/FitAddon/opener instances. vi.mock is hoisted
// above imports, so anything its factories close over must come from vi.hoisted.
const h = vi.hoisted(() => ({
  // Latest constructed Terminal/FitAddon instances.
  term: null as null | Record<string, ReturnType<typeof vi.fn> | number>,
  fit: null as null | Record<string, ReturnType<typeof vi.fn>>,
  // Captured xterm event callbacks (the source wraps sendResize / ws.send).
  resizeHandler: null as null | (() => void),
  dataHandler: null as null | ((data: string) => void),
  // Subscription .dispose() spies — shared so teardown is assertable.
  resizeDispose: vi.fn(),
  dataDispose: vi.fn(),
  // Per-test socket + opener, (re)built in beforeEach.
  socket: null as null | Record<string, unknown>,
  openSession: vi.fn(),
  // ResizeObserver stub state (jsdom has no ResizeObserver).
  roCallback: null as null | ResizeObserverCallback,
  roDisconnect: vi.fn(),
}));

// jsdom does not ship ResizeObserver; stub it globally so the component can
// construct one and we can capture & fire its callback in tests.
vi.stubGlobal('ResizeObserver', vi.fn((callback: ResizeObserverCallback) => {
  h.roCallback = callback;
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: h.roDisconnect,
  };
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => {
    const term: Record<string, ReturnType<typeof vi.fn> | number> = {
      // rows !== cols on purpose: a swapped interpolation would still pass an
      // equal-value assertion, so distinct values pin the rows-then-cols order.
      rows: 24,
      cols: 80,
      loadAddon: vi.fn(),
      open: vi.fn(),
      focus: vi.fn(),
      write: vi.fn(),
      dispose: vi.fn(),
      onResize: vi.fn((cb: () => void) => {
        h.resizeHandler = cb;
        return { dispose: h.resizeDispose };
      }),
      onData: vi.fn((cb: (data: string) => void) => {
        h.dataHandler = cb;
        return { dispose: h.dataDispose };
      }),
    };
    h.term = term;
    return term;
  }),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => {
    const fit = {
      fit: vi.fn(() => {
        // A real fit() recomputes cols/rows and fires term.onResize.
        // Simulate that so the test sees the full chain:
        //   fit() → onResize → sendResize → ws.send.
        h.resizeHandler?.();
      }),
    };
    h.fit = fit;
    return fit;
  }),
}));

vi.mock('@/hooks/assistant', () => ({
  // The component calls useAssistantSessionOpener() and awaits the returned
  // opener; hand back the per-test stub so no real WebSocket is created.
  useAssistantSessionOpener: () => h.openSession,
}));

beforeEach(() => {
  vi.clearAllMocks();
  h.resizeHandler = null;
  h.dataHandler = null;
  h.roCallback = null;
  h.socket = {
    readyState: WebSocket.OPEN,
    send: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onmessage: null,
    onclose: null,
  };
  h.openSession = vi.fn().mockResolvedValue(h.socket);
});

// The opener resolves a microtask after mount; the source sets socket.onopen
// inside that .then, so awaiting it proves `ws` has been assigned (the guard
// `if (ws && ws.readyState === OPEN)` and `if (ws) ws.close(1000)` both depend
// on that assignment).
async function mountAndConnect() {
  const utils = render(<AssistantTerminal />);
  await waitFor(() => expect(h.openSession).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(typeof h.socket!.onopen).toBe('function'));
  return utils;
}

describe('AssistantTerminal', () => {
  test('sends the exact resize control frame (rows then cols) over the WS', async () => {
    await mountAndConnect();
    const socketSend = h.socket!.send as ReturnType<typeof vi.fn>;

    // Nothing sent on connect yet — we drive the resize path explicitly so the
    // assertion isolates term.onResize → sendResize.
    expect(socketSend).not.toHaveBeenCalled();

    // Fire the captured xterm resize callback (what a real fit/resize triggers).
    act(() => {
      h.resizeHandler!();
    });

    expect(socketSend).toHaveBeenCalledTimes(1);
    expect(socketSend).toHaveBeenCalledWith(`${RESIZE_CONTROL_PREFIX} 24 80`);

    // Decompose the frame to pin the literal prefix and the rows-before-cols
    // ordering against the CLI protocol the server parses.
    const frame = socketSend.mock.calls[0][0] as string;
    expect(frame.startsWith(`${RESIZE_CONTROL_PREFIX} `)).toBe(true);
    const [prefix, rows, cols] = frame.split(' ');
    expect(prefix).toBe(RESIZE_CONTROL_PREFIX);
    expect(rows).toBe('24'); // term.rows
    expect(cols).toBe('80'); // term.cols
  });

  test('cleans up subscriptions, closes the WS with 1000, and disposes the terminal on unmount', async () => {
    const { unmount } = await mountAndConnect();
    const term = h.term!;
    const socket = h.socket!;

    unmount();

    expect(h.resizeDispose).toHaveBeenCalledTimes(1);
    expect(h.dataDispose).toHaveBeenCalledTimes(1);
    expect(socket.close as ReturnType<typeof vi.fn>).toHaveBeenCalledWith(1000);
    expect(term.dispose).toHaveBeenCalledTimes(1);
    expect(h.roDisconnect).toHaveBeenCalledTimes(1);
  });

  test('ResizeObserver callback calls fitAddon.fit() and sends a resize frame', async () => {
    await mountAndConnect();

    // The ResizeObserver is constructed on mount; the callback must be captured.
    expect(h.roCallback).not.toBeNull();

    // Clear the initial fit/message calls so we isolate the ResizeObserver path.
    const fitSpy = h.fit!.fit as ReturnType<typeof vi.fn>;
    fitSpy.mockClear();
    const socketSend = h.socket!.send as ReturnType<typeof vi.fn>;
    socketSend.mockClear();

    // jsdom getBoundingClientRect returns all zeros by default, so the
    // component's width>0/height>0 guard would skip fit(). Stub it to
    // return non-zero dimensions.
    const rectStub = vi
      .spyOn(Element.prototype, 'getBoundingClientRect')
      .mockReturnValue({ width: 800, height: 400 } as DOMRect);

    // Fire the ResizeObserver callback — simulating the container getting its
    // final laid-out dimensions after a route transition.
    act(() => {
      h.roCallback!(
        [],
        {} as ResizeObserver,
      );
    });

    rectStub.mockRestore();

    // fitAddon.fit() must be called so the terminal recomputes cols/rows.
    expect(fitSpy).toHaveBeenCalledTimes(1);

    // After fit(), term.onResize fires → sendResize → ws.send with the control frame.
    expect(socketSend).toHaveBeenCalledTimes(1);
    expect(socketSend).toHaveBeenCalledWith(`${RESIZE_CONTROL_PREFIX} 24 80`);
  });
});

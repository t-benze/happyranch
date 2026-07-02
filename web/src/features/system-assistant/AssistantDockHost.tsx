/**
 * AssistantDockHost — global assistant chat dock mounted in AppShell.
 *
 * Owns open/closed state, ⌘K hotkey, the A-mode WebSocket connection, the
 * conversation log, the composer draft, focus-trap/restore, and state
 * persistence across SPA navigation.
 *
 * THR-056 approach-A: the dock renders EXACTLY like a thread conversation.
 * It rides the structured A-mode WS (`/assistant/a-mode`, PR-1) and consumes
 * normalized `TurnFrame`s — `turn_start`/`text_delta`/`tool_call`/`tool_result`/
 * `turn_end`/`status`/`error`/`history`. Each turn's `text_delta`s aggregate
 * into ONE assistant `MessageBubble` (thread idiom); a `TypingBubble` shows
 * while a turn is in flight. The user turn is rendered optimistically on send
 * (no server input-echo). On (re)connect the server first replays a `history`
 * frame carrying the persisted conversation, which hydrates the log.
 *
 * The xterm "Open full session" escape hatch (frozen PTY path) stays in the
 * dock header.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { X, Terminal } from 'lucide-react';
import { useAssistantStatus, useAssistantAModeSessionOpener } from '@/hooks/assistant';
import type { AssistantStatus } from '@/lib/api/types';
import { MessageBubble } from '@/design-system/patterns/MessageBubble';
import { TypingBubble } from '@/design-system/patterns/TypingBubble';

// ---------------------------------------------------------------------------
// hotkey
// ---------------------------------------------------------------------------

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

/**
 * Global Cmd-K / Ctrl-K listener for toggling the assistant dock.
 * Suppressed when focus is inside an editable element.
 */
export function useAssistantDockHotkey(
  onToggle: () => void,
  open: boolean,
): void {
  useEffect(() => {
    const handler = (ev: KeyboardEvent) => {
      const isCmdK =
        (ev.metaKey || ev.ctrlKey) && (ev.key === 'k' || ev.key === 'K');
      if (!isCmdK) return;
      // When the dock is open, always allow Cmd-K to close it,
      // even when focus is inside the composer (an editable element).
      if (!open && isInEditable(ev.target)) return;
      ev.preventDefault();
      onToggle();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onToggle, open]);

  // Esc to close
  useEffect(() => {
    if (!open) return;
    const handler = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        onToggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onToggle]);
}

// ---------------------------------------------------------------------------
// focus trap
// ---------------------------------------------------------------------------

const FOCUSABLE_SELECTOR =
  'input:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

function useFocusTrap(
  containerRef: React.RefObject<HTMLDivElement | null>,
  open: boolean,
  _onClose: () => void,
): void {
  // Restore focus to trigger element on close.
  const triggerRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open || !containerRef.current) return;

    // Remember what was focused before we opened.
    triggerRef.current = document.activeElement;

    const container = containerRef.current;
    // Focus first focusable element after a tick for render.
    const raf = requestAnimationFrame(() => {
      const focusable = getFocusableElements(container);
      if (focusable.length > 0) focusable[0].focus();
    });

    const handleKeyDown = (ev: KeyboardEvent) => {
      if (ev.key !== 'Tab') return;
      const focusable = getFocusableElements(container);
      if (focusable.length === 0) {
        ev.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (ev.shiftKey) {
        if (document.activeElement === first) {
          ev.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          ev.preventDefault();
          first.focus();
        }
      }
    };

    container.addEventListener('keydown', handleKeyDown);

    return () => {
      cancelAnimationFrame(raf);
      container.removeEventListener('keydown', handleKeyDown);

      // Restore focus to the trigger element.
      if (triggerRef.current instanceof HTMLElement) {
        triggerRef.current.focus();
      }
    };
  }, [open, containerRef]);
}

// ---------------------------------------------------------------------------
// A-mode conversation model
// ---------------------------------------------------------------------------

/** One tool invocation surfaced transparently within an assistant turn. */
interface ToolActivity {
  id: string;
  name: string;
  /** null while pending; true/false once a tool_result arrives. */
  ok: boolean | null;
}

/**
 * One rendered turn in the dock conversation. User turns are created
 * optimistically on send; assistant turns aggregate a turn's text_delta
 * frames into a single body.
 */
interface DockTurn {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tools: ToolActivity[];
  timestamp: string;
  /** Assistant turns: true once turn_end has arrived. Always true for user. */
  done: boolean;
}

/** Normalized frame received from the A-mode WS (mirror of the backend TurnFrame). */
interface TurnFrame {
  type: string;
  role?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown> | null;
  ok?: boolean;
  usage?: Record<string, unknown> | null;
  code?: string;
  detail?: string;
  message?: string;
  turns?: PersistedTurn[];
}

/** Shape of one persisted turn in a `history` frame (design §4). */
interface PersistedTurn {
  id?: string;
  prompt?: string;
  frames?: TurnFrame[];
  started_at?: string;
  finished_at?: string | null;
  session_id?: string | null;
}

// ---------------------------------------------------------------------------
// history hydration
// ---------------------------------------------------------------------------

/**
 * Rebuild the rendered conversation from a `history` frame's persisted turns.
 * Each persisted turn contributes an optimistic-style user bubble (its prompt)
 * followed by the assistant turn(s) reconstructed from its recorded frames.
 */
function hydrateHistory(
  turns: PersistedTurn[],
  nextId: () => string,
): DockTurn[] {
  const out: DockTurn[] = [];
  for (const t of turns) {
    if (t.prompt && t.prompt.trim()) {
      out.push({
        id: nextId(),
        role: 'user',
        text: t.prompt,
        tools: [],
        timestamp: t.started_at ?? new Date().toISOString(),
        done: true,
      });
    }
    let current: DockTurn | null = null;
    for (const frame of t.frames ?? []) {
      switch (frame.type) {
        case 'turn_start':
          current = {
            id: nextId(),
            role: 'assistant',
            text: '',
            tools: [],
            timestamp: t.started_at ?? new Date().toISOString(),
            done: false,
          };
          out.push(current);
          break;
        case 'text_delta':
          if (!current) {
            current = {
              id: nextId(),
              role: 'assistant',
              text: '',
              tools: [],
              timestamp: t.started_at ?? new Date().toISOString(),
              done: false,
            };
            out.push(current);
          }
          current.text += frame.text ?? '';
          break;
        case 'tool_call':
          if (current) {
            current.tools.push({ id: nextId(), name: frame.name ?? 'tool', ok: null });
          }
          break;
        case 'tool_result':
          if (current) {
            const pending = [...current.tools]
              .reverse()
              .find((x) => x.name === (frame.name ?? 'tool') && x.ok === null);
            if (pending) pending.ok = frame.ok ?? true;
          }
          break;
        case 'turn_end':
          if (current) current.done = true;
          current = null;
          break;
        default:
          break;
      }
    }
    if (current) current.done = true;
  }
  return out;
}

// ---------------------------------------------------------------------------
// live "now" tick — drives TypingBubble elapsed while a turn is in flight
// ---------------------------------------------------------------------------

function useNowMs(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

// ---------------------------------------------------------------------------
// A-mode WebSocket hook
// ---------------------------------------------------------------------------

interface UseAssistantAModeChat {
  turns: DockTurn[];
  sendMessage: (text: string) => void;
  connecting: boolean;
  // True only once the server's "ready" status has arrived and the socket is
  // still live. Cleared on close/error/reconnect.
  connected: boolean;
  // A turn is in flight (turn_start seen, turn_end not yet).
  inFlight: boolean;
  turnStartedAt: string | null;
  error: string | null;
}

function useAssistantAModeChat(
  open: boolean,
  status: AssistantStatus | undefined,
  statusLoading: boolean,
): UseAssistantAModeChat {
  const [turns, setTurns] = useState<DockTurn[]>([]);
  const [connecting, setConnecting] = useState(false);
  const [connected, setConnected] = useState(false);
  const [inFlight, setInFlight] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const openSession = useAssistantAModeSessionOpener();

  // Monotonic id source — avoids key collisions across turns/tools.
  const idCounterRef = useRef(0);
  const nextId = useCallback((): string => {
    idCounterRef.current += 1;
    return `m${idCounterRef.current}`;
  }, []);

  // Id of the assistant turn currently aggregating text_delta frames.
  const currentAssistantIdRef = useRef<string | null>(null);

  // Open the A-mode WS when the dock opens and the assistant is configured.
  useEffect(() => {
    if (!open) return;
    // Don't decide until the status query has actually resolved.
    if (statusLoading) return;
    if (status?.state !== 'configured') {
      setError('Assistant not configured. Set it up in Settings.');
      return;
    }

    let disposed = false;
    setConnecting(true);
    setConnected(false);
    setInFlight(false);
    setTurnStartedAt(null);
    setError(null);
    currentAssistantIdRef.current = null;

    const applyFrame = (frame: TurnFrame): void => {
      switch (frame.type) {
        case 'history': {
          const hydrated = hydrateHistory(frame.turns ?? [], nextId);
          setTurns(hydrated);
          break;
        }
        case 'status':
          if (frame.code === 'ready') {
            setConnecting(false);
            setConnected(true);
          } else if (frame.code === 'session_closed') {
            setConnected(false);
          } else if (frame.code === 'error') {
            setError(frame.detail ?? 'Assistant error.');
            setInFlight(false);
          }
          break;
        case 'turn_start': {
          const id = nextId();
          currentAssistantIdRef.current = id;
          setInFlight(true);
          setTurnStartedAt(new Date().toISOString());
          setTurns((prev) => [
            ...prev,
            {
              id,
              role: 'assistant',
              text: '',
              tools: [],
              timestamp: new Date().toISOString(),
              done: false,
            },
          ]);
          break;
        }
        case 'text_delta': {
          const id = currentAssistantIdRef.current;
          if (!id) break;
          setTurns((prev) =>
            prev.map((t) =>
              t.id === id ? { ...t, text: t.text + (frame.text ?? '') } : t,
            ),
          );
          break;
        }
        case 'tool_call': {
          const id = currentAssistantIdRef.current;
          if (!id) break;
          const toolId = nextId();
          setTurns((prev) =>
            prev.map((t) =>
              t.id === id
                ? { ...t, tools: [...t.tools, { id: toolId, name: frame.name ?? 'tool', ok: null }] }
                : t,
            ),
          );
          break;
        }
        case 'tool_result': {
          const id = currentAssistantIdRef.current;
          if (!id) break;
          setTurns((prev) =>
            prev.map((t) => {
              if (t.id !== id) return t;
              let patched = false;
              const tools = [...t.tools];
              for (let i = tools.length - 1; i >= 0; i -= 1) {
                if (!patched && tools[i].name === (frame.name ?? 'tool') && tools[i].ok === null) {
                  tools[i] = { ...tools[i], ok: frame.ok ?? true };
                  patched = true;
                }
              }
              return { ...t, tools };
            }),
          );
          break;
        }
        case 'turn_end': {
          const id = currentAssistantIdRef.current;
          if (id) {
            setTurns((prev) =>
              prev.map((t) => (t.id === id ? { ...t, done: true } : t)),
            );
          }
          currentAssistantIdRef.current = null;
          setInFlight(false);
          setTurnStartedAt(null);
          break;
        }
        case 'error':
          setError(frame.message ?? 'Unknown error');
          setConnecting(false);
          setInFlight(false);
          break;
        default:
          break;
      }
    };

    openSession()
      .then((ws) => {
        if (disposed) {
          ws.close(1000);
          return;
        }
        wsRef.current = ws;

        ws.onmessage = (event: MessageEvent) => {
          if (disposed) return;
          const raw = typeof event.data === 'string' ? event.data : '';
          if (!raw) return;
          let frame: TurnFrame;
          try {
            frame = JSON.parse(raw) as TurnFrame;
          } catch {
            // A-mode is structured from frame zero — no raw frames exist.
            // Anything unparseable is dropped rather than surfaced as chat.
            return;
          }
          if (frame && typeof frame.type === 'string') applyFrame(frame);
        };

        ws.onclose = () => {
          wsRef.current = null;
          setConnecting(false);
          setConnected(false);
          setInFlight(false);
        };

        ws.onerror = () => {
          setError('WebSocket connection failed.');
          setConnecting(false);
          setConnected(false);
          setInFlight(false);
        };
      })
      .catch((err: unknown) => {
        if (!disposed) {
          setError(`Connection failed: ${String(err)}`);
          setConnecting(false);
          setConnected(false);
        }
      });

    return () => {
      disposed = true;
      if (wsRef.current) {
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
  }, [open, openSession, status, statusLoading, nextId]);

  const sendMessage = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      // Optimistic user turn — rendered client-side; the server does NOT
      // echo the prompt back.
      setTurns((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'user',
          text: trimmed,
          tools: [],
          timestamp: new Date().toISOString(),
          done: true,
        },
      ]);

      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'start', text: trimmed }));
      } else {
        setError('Not connected. Reopen the dock to reconnect.');
      }
    },
    [nextId],
  );

  return {
    turns,
    sendMessage,
    connecting,
    connected,
    inFlight,
    turnStartedAt,
    error,
  };
}

// ---------------------------------------------------------------------------
// Host component
// ---------------------------------------------------------------------------

export function AssistantDockHost(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [composerDraft, setComposerDraft] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { slug: urlSlug } = useParams<{ slug: string }>();
  const location = useLocation();
  const contextSlug =
    (location.pathname.match(/^\/orgs\/([^/]+)/)?.[1]) ?? null;
  const activeSlug = urlSlug ?? contextSlug;

  const statusQuery = useAssistantStatus();
  const status = statusQuery.data;

  const {
    turns,
    sendMessage,
    connecting,
    connected,
    inFlight,
    turnStartedAt,
    error: wsError,
  } = useAssistantAModeChat(open, status, statusQuery.isLoading);

  const nowMs = useNowMs(open && inFlight);

  const toggle = useCallback(() => setOpen((o) => !o), []);
  useAssistantDockHotkey(toggle, open);

  // Focus-trap while open, focus-restore on close.
  useFocusTrap(containerRef, open, () => setOpen(false));

  // Focus composer when dock opens.
  useEffect(() => {
    if (open && composerRef.current) {
      // Small delay to let the dock render/transition.
      const timer = setTimeout(() => {
        composerRef.current?.focus();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [open]);

  // Keep the transcript pinned to the newest turn as it streams (thread idiom).
  useEffect(() => {
    if (typeof transcriptEndRef.current?.scrollIntoView === 'function') {
      transcriptEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [turns, inFlight]);

  // Handle [data-assistant-open] clicks anywhere in the app.
  useEffect(() => {
    const handler = (ev: Event) => {
      const target = ev.target as HTMLElement | null;
      if (target?.closest('[data-assistant-open]')) {
        setOpen(true);
      }
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, []);

  const handleSend = () => {
    const text = composerDraft.trim();
    if (!text) return;
    sendMessage(text);
    setComposerDraft('');
  };

  const handleComposerKeyDown = (
    e: React.KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const assistantConfigured = status?.state === 'configured';
  const assistantPath = activeSlug ? `/orgs/${activeSlug}/assistant` : '/assistant';

  // Assistant speaker label — data-backed executor name when available.
  const assistantSpeaker = status?.selected_executor ?? 'assistant';

  // DOCK-02 header — connection status line. Every label below is derived
  // from state the dock already tracks (status poll + live WS signals); no
  // connection is asserted that cannot be proven. `tone` selects a token
  // text-color for the leading dot + label.
  const connection: { tone: string; label: string } = statusQuery.isLoading
    ? { tone: 'text-text-muted', label: 'Checking…' }
    : !assistantConfigured
      ? { tone: 'text-text-muted', label: 'Not configured' }
      : wsError
        ? { tone: 'text-feedback-danger', label: 'Disconnected' }
        : connecting
          ? { tone: 'text-feedback-warning', label: 'Connecting…' }
          : connected
            ? { tone: 'text-feedback-success', label: 'Connected' }
            : { tone: 'text-text-muted', label: 'Idle' };

  // Executor pill — rendered only when the selected executor is data-backed
  // (AssistantStatus.selected_executor). Honestly omitted when null.
  const executor = status?.selected_executor ?? null;

  // Show the loading skeleton only while first connecting with nothing to show;
  // once history has hydrated (or a turn exists) render the conversation.
  const showLoading = connecting && turns.length === 0;

  return (
    <>
      {/* Scrim — Pasture surface-scrim token, same opacity semantics */}
      {open && (
        <div
          className="bg-surface-scrim fixed inset-0 z-40 transition-opacity"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Dock panel — Pasture surface card with warm shadow */}
      <div
        ref={containerRef}
        role="dialog"
        aria-label="Ranch Assistant"
        aria-modal={open ? 'true' : undefined}
        className={[
          'border-border-default bg-surface-raised fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l shadow-pasture-lg rounded-l-lg transition-transform duration-200',
          open ? 'translate-x-0' : 'translate-x-full pointer-events-none',
        ].join(' ')}
      >
        {/* Header — Pasture font-display heading + connection status line */}
        <div className="border-border-default flex shrink-0 items-center gap-2 border-b px-4 py-3">
          <div className="min-w-0 flex-1">
            <span className="text-text-primary font-display block text-base">
              Ranch Assistant
            </span>
            <div className="mt-0.5 flex items-center gap-2">
              <span
                className={`inline-flex items-center gap-1 text-xs ${connection.tone}`}
              >
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full bg-current"
                  aria-hidden="true"
                />
                {connection.label}
              </span>
              <span className="text-text-muted text-xs">· operates your runtime</span>
              {executor && (
                <span className="bg-surface-sunken text-text-secondary inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium">
                  {executor}
                </span>
              )}
            </div>
          </div>

          {/* "Open full session" escape hatch — retained xterm page */}
          <a
            href={assistantPath}
            onClick={(e) => {
              e.preventDefault();
              setOpen(false);
              navigate(assistantPath);
            }}
            className="text-text-secondary hover:text-text-primary hover:bg-surface-hover inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors"
            title="Open full terminal session"
          >
            <Terminal size={14} aria-hidden="true" />
            <span>Full session</span>
          </a>

          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close assistant"
            className="text-text-secondary hover:text-text-primary hover:bg-surface-hover inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Messages area — thread-style transcript */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {statusQuery.isLoading ? (
            <EmptyState text="Loading…" calm />
          ) : !assistantConfigured ? (
            <EmptyState
              text={
                status
                  ? 'Assistant is not ready. Set it up from Settings → Assistant.'
                  : 'Could not load assistant status.'
              }
              error={!status}
              calm
            />
          ) : showLoading ? (
            <LoadingState />
          ) : turns.length === 0 && !inFlight ? (
            <EmptyState
              text="Ask the assistant anything — or type / to run a command."
              calm
            />
          ) : (
            <div className="flex flex-col gap-2">
              {turns.map((turn, i) => (
                <DockTurnView
                  key={turn.id}
                  turn={turn}
                  seq={i + 1}
                  assistantSpeaker={assistantSpeaker}
                />
              ))}
              {inFlight && (
                <TypingBubble
                  agentName={assistantSpeaker}
                  status="working"
                  startedAt={turnStartedAt}
                  nowMs={nowMs}
                />
              )}
              <div ref={transcriptEndRef} />
            </div>
          )}

          {wsError && (
            <div
              role="alert"
              className="border-border-default bg-surface-sunken text-feedback-danger mt-3 rounded-lg border p-2 text-xs"
            >
              {wsError}
            </div>
          )}
        </div>

        {/* Composer — Pasture surface tokens */}
        {assistantConfigured && (
          <div className="border-border-default shrink-0 border-t p-3">
            <div className="flex items-end gap-2">
              <textarea
                ref={composerRef}
                value={composerDraft}
                onChange={(e) => setComposerDraft(e.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="Ask the assistant, or type / to run a command…"
                rows={1}
                className="border-border-default bg-surface-sunken text-text-primary placeholder:text-text-muted focus:border-accent-ring min-h-9 flex-1 resize-none rounded-lg border px-3 py-2 text-sm focus:outline-none"
                aria-label="Assistant composer"
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={!composerDraft.trim() || connecting}
                className="bg-accent-default text-text-inverse hover:bg-accent-hover inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
            <p className="text-text-muted mt-1 text-xs">
              <kbd className="font-mono">Enter</kbd> to send ·{' '}
              <kbd className="font-mono">Shift+Enter</kbd> for new line
            </p>
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/**
 * One conversation turn, rendered with the thread components: the user turn as
 * a `founder`-variant MessageBubble, the assistant turn as a `worker`-variant
 * MessageBubble (design §5). Tool activity is surfaced minimally above the
 * assistant body.
 */
function DockTurnView({
  turn,
  seq,
  assistantSpeaker,
}: {
  turn: DockTurn;
  seq: number;
  assistantSpeaker: string;
}): JSX.Element {
  if (turn.role === 'user') {
    return (
      <MessageBubble
        variant="founder"
        seq={seq}
        speaker="you"
        speakerRole="founder"
        timestamp={turn.timestamp}
        body={turn.text}
      />
    );
  }

  return (
    <div className="flex flex-col gap-1">
      {turn.tools.length > 0 && (
        <ul className="flex flex-col gap-0.5" aria-label="Tool activity">
          {turn.tools.map((tool) => (
            <li
              key={tool.id}
              className="text-text-muted flex items-center gap-1.5 font-mono text-xs"
            >
              <span aria-hidden="true">
                {tool.ok === null ? '⋯' : tool.ok ? '✓' : '✗'}
              </span>
              <span className="truncate">{tool.name}</span>
            </li>
          ))}
        </ul>
      )}
      {turn.text.trim() !== '' && (
        <MessageBubble
          variant="worker"
          seq={seq}
          speaker={assistantSpeaker}
          speakerRole="worker"
          timestamp={turn.timestamp}
          body={turn.text}
        />
      )}
    </div>
  );
}

function EmptyState({
  text,
  calm = false,
  error = false,
}: {
  text: string;
  calm?: boolean;
  error?: boolean;
}): JSX.Element {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-text-secondary max-w-xs text-center text-sm">
        {calm && (
          <div className="mb-2 text-3xl" aria-hidden="true">
            🌾
          </div>
        )}
        <p className={error ? 'text-feedback-danger' : ''}>{text}</p>
      </div>
    </div>
  );
}

function LoadingState(): JSX.Element {
  const widths = [
    ['w-3/5', 'w-2/5'],
    ['w-2/3', 'w-1/2'],
    ['w-3/4', 'w-2/3'],
  ];
  return (
    <div className="flex flex-col gap-3" aria-label="Loading">
      {widths.map(([w1, w2], i) => (
        <div
          key={i}
          className="bg-surface-sunken animate-pulse rounded-lg p-4"
        >
          <div className={`bg-surface-raised h-3 rounded-md ${w1}`} />
          <div className={`bg-surface-raised mt-2 h-3 rounded-md ${w2}`} />
        </div>
      ))}
    </div>
  );
}

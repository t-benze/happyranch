/**
 * AssistantDockHost — global assistant chat dock mounted in AppShell.
 *
 * Owns open/closed state, ⌘K hotkey, WebSocket connection (structured JSON
 * protocol over the existing /api/v1/assistant/session), message history,
 * composer draft, focus-trap/restore, and state persistence across SPA
 * navigation.
 *
 * Per PRD §2.5.2 + §4.10 (design-overhaul v1). Structured frames ride the
 * EXISTING assistant WS — bearer-subprotocol auth + PTY attach contract
 * are frozen and unchanged. The xterm "Open full session" escape hatch is
 * in the dock header.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { X, Terminal } from 'lucide-react';
import { useAssistantStatus, useAssistantSessionOpener } from '@/hooks/assistant';
import type { AssistantStatus } from '@/lib/api/types';
import { AssistantTurn, type AssistantMessage } from './AssistantTurn';

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
// WebSocket structured session hook
// ---------------------------------------------------------------------------

type WsFrame = { type: string; text?: string; code?: string; message?: string };

interface UseAssistantChatWs {
  messages: AssistantMessage[];
  sendMessage: (text: string) => void;
  connecting: boolean;
  error: string | null;
}

function useAssistantChatWs(
  open: boolean,
  status: AssistantStatus | undefined,
  statusLoading: boolean,
): UseAssistantChatWs {
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const openSession = useAssistantSessionOpener();

  // Open WS when dock opens and assistant is configured.
  useEffect(() => {
    if (!open) return;
    // Don't decide until the status query has actually resolved.
    // On cold load Cmd-K before the query completes, status is still
    // undefined — wait rather than emitting a permanent error.
    if (statusLoading) return;
    if (status?.state !== 'configured') {
      setError('Assistant not configured. Set it up in Settings.');
      return;
    }

    let disposed = false;
    setConnecting(true);
    setError(null);

    openSession()
      .then((ws) => {
        if (disposed) {
          ws.close(1000);
          return;
        }
        wsRef.current = ws;

        ws.onopen = () => {
          // Negotiate structured JSON-chat mode.
          ws.send(
            JSON.stringify({ type: 'handshake', protocol: 'json-chat', version: 1 }),
          );
        };

        ws.onmessage = (event: MessageEvent) => {
          if (disposed) return;
          try {
            const frame: WsFrame = JSON.parse(
              typeof event.data === 'string' ? event.data : '',
            );
            if (frame.type === 'status') {
              if (frame.code === 'ready') {
                setConnecting(false);
              }
            } else if (frame.type === 'output') {
              const text = frame.text ?? '';
              if (text.trim()) {
                setMessages((prev) => [
                  ...prev,
                  {
                    role: 'assistant',
                    text,
                    timestamp: new Date().toISOString(),
                  },
                ]);
              }
            } else if (frame.type === 'error') {
              setError(frame.message ?? 'Unknown error');
              setConnecting(false);
            }
          } catch {
            // Non-JSON frame (legacy raw text) — treat as assistant output.
            const text =
              typeof event.data === 'string' ? event.data : String(event.data);
            if (text.trim()) {
              setMessages((prev) => [
                ...prev,
                {
                  role: 'assistant',
                  text,
                  timestamp: new Date().toISOString(),
                },
              ]);
            }
          }
        };

        ws.onclose = () => {
          wsRef.current = null;
          setConnecting(false);
        };

        ws.onerror = () => {
          setError('WebSocket connection failed.');
          setConnecting(false);
        };
      })
      .catch((err: unknown) => {
        if (!disposed) {
          setError(`Connection failed: ${String(err)}`);
          setConnecting(false);
        }
      });

    return () => {
      disposed = true;
      if (wsRef.current) {
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
  }, [open, openSession, status, statusLoading]);

  const sendMessage = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      const trimmed = text.trim();

      // Add user message to history.
      setMessages((prev) => [
        ...prev,
        { role: 'user', text: trimmed, timestamp: new Date().toISOString() },
      ]);

      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'chat', text: trimmed }));
      } else {
        setError('Not connected. Reopen the dock to reconnect.');
      }
    },
    [],
  );

  return { messages, sendMessage, connecting, error };
}

// ---------------------------------------------------------------------------
// Host component
// ---------------------------------------------------------------------------

export function AssistantDockHost(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [composerDraft, setComposerDraft] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const { slug: urlSlug } = useParams<{ slug: string }>();
  const location = useLocation();
  const contextSlug =
    (location.pathname.match(/^\/orgs\/([^/]+)/)?.[1]) ?? null;
  const activeSlug = urlSlug ?? contextSlug;

  const statusQuery = useAssistantStatus();
  const status = statusQuery.data;

  const { messages, sendMessage, connecting, error: wsError } =
    useAssistantChatWs(open, status, statusQuery.isLoading);

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

  return (
    <>
      {/* Scrim */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/30 transition-opacity"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Dock panel */}
      <div
        ref={containerRef}
        role="dialog"
        aria-label="System Assistant"
        aria-modal={open ? 'true' : undefined}
        className={[
          'border-border bg-bg fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l shadow-2xl transition-transform duration-200',
          open ? 'translate-x-0' : 'translate-x-full pointer-events-none',
        ].join(' ')}
      >
        {/* Header */}
        <div className="border-border flex shrink-0 items-center gap-2 border-b px-4 py-3">
          <span className="text-fg flex-1 text-sm font-semibold">
            System Assistant
          </span>

          {/* "Open full session" escape hatch — retained xterm page */}
          <a
            href={assistantPath}
            onClick={(e) => {
              e.preventDefault();
              setOpen(false);
              navigate(assistantPath);
            }}
            className="text-fg-muted hover:text-fg hover:bg-bg-raised inline-flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors"
            title="Open full terminal session"
          >
            <Terminal size={14} aria-hidden="true" />
            <span>Full session</span>
          </a>

          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close assistant"
            className="text-fg-muted hover:text-fg hover:bg-bg-raised inline-flex h-7 w-7 items-center justify-center rounded transition-colors"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {statusQuery.isLoading ? (
            <EmptyState text="Loading…" />
          ) : !assistantConfigured ? (
            <EmptyState
              text={
                status
                  ? 'Assistant is not ready. Set it up from Settings → Assistant.'
                  : 'Could not load assistant status.'
              }
              error={!status}
            />
          ) : connecting ? (
            <LoadingState />
          ) : messages.length === 0 ? (
            <EmptyState
              text="Ask the assistant anything — or type / to run a command."
              calm
            />
          ) : (
            <div className="flex flex-col gap-3">
              {messages.map((msg, i) => (
                <AssistantTurn
                  key={i}
                  message={msg}
                  orgSlug={activeSlug ?? undefined}
                />
              ))}
            </div>
          )}

          {wsError && (
            <div
              role="alert"
              className="border-border bg-bg-subtle text-feedback-danger mt-3 rounded border p-2 text-xs"
            >
              {wsError}
            </div>
          )}
        </div>

        {/* Composer */}
        {assistantConfigured && (
          <div className="border-border shrink-0 border-t p-3">
            <div className="flex items-end gap-2">
              <textarea
                ref={composerRef}
                value={composerDraft}
                onChange={(e) => setComposerDraft(e.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="Ask or search…"
                rows={1}
                className="border-border bg-bg-subtle text-fg placeholder:text-fg-subtle focus:border-accent-ring min-h-9 flex-1 resize-none rounded border px-3 py-2 text-sm focus:outline-none"
                aria-label="Assistant composer"
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={!composerDraft.trim() || connecting}
                className="bg-accent text-fg-on-accent hover:bg-accent-hover inline-flex h-9 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
            <p className="text-fg-subtle mt-1 text-xs">
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
      <div className="text-fg-muted max-w-xs text-center text-sm">
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
          className="bg-bg-subtle animate-pulse rounded-lg p-4"
        >
          <div className={`bg-bg-raised h-3 rounded ${w1}`} />
          <div className={`bg-bg-raised mt-2 h-3 rounded ${w2}`} />
        </div>
      ))}
    </div>
  );
}

/**
 * In-browser terminal attached to the System Assistant PTY over the existing
 * WebSocket at /api/v1/assistant/session.
 *
 * Protocol mirrors the CLI reference client (cli/commands/assistant.py):
 *   - stdin:  term.onData(d => ws.send(d))
 *   - stdout: ws.onmessage = e => term.write(e.data)
 *   - resize: send the EXACT control string
 *             "__HAPPYRANCH_ASSISTANT_RESIZE__ <rows> <cols>" on open and on
 *             every term.onResize (the server parses it at
 *             routes/assistant.py:_parse_resize_control).
 *
 * Auth is the browser bearer-subprotocol (THR-006 Option A); the opener is
 * provided through the provider-aware hook so the sandbox can stub it. Only
 * mount this when status.state === 'configured'.
 */
import { useEffect, useRef } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useAssistantSessionOpener } from '@/hooks/assistant';

const RESIZE_CONTROL_PREFIX = '__HAPPYRANCH_ASSISTANT_RESIZE__';

export function AssistantTerminal(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const openSession = useAssistantSessionOpener();

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;
    let ws: WebSocket | null = null;

    const term = new Terminal({
      convertEol: true,
      fontSize: 13,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      cursorBlink: true,
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();

    const sendResize = (): void => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(`${RESIZE_CONTROL_PREFIX} ${term.rows} ${term.cols}`);
      }
    };

    // Every fit that changes the dimensions fires term.onResize.
    const resizeSub = term.onResize(() => sendResize());
    const dataSub = term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
    });
    const onWindowResize = (): void => fitAddon.fit();
    window.addEventListener('resize', onWindowResize);

    openSession()
      .then((socket) => {
        if (disposed) {
          socket.close(1000);
          return;
        }
        ws = socket;
        socket.onopen = (): void => {
          fitAddon.fit();
          sendResize();
          term.focus();
        };
        socket.onmessage = (event: MessageEvent): void => {
          if (typeof event.data === 'string') term.write(event.data);
        };
        socket.onclose = (): void => {
          if (!disposed) term.write('\r\n[assistant session closed]\r\n');
        };
      })
      .catch((err: unknown) => {
        if (!disposed) term.write(`\r\n[assistant] connection failed: ${String(err)}\r\n`);
      });

    return () => {
      disposed = true;
      window.removeEventListener('resize', onWindowResize);
      resizeSub.dispose();
      dataSub.dispose();
      if (ws) ws.close(1000);
      term.dispose();
    };
  }, [openSession]);

  return (
    <div
      ref={containerRef}
      data-testid="assistant-terminal"
      className="border-border bg-bg-sunken h-96 w-full overflow-hidden rounded-md border p-2"
    />
  );
}

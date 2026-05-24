import { useEffect, useRef } from 'react';
import { useScriptOutput } from '@/hooks/scripts';
import { scriptEventsPath } from '@/lib/api/scripts';
import { useScriptEventStream } from './scriptEventsHook';
import type { ScriptRequest } from '@/lib/api/types';

interface Props {
  sr: ScriptRequest;
  slug: string;
}

export function OutputPanel({ sr, slug }: Props): JSX.Element | null {
  const isLive = sr.status === 'running';
  const { events, terminal } = useScriptEventStream(
    isLive ? scriptEventsPath(slug, sr.id) : null,
    isLive,
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({ top: containerRef.current.scrollHeight });
    }
  }, [events.length]);

  const outputQuery = useScriptOutput(
    !isLive && (sr.status === 'completed' || sr.status === 'failed') ? sr.id : undefined,
  );

  if (sr.status === 'pending' || sr.status === 'rejected') return null;

  return (
    <section>
      <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Output
      </h3>

      {isLive && (
        <div
          ref={containerRef}
          className="bg-surface-canvas h-64 overflow-y-auto rounded p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {events.length === 0 && !terminal && (
            <span className="text-fg-muted">Waiting for output…</span>
          )}
          {events.map((e, i) => (
            <div key={i} className={e.kind === 'stderr' ? 'text-fg-danger' : ''}>
              {e.line}
            </div>
          ))}
          {terminal && (
            <div className="text-fg-muted mt-2">
              [done] {terminal.status} exit={terminal.exit_code ?? 'n/a'}
            </div>
          )}
        </div>
      )}

      {!isLive && outputQuery.isLoading && (
        <p className="text-fg-muted text-sm">Loading output…</p>
      )}

      {!isLive && outputQuery.data && (
        <div className="space-y-3">
          <div>
            <h4 className="text-fg-muted mb-1 text-xs uppercase">stdout</h4>
            <pre className="bg-surface-canvas overflow-x-auto rounded p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stdout || '(empty)'}
            </pre>
          </div>
          <div>
            <h4 className="text-fg-muted mb-1 text-xs uppercase">stderr</h4>
            <pre className="bg-surface-canvas overflow-x-auto rounded p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stderr || '(empty)'}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

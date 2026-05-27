import { useEffect, useRef } from 'react';
import { useJobOutput } from '@/hooks/jobs';
import { jobEventsPath } from '@/lib/api/jobs';
import { useJobEventStream } from './jobEventsHook';
import type { JobRecord } from '@/lib/api/types';

interface Props {
  job: JobRecord;
  slug: string;
}

export function OutputPanel({ job, slug }: Props): JSX.Element | null {
  const isLive = job.status === 'running';
  const { events, terminal } = useJobEventStream(
    isLive ? jobEventsPath(slug, job.id) : null,
    isLive,
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // JSDOM omits Element.prototype.scrollTo. Guard so the test path that
    // exercises the running drawer (Stop button) doesn't throw.
    const el = containerRef.current;
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [events.length]);

  const outputQuery = useJobOutput(
    !isLive && (job.status === 'completed' || job.status === 'failed') ? job.id : undefined,
  );

  if (job.status === 'pending' || job.status === 'rejected') return null;

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

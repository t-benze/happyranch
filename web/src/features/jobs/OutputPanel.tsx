import { useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useJobOutput } from '@/hooks/jobs';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes the job tail/SSE helpers; routed direct per THR-011 founder ruling (option 3), pending a future hook
import { jobEventsPath, tailJob } from '@/lib/api/jobs';
import { useJobEventStream } from './jobEventsHook';
import type { JobRecord } from '@/lib/api/types';

interface Props {
  job: JobRecord;
  slug: string;
}

const TAIL_LINES = 200;

export function OutputPanel({ job, slug }: Props): JSX.Element | null {
  const isLive = job.status === 'running';
  const qc = useQueryClient();

  // Seed: when the drawer opens on an already-running job, the /events stream
  // only delivers lines produced AFTER subscription. Without a one-time /tail
  // pull, earlier stdout/stderr is invisible — exactly the output a debugger
  // would want. Fetch once on mount of the live drawer, then merge with the
  // live events below.
  const seedQuery = useQuery({
    queryKey: ['job-tail-seed', slug, job.id],
    queryFn: () =>
      Promise.all([
        tailJob(slug, job.id, { stream: 'stdout', lines: TAIL_LINES }),
        tailJob(slug, job.id, { stream: 'stderr', lines: TAIL_LINES }),
      ]).then(([out, err]) => ({ stdout: out.lines, stderr: err.lines })),
    enabled: isLive,
    staleTime: Infinity, // one-shot seed; never refetch automatically
  });

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
  }, [events.length, seedQuery.data]);

  // When the SSE stream reports terminal, the row in the DB has flipped to
  // terminal too. Invalidate the cached job record so the drawer's header,
  // action bar, and (after this remount) the terminal-output fetch reflect
  // the new state without the user reloading.
  useEffect(() => {
    if (terminal) {
      qc.invalidateQueries({ queryKey: ['job', slug, job.id] });
      qc.invalidateQueries({ queryKey: ['jobs', slug] });
    }
  }, [terminal, qc, slug, job.id]);

  const outputQuery = useJobOutput(
    !isLive && (job.status === 'completed' || job.status === 'failed') ? job.id : undefined,
  );

  // Merge seed lines (oldest first, stdout then stderr — best-effort ordering
  // since we don't have interleaved timestamps from /tail) with the live SSE
  // events.
  const seedLines = useMemo(() => {
    if (!seedQuery.data) return [] as { kind: 'stdout' | 'stderr'; line: string }[];
    return [
      ...seedQuery.data.stdout.map((line) => ({ kind: 'stdout' as const, line })),
      ...seedQuery.data.stderr.map((line) => ({ kind: 'stderr' as const, line })),
    ];
  }, [seedQuery.data]);

  if (job.status === 'pending' || job.status === 'rejected') return null;

  return (
    <section>
      <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Output
      </h3>

      {isLive && (
        <div
          ref={containerRef}
          className="bg-surface-sunken border-border-default h-64 overflow-y-auto rounded-lg border p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {seedLines.length === 0 && events.length === 0 && !terminal && (
            <span className="text-text-muted">Waiting for output…</span>
          )}
          {seedLines.map((e, i) => (
            <div key={`seed-${i}`} className={e.kind === 'stderr' ? 'text-feedback-danger' : ''}>
              {e.line}
            </div>
          ))}
          {events.map((e, i) => (
            <div key={`live-${i}`} className={e.kind === 'stderr' ? 'text-feedback-danger' : ''}>
              {e.line}
            </div>
          ))}
          {terminal && (
            <div className="text-text-muted mt-2">
              [done] {terminal.status} exit={terminal.exit_code ?? 'n/a'}
            </div>
          )}
        </div>
      )}

      {!isLive && outputQuery.isLoading && (
        <p className="text-text-muted text-sm">Loading output…</p>
      )}

      {!isLive && outputQuery.data && (
        <div className="space-y-3">
          <div>
            <h4 className="text-text-muted mb-1 text-xs uppercase">stdout</h4>
            <pre className="bg-surface-sunken border-border-default overflow-x-auto rounded-lg border p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stdout || '(empty)'}
            </pre>
          </div>
          <div>
            <h4 className="text-text-muted mb-1 text-xs uppercase">stderr</h4>
            <pre className="bg-surface-sunken border-border-default overflow-x-auto rounded-lg border p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stderr || '(empty)'}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

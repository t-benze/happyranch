import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useJobOutput } from '@/hooks/jobs';
import { useTranslation } from '@/hooks/i18n';
import { Button } from '@/design-system/primitives/Button';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes the job tail/SSE helpers; routed direct per THR-011 founder ruling (option 3), pending a future hook
import { getJob, jobEventsPath, tailJob } from '@/lib/api/jobs';
import { useJobEventStream } from './jobEventsHook';
import type { JobRecord } from '@/lib/api/types';

interface Props {
  job: JobRecord;
  slug: string;
}

const TAIL_LINES = 200;

export function OutputPanel({ job, slug }: Props): JSX.Element | null {
  const { t } = useTranslation();
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

  const { events, terminal, unavailable } = useJobEventStream(
    jobEventsPath(slug, job.id),
    isLive,
  );

  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  useEffect(() => { setRefreshFailed(false); }, [slug, job.id]);
  const refresh = async () => {
    setRefreshing(true);
    setRefreshFailed(false);
    try {
      await qc.fetchQuery({ queryKey: ['job', slug, job.id],
        queryFn: () => getJob(slug, job.id), staleTime: 0, retry: false });
      void qc.invalidateQueries({ queryKey: ['jobs', slug] });
    } catch {
      setRefreshFailed(true);
    } finally {
      setRefreshing(false);
    }
  };
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // JSDOM omits Element.prototype.scrollTo. Guard so the test path that
    // exercises the running drawer (Stop button) doesn't throw.
    const el = containerRef.current;
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [events.length, seedQuery.data]);

  // A terminal frame is a hint. Only a successful detail read owns status/actions.
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
        {t('jobs.output.title')}
      </h3>

      {unavailable && isLive && (
        <div className="mb-2 space-y-2 text-sm">
          <p>{t('jobs.output.unavailable')}</p>
          <Button variant="secondary" size="sm" disabled={refreshing} onClick={refresh}>
            {t('jobs.output.refresh')}
          </Button>
        </div>
      )}
      {refreshFailed && <p role="alert" className="text-feedback-danger mb-2 text-sm">{t('jobs.output.refreshFailed')}</p>}
      {!isLive && outputQuery.isError && <p role="alert" className="text-feedback-danger mb-2 text-sm">{t('jobs.output.failed')}</p>}
      {(isLive || !outputQuery.data) && (
        <div
          ref={containerRef}
          className="bg-surface-sunken border-border-default h-64 overflow-y-auto rounded-lg border p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {seedLines.length === 0 && events.length === 0 && !terminal && (
            <span className="text-text-muted">{t('jobs.output.waiting')}</span>
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
          {!isLive && (seedLines.length > 0 || events.length > 0) && (
            <p className="text-text-muted mt-2">{t('jobs.output.lastReceived')}</p>
          )}
        </div>
      )}

      {!isLive && outputQuery.isLoading && (
        <p className="text-text-muted text-sm">{t('jobs.output.loading')}</p>
      )}

      {!isLive && outputQuery.data && (
        <div className="space-y-3">
          <div>
            <h4 className="text-text-muted mb-1 text-xs uppercase">stdout</h4>
            <pre className="bg-surface-sunken border-border-default overflow-x-auto rounded-lg border p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stdout || t('jobs.output.empty')}
            </pre>
          </div>
          <div>
            <h4 className="text-text-muted mb-1 text-xs uppercase">stderr</h4>
            <pre className="bg-surface-sunken border-border-default overflow-x-auto rounded-lg border p-3 text-xs whitespace-pre-wrap">
              {outputQuery.data.stderr || t('jobs.output.empty')}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

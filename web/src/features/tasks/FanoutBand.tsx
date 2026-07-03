/**
 * FanoutBand — compact fan-out status band shown near the Task detail header
 * for the two fan-out lifecycle states (TASK-1717 polish, design target
 * TASK-1696). Presentation-only; every value is DERIVED and honesty-degraded
 * per the Step 0 reconciliation (no fabricated locales, tokens, executor
 * values, artifact links, or merge summaries).
 *
 *  - running : N subtasks spawned; compact progress counts from recall.
 *  - joined  : parent resumed after all children terminal; terminal counts.
 *
 * (pending mode removed per THR-012 msg 129/131 — no fan-out review gate.)
 *
 * Regular (non-fan-out) tasks never render this band — the caller only mounts
 * it when real fan-out evidence exists.
 */

import type { ChildStatusCounts } from './fanout';
import { progressSummary } from './fanout';

export type FanoutMode = 'running' | 'joined';

interface FanoutBandProps {
  mode: FanoutMode;
  /** Planned/known fan-out width. Null when not recorded on the payload. */
  width: number | null;
  /** Child status counts (running/joined). Null for pending (no children). */
  counts: ChildStatusCounts | null;
}

/** Fan-out glyph — a small branch icon echoing the design without new assets. */
function BranchIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M6 8.2v3.3a3 3 0 0 0 3 3h6.8" />
      <path d="M6 8.2V6" opacity="0" />
      <path d="M15.8 6H8.2" />
    </svg>
  );
}

/** Segmented progress bar for the running state. Widths are proportional to
 *  the counts; each segment is a design-token status colour. */
function ProgressBar({ counts }: { counts: ChildStatusCounts }): JSX.Element {
  const total = Math.max(counts.total, 1);
  const seg = (n: number, cls: string, key: string) =>
    n > 0 ? (
      <span
        key={key}
        className={cls}
        // eslint-disable-next-line react/forbid-dom-props -- proportional segment width is a runtime ratio of live child counts; no static utility class can express a data-driven percentage
        style={{ width: `${(n / total) * 100}%` }}
        aria-hidden
      />
    ) : null;
  return (
    <div className="bg-border-subtle mt-2 flex h-1.5 w-full overflow-hidden rounded-full">
      {seg(counts.completed, 'bg-status-open', 'done')}
      {seg(counts.running, 'bg-accent-default', 'run')}
      {seg(counts.failed, 'bg-status-abandoned', 'fail')}
      {seg(counts.queued, 'bg-border-strong', 'queue')}
    </div>
  );
}

function headline(
  mode: FanoutMode,
  width: number | null,
  counts: ChildStatusCounts | null,
): string {
  const n = width ?? counts?.total ?? 0;
  switch (mode) {
    case 'running':
      return `Running fan-out — ${counts?.terminal ?? 0} of ${n} done`;
    case 'joined':
      return `Fan-out joined — ${counts?.completed ?? 0} of ${n} succeeded`;
  }
}

function toneClasses(mode: FanoutMode): { title: string; icon: string } {
  switch (mode) {
    case 'running':
      return { title: 'text-accent-default', icon: 'text-accent-default' };
    case 'joined':
      return { title: 'text-status-open', icon: 'text-status-open' };
  }
}

export function FanoutBand({
  mode,
  width,
  counts,
}: FanoutBandProps): JSX.Element {
  const tone = toneClasses(mode);
  const n = width ?? counts?.total ?? 0;

  return (
    <section
      aria-label="Fan-out status"
      className="border-border-default bg-surface-raised mt-4 rounded-xl border p-4"
    >
      <div className="flex items-start gap-3">
        <span className={`mt-0.5 shrink-0 ${tone.icon}`}>
          <BranchIcon />
        </span>
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-semibold ${tone.title}`}>
            {headline(mode, width, counts)}
          </p>

          {mode === 'running' && (
            <>
              <p className="text-text-muted mt-1 text-xs">
                Parent resumes automatically once every subtask reaches a
                terminal state.
              </p>
              {counts && (
                <>
                  <ProgressBar counts={counts} />
                  <p className="text-text-secondary mt-1.5 font-mono text-xs tabular-nums">
                    {progressSummary(counts)}
                  </p>
                </>
              )}
            </>
          )}

          {mode === 'joined' && counts && (
            <p className="text-text-muted mt-1 text-xs">
              Parent resumed and completed once all subtasks reached a terminal
              state.
              {counts.failed > 0 && (
                <>
                  {' '}
                  {counts.failed} subtask{counts.failed === 1 ? '' : 's'} did not
                  succeed.
                </>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Running/joined: backed metadata only — planned width and the constant
          Phase-1 join mode (all children terminal before join). No fabricated
          executor/token/artifact fields. */}
      {(mode === 'running' || mode === 'joined') && n > 0 && (
        <div className="border-border-subtle text-text-muted mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t pt-2 text-xs">
          <span>
            Width <span className="text-text-secondary tabular-nums">{n}</span>
          </span>
          <span>
            Join <span className="text-text-secondary font-mono">all-terminal</span>
          </span>
        </div>
      )}

    </section>
  );
}

/**
 * Workload — one row per agent the daemon reports for the current window
 * (PRD §4). Lifecycle counts only: no token value, CLI/model selector or
 * token-derived rank ever appears here.
 */
import type { ReactNode } from 'react';
import type { WorkloadAgent, WorkloadPeriod, WorkloadResponse } from './useUsageData';
import { useWorkload } from './useUsageData';
import {
  CELL_CLASS,
  DeltaLine,
  FootnoteMark,
  Footnotes,
  IDENTITY_CLASS,
  ScrollTable,
  SectionError,
  StaleNotice,
  TH_CLASS,
  TableSkeleton,
  UsageSection,
} from './UsageParts';
import { formatCount, formatDuration, formatInstant } from './usageFormat';

const COLUMNS = ['Agent', 'Task runs', 'Thread wakes', 'Recorded runtime', 'Deliveries', 'Replies'];

const DEFINITIONS: Array<[string, string]> = [
  ['Task runs', 'Task execution sessions that started in the period, including retries, recovery turns and manager decisions.'],
  ['Thread wakes', 'Thread invocations that started in the period, whatever their outcome.'],
  ['Recorded runtime', 'Agent runtime as recorded for those runs. Missing durations are not counted as zero; this is not human working time.'],
  ['Deliveries', 'Distinct tasks that reached an accepted, completed worker delivery in the period.'],
  ['Replies', 'Thread reply wakes that produced one persisted agent message.'],
];

function runtimeValue(p: WorkloadPeriod): string {
  return p.recorded_runtime.known === 0 ? 'Not recorded' : formatDuration(p.recorded_runtime.seconds);
}

function runtimeCoverage(p: WorkloadPeriod): string {
  return `recorded for ${p.recorded_runtime.known} of ${p.recorded_runtime.total} runs`;
}

function replyCoverage(p: WorkloadPeriod): string {
  return `${p.reply_outcome_coverage.recorded} of ${p.reply_outcome_coverage.total_consumed}`;
}

function CountCell({
  value,
  extra,
  delta,
}: {
  value: string;
  extra?: ReactNode;
  delta?: ReactNode;
}): JSX.Element {
  return (
    <td className={CELL_CLASS}>
      <span className="text-text-primary">{value}</span>
      {extra}
      {delta}
    </td>
  );
}

function SubLine({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-2xs text-text-muted mt-1 block font-sans">{children}</span>;
}

function AgentRow({ row, compare }: { row: WorkloadAgent; compare: boolean }): JSX.Element {
  const cur = row.current;
  const prev = row.previous;
  const showDelta = compare && prev !== null;
  const unknownReplies = cur.reply_outcome_coverage.total_consumed - cur.reply_outcome_coverage.recorded;

  const count = (key: 'task_runs' | 'thread_wakes' | 'deliveries'): JSX.Element => (
    <CountCell
      value={formatCount(cur[key])}
      delta={
        showDelta && (
          <DeltaLine
            delta={row.deltas?.[key]}
            formatAbsolute={formatCount}
            previous={formatCount(prev[key])}
            coverage=""
          />
        )
      }
    />
  );

  return (
    <tr className="border-border-default border-b last:border-0">
      <th scope="row" className={IDENTITY_CLASS}>
        <span className="text-text-primary font-medium break-words">{row.agent}</span>
      </th>
      {count('task_runs')}
      {count('thread_wakes')}
      <CountCell
        value={runtimeValue(cur)}
        extra={
          <>
            <SubLine>{runtimeCoverage(cur)}</SubLine>
            {showDelta && <SubLine>before: {runtimeCoverage(prev)}</SubLine>}
          </>
        }
        delta={
          showDelta && (
            <DeltaLine
              delta={row.deltas?.recorded_runtime_seconds}
              formatAbsolute={formatDuration}
              previous={runtimeValue(prev)}
              coverage={`${runtimeCoverage(cur)} now · ${runtimeCoverage(prev)} before`}
            />
          )
        }
      />
      {count('deliveries')}
      <CountCell
        value={formatCount(cur.replies)}
        extra={
          <>
            <SubLine>recorded {replyCoverage(cur)}</SubLine>
            {unknownReplies > 0 && <SubLine>{unknownReplies} reply outcome not recorded</SubLine>}
          </>
        }
        delta={
          showDelta && (
            <DeltaLine
              delta={row.deltas?.replies}
              formatAbsolute={formatCount}
              previous={formatCount(prev.replies)}
              coverage={`recorded ${replyCoverage(cur)} now · ${replyCoverage(prev)} before`}
            />
          )
        }
      />
    </tr>
  );
}

function WorkloadTable({ data, compare }: { data: WorkloadResponse; compare: boolean }): JSX.Element {
  return (
    <ScrollTable label="Workload table">
      <table className="text-body min-w-content-form w-full table-fixed border-collapse">
        <caption className="sr-only">Workload by agent</caption>
        <thead>
          <tr>
            <th scope="col" className={`${TH_CLASS} bg-surface sticky left-0 z-10 w-1/4 text-left`}>Agent</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Task runs</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Thread wakes</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>
              Recorded runtime<span className="sr-only"> (agent runtime, not human hours)</span>
              <FootnoteMark n={1} />
            </th>
            <th scope="col" className={`${TH_CLASS} text-right`}>
              Deliveries
              <FootnoteMark n={2} />
            </th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Replies</th>
          </tr>
        </thead>
        <tbody>
          {data.agents.map((row) => (
            <AgentRow key={row.agent} row={row} compare={compare} />
          ))}
        </tbody>
      </table>
    </ScrollTable>
  );
}

function CoverageSentence({ data, compare }: { data: WorkloadResponse; compare: boolean }): JSX.Element {
  const sum = (pick: (a: WorkloadAgent) => WorkloadPeriod | null) =>
    data.agents.reduce(
      (acc, a) => {
        const p = pick(a);
        if (p) {
          acc.known += p.recorded_runtime.known;
          acc.total += p.recorded_runtime.total;
        }
        return acc;
      },
      { known: 0, total: 0 },
    );
  const cur = sum((a) => a.current);
  const prev = compare ? sum((a) => a.previous) : null;
  return (
    <p className="text-body text-text-secondary mt-3">
      Runtime recorded for {cur.known} of {cur.total} task runs and thread wakes in this period.
      {prev && (
        <> Previous 7 days, for the agents listed: {prev.known} of {prev.total}.</>
      )}
    </p>
  );
}

function UnclassifiedFootnote({ data }: { data: WorkloadResponse }): JSX.Element | null {
  const rows = data.agents.filter((a) => a.current.delivery_unclassified_results > 0);
  if (rows.length === 0) return null;
  const total = rows.reduce((n, a) => n + a.current.delivery_unclassified_results, 0);
  const detail = rows.map((a) => `${a.agent} ${a.current.delivery_unclassified_results}`).join(', ');
  return (
    <p>
      {total} completed results could not be classified as worker delivery and are not counted in
      Deliveries ({detail}).
    </p>
  );
}

export function WorkloadSection({ compare }: { compare: boolean }): JSX.Element {
  const q = useWorkload(compare);
  const data = q.data;
  const meta = data
    ? `Data through ${formatInstant(data.data_through, data.timezone)} (${data.timezone}) · generated ${formatInstant(data.generated_at, data.timezone)}`
    : null;
  const stale = q.isError && data !== undefined;
  const retry = () => void q.refetch();

  let status: string;
  if (q.isPending) status = 'Loading workload';
  else if (!data) status = 'Workload failed to load';
  else if (stale) status = 'Workload is stale: the latest refresh failed';
  else status = `Workload loaded for ${data.agents.length} agent${data.agents.length === 1 ? '' : 's'}`;

  let body: ReactNode;
  if (q.isPending) {
    body = <TableSkeleton columns={COLUMNS} />;
  } else if (!data) {
    body = <SectionError view="Workload" onRetry={retry} retrying={q.isFetching} />;
  } else if (data.agents.length === 0) {
    body = (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-5">
        <p className="text-body text-text-primary font-medium">
          No task runs or thread wakes started in this period.
        </p>
        <ul aria-label="Workload column definitions" className="text-caption text-text-secondary mt-3 space-y-2">
          {DEFINITIONS.map(([term, def]) => (
            <li key={term}>
              <strong className="text-text-primary font-semibold">{term}</strong> — {def}
            </li>
          ))}
        </ul>
      </div>
    );
  } else {
    body = (
      <>
        {stale && (
          <StaleNotice
            dataThrough={`${formatInstant(data.data_through, data.timezone)} (${data.timezone})`}
            onRetry={retry}
            retrying={q.isFetching}
          />
        )}
        <WorkloadTable data={data} compare={compare} />
        <CoverageSentence data={data} compare={compare} />
        <Footnotes>
          <p>
            <sup>1</sup> Agent runtime as recorded, including provider latency, retries and timeouts.
            Parallel runs add up; this is not human working time.
          </p>
          <p>
            <sup>2</sup> Completed worker deliveries only. Manager decisions, child callbacks, thread
            follow-ups, declines, retries and failures are activity, not delivery.
          </p>
          <UnclassifiedFootnote data={data} />
          <p>Counts are CLI-neutral. Changes compare with the previous 7 days and do not mean better or worse.</p>
        </Footnotes>
      </>
    );
  }

  return (
    <UsageSection
      id="usage-workload"
      title="Workload"
      question="Who is carrying the workload?"
      meta={meta}
      status={status}
    >
      {body}
    </UsageSection>
  );
}

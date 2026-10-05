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
import { useUsagePresentation, type UsagePresentation } from './strings';
import type { MessageKey } from '@/lib/i18n';

const COLUMNS: MessageKey[] = ['usage.agent', 'usage.taskRuns', 'usage.threadWakes', 'usage.runtime', 'usage.deliveries', 'usage.replies'];

const DEFINITIONS: Array<[MessageKey, MessageKey]> = [
  ['usage.taskRuns', 'usage.taskRunsDefinition'],
  ['usage.threadWakes', 'usage.threadWakesDefinition'],
  ['usage.runtime', 'usage.runtimeDefinition'],
  ['usage.deliveries', 'usage.deliveriesDefinition'],
  ['usage.replies', 'usage.repliesDefinition'],
];

function runtimeValue(p: WorkloadPeriod, { t, formatDuration }: UsagePresentation): string {
  return p.recorded_runtime.known === 0 ? t('usage.notRecorded') : formatDuration(p.recorded_runtime.seconds);
}

function runtimeCoverage(p: WorkloadPeriod, { t, formatCount }: UsagePresentation): string {
  return t('usage.runtimeCoverage', { known: formatCount(p.recorded_runtime.known), total: formatCount(p.recorded_runtime.total) });
}

function replyCoverage(p: WorkloadPeriod, { t, formatCount }: UsagePresentation): string {
  return t('usage.fraction', { known: formatCount(p.reply_outcome_coverage.recorded), total: formatCount(p.reply_outcome_coverage.total_consumed) });
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
  const presentation = useUsagePresentation();
  const { t, formatCount, formatDuration } = presentation;
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
        value={runtimeValue(cur, presentation)}
        extra={
          <>
            <SubLine>{runtimeCoverage(cur, presentation)}</SubLine>
            {showDelta && <SubLine>{t('usage.before', { value: runtimeCoverage(prev, presentation) })}</SubLine>}
          </>
        }
        delta={
          showDelta && (
            <DeltaLine
              delta={row.deltas?.recorded_runtime_seconds}
              formatAbsolute={formatDuration}
              previous={runtimeValue(prev, presentation)}
              coverage={t('usage.nowBefore', { current: runtimeCoverage(cur, presentation), previous: runtimeCoverage(prev, presentation) })}
            />
          )
        }
      />
      {count('deliveries')}
      <CountCell
        value={formatCount(cur.replies)}
        extra={
          <>
            <SubLine>{t('usage.replyRecorded', { value: replyCoverage(cur, presentation) })}</SubLine>
            {unknownReplies > 0 && <SubLine>{t('usage.replyUnknown', { n: formatCount(unknownReplies) })}</SubLine>}
          </>
        }
        delta={
          showDelta && (
            <DeltaLine
              delta={row.deltas?.replies}
              formatAbsolute={formatCount}
              previous={formatCount(prev.replies)}
              coverage={t('usage.replyNowBefore', { current: replyCoverage(cur, presentation), previous: replyCoverage(prev, presentation) })}
            />
          )
        }
      />
    </tr>
  );
}

function WorkloadTable({ data, compare }: { data: WorkloadResponse; compare: boolean }): JSX.Element {
  const presentation = useUsagePresentation();
  const { t } = presentation;
  return (
    <ScrollTable label={t('usage.workloadTable')}>
      <table className="text-body min-w-content-form w-full table-fixed border-collapse">
        <caption className="sr-only">{t('usage.workloadCaption')}</caption>
        <thead>
          <tr>
            <th scope="col" className={`${TH_CLASS} bg-surface sticky left-0 z-10 w-1/4 text-left`}>{t('usage.agent')}</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.taskRuns')}</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.threadWakes')}</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>
              {t('usage.runtime')}<span className="sr-only">{t('usage.runtimeAssistive')}</span>
              <FootnoteMark n={1} />
            </th>
            <th scope="col" className={`${TH_CLASS} text-right`}>
              {t('usage.deliveries')}
              <FootnoteMark n={2} />
            </th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.replies')}</th>
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
  const presentation = useUsagePresentation();
  const { t, formatCount } = presentation;
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
      {t('usage.runtimeSentence', { known: formatCount(cur.known), total: formatCount(cur.total) })}
      {prev && (
        <>{t('usage.runtimePrevious', { known: formatCount(prev.known), total: formatCount(prev.total) })}</>
      )}
    </p>
  );
}

function UnclassifiedFootnote({ data }: { data: WorkloadResponse }): JSX.Element | null {
  const presentation = useUsagePresentation();
  const { t, formatCount } = presentation;
  const rows = data.agents.filter((a) => a.current.delivery_unclassified_results > 0);
  if (rows.length === 0) return null;
  const total = rows.reduce((n, a) => n + a.current.delivery_unclassified_results, 0);
  const detail = rows.map((a) => `${a.agent} ${formatCount(a.current.delivery_unclassified_results)}`).join(', ');
  return (
    <p>
      {t('usage.unclassifiedDelivery', { n: formatCount(total), detail })}
    </p>
  );
}

export function WorkloadSection({ compare }: { compare: boolean }): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount, formatInstant } = presentation;
  const q = useWorkload(compare);
  const data = q.data;
  const meta = data
    ? t('usage.meta', { through: formatInstant(data.data_through, data.timezone), timezone: data.timezone, generated: formatInstant(data.generated_at, data.timezone) })
    : null;
  const stale = q.isError && data !== undefined;
  const retry = () => void q.refetch();

  let status: string;
  if (q.isPending) status = t('usage.workloadLoading');
  else if (!data) status = t('usage.workloadFailed');
  else if (stale) status = t('usage.workloadStale');
  else status = t('usage.workloadLoaded', { count: data.agents.length, n: formatCount(data.agents.length) });

  let body: ReactNode;
  if (q.isPending) {
    body = <TableSkeleton columns={COLUMNS.map(key => t(key))} />;
  } else if (!data) {
    body = <SectionError view={t('usage.workload')} onRetry={retry} retrying={q.isFetching} />;
  } else if (data.agents.length === 0) {
    body = (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-5">
        <p className="text-body text-text-primary font-medium">
          {t('usage.workloadEmpty')}
        </p>
        <ul aria-label={t('usage.workloadDefinitions')} className="text-caption text-text-secondary mt-3 space-y-2">
          {DEFINITIONS.map(([term, def]) => (
            <li key={term}>
              <strong className="text-text-primary font-semibold">{t(term)}</strong> — {t(def)}
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
            <sup>1</sup> {t('usage.runtimeFootnote')}
          </p>
          <p>
            <sup>2</sup> {t('usage.deliveryFootnote')}
          </p>
          <UnclassifiedFootnote data={data} />
          <p>{t('usage.countFootnote')}</p>
        </Footnotes>
      </>
    );
  }

  return (
    <UsageSection
      id="usage-workload"
      title={t('usage.workload')}
      question={t('usage.workloadQuestion')}
      meta={meta}
      status={status}
    >
      {body}
    </UsageSection>
  );
}

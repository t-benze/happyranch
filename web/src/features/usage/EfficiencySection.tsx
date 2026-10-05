/**
 * Efficiency — one homogeneous cohort (exactly one CLI and one model) broken
 * into the five fixed run types (PRD §5–§8). Nothing renders until a CLI is
 * chosen; choosing one preselects its CLI default (not pinned) cohort, a named
 * model can then be picked, and there is no combined or aggregate option. Every
 * figure, coverage
 * fraction and delta comes from `GET /usage/efficiency`; missing usage is
 * never shown as zero.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type {
  CohortOption,
  CohortSelection,
  DeclineWaste,
  EfficiencyPeriod,
  EfficiencyResponse,
  EfficiencyRow,
  EfficiencyRunType,
  TokenMetric,
  UnattributedCounts,
  UsageDelta,
} from './useUsageData';
import { useEfficiency, useEfficiencyOptions } from './useUsageData';
import {
  CELL_CLASS,
  DASH,
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
import { ROW_LEVEL_WITHHELD_REASONS } from './usageFormat';
import { useUsagePresentation, type UsagePresentation } from './strings';
import type { MessageKey } from '@/lib/i18n';

const RUN_TYPES: Array<[EfficiencyRunType, MessageKey]> = [
  ['worker_task', 'usage.workerTask'],
  ['manager_decision', 'usage.managerDecision'],
  ['thread_reply', 'usage.threadReply'],
  ['thread_followup', 'usage.threadFollowup'],
  ['dream', 'usage.dream'],
];

const COLUMNS: MessageKey[] = ['usage.runType', 'usage.runs', 'usage.freshMedian', 'usage.rereadMedian', 'usage.outputMedian', 'usage.declineWaste'];

const UNPINNED_LABEL = 'usage.unpinned';

const TOKEN_CLASSES = [
  ['fresh_input', 'usage.freshInput'],
  ['reread', 'usage.reread'],
  ['output', 'usage.output'],
] as const;

type TokenClass = (typeof TOKEN_CLASSES)[number][0];

function modelLabel(model: string | null, { t }: UsagePresentation): string {
  return model === null ? t(UNPINNED_LABEL) : model;
}

/* ------------------------------------------------------------------ */
/*  Cohort selectors                                                   */
/* ------------------------------------------------------------------ */

function Pill({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'text-caption focus-visible:ring-accent-ring rounded-sm border px-3 py-1 font-mono transition-colors focus-visible:ring-2 focus-visible:outline-none',
        pressed
          ? 'bg-text-primary text-text-inverse border-text-primary'
          : 'bg-surface border-border-default text-text-secondary hover:text-text-primary',
      )}
    >
      {children}
    </button>
  );
}

function CohortPicker({
  cohorts,
  executor,
  selection,
  onExecutor,
  onModel,
}: {
  cohorts: CohortOption[];
  executor: string | null;
  selection: CohortSelection | null;
  onExecutor: (executor: string) => void;
  onModel: (model: string | null) => void;
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t } = presentation;
  const executors = useMemo(() => [...new Set(cohorts.map((c) => c.executor))], [cohorts]);
  // The unpinned cohort is always offered for the chosen CLI, even when it has
  // no runs in this window (and so no cohort row).
  const models: Array<string | null> = [
    null,
    ...cohorts.filter((c) => c.executor === executor && c.model !== null).map((c) => c.model),
  ];
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3">
      <div role="group" aria-labelledby="usage-cli-label" className="flex flex-wrap items-center gap-2">
        <span id="usage-cli-label" className="text-caption text-text-muted mr-1">{t('usage.cli')}</span>
        {executors.map((ex) => (
          <Pill key={ex} pressed={executor === ex} onClick={() => onExecutor(ex)}>
            {ex}
          </Pill>
        ))}
      </div>
      <div role="group" aria-labelledby="usage-model-label" className="flex flex-wrap items-center gap-2">
        <span id="usage-model-label" className="text-caption text-text-muted mr-1">{t('usage.model')}</span>
        {executor === null ? (
          <span className="text-caption text-text-muted">{t('usage.chooseCliFirst')}</span>
        ) : (
          models.map((model) => (
            <Pill
              key={model ?? '\u0000unpinned'}
              pressed={selection !== null && selection.executor === executor && selection.model === model}
              onClick={() => onModel(model)}
            >
              {modelLabel(model, presentation)}
            </Pill>
          ))
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Cells                                                              */
/* ------------------------------------------------------------------ */

function Sub({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-2xs text-text-muted mt-1 block font-sans">{children}</span>;
}

function coverageFor(p: EfficiencyPeriod, { t, formatCount }: UsagePresentation): string {
  return t('usage.fraction', { known: formatCount(p.usage_coverage.known), total: formatCount(p.usage_coverage.total) });
}

function tokenDisplay(m: TokenMetric, p: EfficiencyPeriod, { t, formatTokenValue }: UsagePresentation): string {
  if (p.runs === 0) return DASH;
  if (m.value !== null) return formatTokenValue(m.value);
  return p.usage_coverage.known === 0 ? t('usage.unknown') : t('usage.notReported');
}

interface RowCompare {
  previous: EfficiencyPeriod;
  deltas: Record<string, UsageDelta>;
  rowWithheld: boolean;
}

function TokenCell({
  name,
  period,
  cmp,
}: {
  name: TokenClass;
  period: EfficiencyPeriod;
  cmp: RowCompare | null;
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount, formatExactTokens, formatTokenValue } = presentation;
  const m = period[name];
  const known = period.usage_coverage.known;
  const display = tokenDisplay(m, period, presentation);
  const partial = name === 'fresh_input' && m.partial_count > 0 && period.runs > 0;
  return (
    <td className={CELL_CLASS}>
      <span
        className={cn(m.value === null ? 'text-text-muted font-sans' : 'text-text-primary')}
        title={m.value !== null ? formatExactTokens(m.value) : undefined}
      >
        {display}
      </span>
      {partial ? (
        <>
          <span className="text-2xs bg-attention-soft text-attention-text mt-1 inline-block rounded-sm px-1 font-sans">
            {t('usage.partial')}
          </span>
          <Sub>
            {t('usage.partialDetail', { reported: formatCount(m.n_reported), known: formatCount(known), partial: formatCount(m.partial_count) })}
          </Sub>
        </>
      ) : (
        m.value !== null &&
        m.n_reported < known && <Sub>{t('usage.classReported', { reported: formatCount(m.n_reported), known: formatCount(known) })}</Sub>
      )}
      {cmp && (
        <DeltaLine
          delta={cmp.deltas[name]}
          formatAbsolute={formatTokenValue}
          previous={tokenDisplay(cmp.previous[name], cmp.previous, presentation)}
          coverage={t('usage.usageNowBefore', { current: coverageFor(period, presentation), previous: coverageFor(cmp.previous, presentation) })}
          rowWithheld={cmp.rowWithheld}
        />
      )}
    </td>
  );
}

function DeclineCell({
  period,
  cmp,
}: {
  period: EfficiencyPeriod;
  cmp: RowCompare | null;
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount, formatExactTokens, formatRate, formatTokenValue } = presentation;
  const d: DeclineWaste | null = period.decline_waste;
  if (d === null) return <td className={CELL_CLASS} />;
  if (period.runs === 0) {
    return (
      <td className={CELL_CLASS}>
        <span className="text-text-muted">{DASH}</span>
      </td>
    );
  }
  const prev = cmp?.previous.decline_waste ?? null;
  const declineCoverage = (x: DeclineWaste) => t('usage.fraction', { known: formatCount(x.usage_known), total: formatCount(x.declined) });
  const delta = (key: string, previous: string) =>
    cmp && (
      <DeltaLine
        delta={cmp.deltas[key]}
        formatAbsolute={formatTokenValue}
        previous={previous}
        coverage={
          prev
            ? t('usage.declineNowBefore', { current: declineCoverage(d), previous: declineCoverage(prev) })
            : t('usage.declineCoverage', { value: declineCoverage(d) })
        }
        rowWithheld={cmp.rowWithheld}
      />
    );
  const rateDelta = delta('decline_rate', prev?.rate != null ? formatRate(prev.rate) : DASH);

  if (d.state === 'no_declines') {
    return (
      <td className={CELL_CLASS}>
        <span className="text-text-primary">{t('usage.noDeclines')}</span>
        {rateDelta}
      </td>
    );
  }
  return (
    <td className={CELL_CLASS}>
      <span className="text-text-primary">{d.rate !== null ? formatRate(d.rate) : DASH}</span>
      <Sub>
        {t('usage.declined', { declined: formatCount(d.declined), total: formatCount(d.total) })}
      </Sub>
      {rateDelta}
      {d.usage_known === 0 ? (
        <Sub>{t('usage.declinesUnknown')}</Sub>
      ) : (
        <>
          <Sub>{t('usage.declineTotals')}</Sub>
          <dl className="text-2xs mt-1 space-y-1">
            {TOKEN_CLASSES.map(([key, label]) => {
              const total = d[key];
              const prevTotal = prev?.[key];
              return (
                <div key={key} className="flex flex-wrap items-baseline justify-end gap-x-2">
                  <dt className="text-text-muted font-sans">{t(label)}</dt>
                  <dd className="text-text-primary">
                    {total.value !== null ? (
                      <span title={formatExactTokens(total.value)}>{formatTokenValue(total.value)}</span>
                    ) : (
                      <span className="text-text-muted font-sans">{t('usage.notReported')}</span>
                    )}
                    {delta(
                      `decline_${key}`,
                      prevTotal && prevTotal.value !== null ? formatTokenValue(prevTotal.value) : t('usage.notReported'),
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
          <Sub>
            {t('usage.declineKnown', { known: formatCount(d.usage_known), total: formatCount(d.declined) })}
          </Sub>
        </>
      )}
    </td>
  );
}

function RunTypeRow({
  label,
  row,
  compare,
}: {
  label: string;
  row: EfficiencyRow;
  compare: boolean;
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount, withheldExplanation } = presentation;
  const cur = row.current;
  let cmp: RowCompare | null = null;
  if (compare && row.previous && row.deltas) {
    const values = Object.values(row.deltas);
    const rowWithheld =
      values.length > 0 &&
      values.every(
        (d) => d.kind === 'withheld' && d.withheld_reason !== null && ROW_LEVEL_WITHHELD_REASONS.has(d.withheld_reason),
      );
    cmp = { previous: row.previous, deltas: row.deltas, rowWithheld };
  }
  const rowReason = cmp?.rowWithheld ? Object.values(cmp.deltas)[0].withheld_reason : null;

  return (
    <tr className="border-border-default border-b last:border-0">
      <th scope="row" className={IDENTITY_CLASS}>
        <span className="text-text-primary block font-medium">{label}</span>
        {cur.runs === 0 && <Sub>{t('usage.noRuns')}</Sub>}
        {cmp ? (
          <Sub>
            {t('usage.rowCoverageCompare', { current: coverageFor(cur, presentation), previous: coverageFor(cmp.previous, presentation) })}
          </Sub>
        ) : (
          cur.runs > 0 && <Sub>{t('usage.rowCoverage', { value: coverageFor(cur, presentation) })}</Sub>
        )}
        {rowReason && <Sub>{withheldExplanation(rowReason)}</Sub>}
      </th>
      <td className={CELL_CLASS}>
        <span className="text-text-primary">{formatCount(cur.runs)}</span>
        {cmp && (
          <DeltaLine
            delta={cmp.deltas.runs}
            formatAbsolute={formatCount}
            previous={formatCount(cmp.previous.runs)}
            coverage={t('usage.usageNowBefore', { current: coverageFor(cur, presentation), previous: coverageFor(cmp.previous, presentation) })}
            rowWithheld={cmp.rowWithheld}
          />
        )}
      </td>
      {TOKEN_CLASSES.map(([key]) => (
        <TokenCell key={key} name={key} period={cur} cmp={cmp} />
      ))}
      <DeclineCell period={cur} cmp={cmp} />
    </tr>
  );
}

function EfficiencyTable({
  data,
  selection,
  compare,
}: {
  data: EfficiencyResponse;
  selection: CohortSelection;
  compare: boolean;
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t } = presentation;
  const byType = new Map(data.rows.map((r) => [r.run_type, r]));
  return (
    <ScrollTable label={t('usage.efficiencyTable')}>
      <table className="text-body min-w-content-form md:min-w-content-narrow w-full table-fixed border-collapse">
        <caption className="sr-only">
          {t('usage.efficiencyCaption', { executor: selection.executor, model: modelLabel(selection.model, presentation) })}
        </caption>
        <thead>
          <tr>
            <th scope="col" className={`${TH_CLASS} bg-surface sticky left-0 z-10 w-1/5 text-left`}>{t('usage.runType')}</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.runs')}</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.freshMedian')}<FootnoteMark n={1} /></th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.rereadMedian')}<FootnoteMark n={2} /></th>
            <th scope="col" className={`${TH_CLASS} text-right`}>{t('usage.outputMedian')}<FootnoteMark n={3} /></th>
            <th scope="col" className={`${TH_CLASS} w-1/5 text-right`}>{t('usage.declineWaste')}<FootnoteMark n={4} /></th>
          </tr>
        </thead>
        <tbody>
          {RUN_TYPES.map(([type, label]) => {
            const row = byType.get(type);
            return row ? <RunTypeRow key={type} label={t(label)} row={row} compare={compare} /> : null;
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

const UNATTRIBUTED_LABELS: Array<[keyof UnattributedCounts, MessageKey]> = [
  ...RUN_TYPES,
  ['task_unclassified', 'usage.taskUnclassified'],
  ['recovery', 'usage.recovery'],
];

function UnattributedBlock({
  counts,
  period,
}: {
  counts: UnattributedCounts;
  period: 'current' | 'previous';
}): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount } = presentation;
  const periodLabel = t(period === 'current' ? 'usage.thisPeriod' : 'usage.previousPeriod');
  const entries = UNATTRIBUTED_LABELS.filter(([key]) => counts[key] > 0);
  const total = entries.reduce((n, [key]) => n + counts[key], 0);
  if (total === 0) {
    return <p>{t('usage.allAttributed', { period: periodLabel })}</p>;
  }
  return (
    <div>
      <p>
        {t('usage.unattributed', { count: total, n: formatCount(total), period: periodLabel })}
      </p>
      <ul aria-label={t('usage.unattributedLabel', { period: periodLabel })} className="mt-1 list-disc pl-5">
        {entries.map(([key, label]) => (
          <li key={key}>
            {RUN_TYPES.some(([type]) => type === key) ? t('usage.unattributedType', { label: t(label) }) : t(label)}: {formatCount(counts[key])}
          </li>
        ))}
      </ul>
    </div>
  );
}

function UsageSentence({ data, compare }: { data: EfficiencyResponse; compare: boolean }): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatCount } = presentation;
  const sum = (pick: (r: EfficiencyRow) => EfficiencyPeriod | null) =>
    data.rows.reduce(
      (acc, r) => {
        const p = pick(r);
        if (p) {
          acc.known += p.usage_coverage.known;
          acc.total += p.usage_coverage.total;
        }
        return acc;
      },
      { known: 0, total: 0 },
    );
  const cur = sum((r) => r.current);
  const prev = compare ? sum((r) => r.previous) : null;
  return (
    <p className="text-body text-text-secondary mt-3">
      <span>{t('usage.usageSentence', { known: formatCount(cur.known), total: formatCount(cur.total) })}</span>
      {prev && <span>{t('usage.usagePrevious', { known: formatCount(prev.known), total: formatCount(prev.total) })}</span>}{' '}
      {t('usage.usageMissingFootnote')}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/*  Section                                                            */
/* ------------------------------------------------------------------ */

export function EfficiencySection({ compare }: { compare: boolean }): JSX.Element {
  const presentation = useUsagePresentation();
  const { t, formatInstant } = presentation;
  const optionsQ = useEfficiencyOptions(compare);
  const [executor, setExecutor] = useState<string | null>(null);
  const [selection, setSelection] = useState<CohortSelection | null>(null);

  const cohorts = optionsQ.data?.cohorts ?? [];
  // Until the reconciliation effect below clears them, a stored CLI that the
  // latest options no longer offer is treated as not chosen: no pills, no request.
  const offered = new Set(cohorts.map((c) => c.executor));
  const chosenExecutor = executor !== null && offered.has(executor) ? executor : null;
  const chosen = selection !== null && offered.has(selection.executor) ? selection : null;
  const dataQ = useEfficiency(chosen, compare);

  // A named model that is no longer offered (e.g. a previous-only cohort after
  // Compare is switched off) is never silently kept: it falls back to the CLI's
  // unpinned cohort, which is always offered. Both are cleared when the CLI is gone.
  useEffect(() => {
    if (!optionsQ.isSuccess || optionsQ.isPlaceholderData) return;
    const list = optionsQ.data.cohorts;
    if (executor !== null && !list.some((c) => c.executor === executor)) {
      setExecutor(null);
      setSelection(null);
    } else if (
      selection !== null &&
      selection.model !== null &&
      !list.some((c) => c.executor === selection.executor && c.model === selection.model)
    ) {
      setSelection({ executor: selection.executor, model: null });
    }
  }, [optionsQ.isSuccess, optionsQ.isPlaceholderData, optionsQ.data, executor, selection]);

  const onExecutor = (ex: string) => {
    if (ex === executor) return;
    setExecutor(ex);
    setSelection({ executor: ex, model: null });
  };
  const onModel = (model: string | null) => {
    if (executor !== null) setSelection({ executor, model });
  };

  const shown = chosen ? dataQ.data : optionsQ.data;
  const meta = shown
    ? t('usage.meta', { through: formatInstant(shown.data_through, shown.timezone), timezone: shown.timezone, generated: formatInstant(shown.generated_at, shown.timezone) })
    : null;

  let status: string;
  let body: ReactNode;
  const optionsFailed = optionsQ.isError && optionsQ.data === undefined;

  if (optionsQ.isPending) {
    status = t('usage.efficiencyOptionsLoading');
    body = <TableSkeleton columns={COLUMNS.map(key => t(key))} rows={5} />;
  } else if (optionsFailed) {
    status = t('usage.efficiencyOptionsFailed');
    body = <SectionError view={t('usage.efficiency')} onRetry={() => void optionsQ.refetch()} retrying={optionsQ.isFetching} />;
  } else if (cohorts.length === 0) {
    status = t('usage.efficiencyNoCohorts');
    body = (
      <p className="text-body text-text-secondary">
        {t('usage.efficiencyEmpty')}
      </p>
    );
  } else if (chosen === null) {
    status = t('usage.efficiencyChooseCli');
    body = (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-5">
        <p className="text-body text-text-primary">
          {t('usage.chooseCohort')}
        </p>
      </div>
    );
  } else if (dataQ.isPending) {
    status = t('usage.efficiencyLoading');
    body = <TableSkeleton columns={COLUMNS.map(key => t(key))} rows={5} />;
  } else if (!dataQ.data) {
    status = t('usage.efficiencyFailed');
    body = <SectionError view={t('usage.efficiency')} onRetry={() => void dataQ.refetch()} retrying={dataQ.isFetching} />;
  } else {
    const data = dataQ.data;
    const stale = dataQ.isError;
    status = stale
      ? t('usage.efficiencyStale')
      : t('usage.efficiencyLoaded', { executor: chosen.executor, model: modelLabel(chosen.model, presentation) });
    body = (
      <>
        {stale && (
          <StaleNotice
            dataThrough={`${formatInstant(data.data_through, data.timezone)} (${data.timezone})`}
            onRetry={() => void dataQ.refetch()}
            retrying={dataQ.isFetching}
          />
        )}
        <EfficiencyTable data={data} selection={chosen} compare={compare} />
        <UsageSentence data={data} compare={compare} />
        <div className="text-body text-text-secondary mt-3 space-y-2">
          <UnattributedBlock counts={data.unattributed.current} period="current" />
          {compare && data.unattributed.previous && (
            <UnattributedBlock counts={data.unattributed.previous} period="previous" />
          )}
        </div>
        <Footnotes>
          <p><sup>1</sup> {t('usage.freshFootnote')}</p>
          <p><sup>2</sup> {t('usage.rereadFootnote')}</p>
          <p><sup>3</sup> {t('usage.outputFootnote')}</p>
          <p>
            <sup>4</sup> {t('usage.declineFootnote')}
          </p>
          <p>
            {t('usage.medianFootnote')}
          </p>
        </Footnotes>
      </>
    );
  }

  return (
    <UsageSection
      id="usage-efficiency"
      title={t('usage.efficiency')}
      question={t('usage.efficiencyQuestion')}
      meta={meta}
      status={status}
      controls={
        cohorts.length > 0 && (
          <CohortPicker
            cohorts={cohorts}
            executor={chosenExecutor}
            selection={chosen}
            onExecutor={onExecutor}
            onModel={onModel}
          />
        )
      }
    >
      {body}
    </UsageSection>
  );
}

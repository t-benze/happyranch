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
import {
  ROW_LEVEL_WITHHELD_REASONS,
  formatExactTokens,
  formatInstant,
  formatRate,
  formatTokenValue,
  withheldExplanation,
} from './usageFormat';

const RUN_TYPES: Array<[EfficiencyRunType, string]> = [
  ['worker_task', 'Worker task'],
  ['manager_decision', 'Manager decision'],
  ['thread_reply', 'Thread reply'],
  ['thread_followup', 'Thread follow-up'],
  ['dream', 'Dream'],
];

const COLUMNS = ['Run type', 'Runs', 'Median fresh input', 'Median re-read', 'Median output', 'Decline waste'];

const UNPINNED_LABEL = 'CLI default (not pinned)';

const TOKEN_CLASSES = [
  ['fresh_input', 'Fresh input'],
  ['reread', 'Re-read'],
  ['output', 'Output'],
] as const;

type TokenClass = (typeof TOKEN_CLASSES)[number][0];

function modelLabel(model: string | null): string {
  return model === null ? UNPINNED_LABEL : model;
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
        <span id="usage-cli-label" className="text-caption text-text-muted mr-1">CLI</span>
        {executors.map((ex) => (
          <Pill key={ex} pressed={executor === ex} onClick={() => onExecutor(ex)}>
            {ex}
          </Pill>
        ))}
      </div>
      <div role="group" aria-labelledby="usage-model-label" className="flex flex-wrap items-center gap-2">
        <span id="usage-model-label" className="text-caption text-text-muted mr-1">Model</span>
        {executor === null ? (
          <span className="text-caption text-text-muted">Choose a CLI first</span>
        ) : (
          models.map((model) => (
            <Pill
              key={model ?? '\u0000unpinned'}
              pressed={selection !== null && selection.executor === executor && selection.model === model}
              onClick={() => onModel(model)}
            >
              {modelLabel(model)}
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

function coverageFor(p: EfficiencyPeriod): string {
  return `${p.usage_coverage.known} of ${p.usage_coverage.total}`;
}

function tokenDisplay(m: TokenMetric, p: EfficiencyPeriod): string {
  if (p.runs === 0) return DASH;
  if (m.value !== null) return formatTokenValue(m.value);
  return p.usage_coverage.known === 0 ? 'Unknown' : 'Not reported';
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
  const m = period[name];
  const known = period.usage_coverage.known;
  const display = tokenDisplay(m, period);
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
            Partial
          </span>
          <Sub>
            median of {m.n_reported} of {known} runs; cache write not reported for {m.partial_count}
          </Sub>
        </>
      ) : (
        m.value !== null &&
        m.n_reported < known && <Sub>{m.n_reported} of {known} class-reported</Sub>
      )}
      {cmp && (
        <DeltaLine
          delta={cmp.deltas[name]}
          formatAbsolute={formatTokenValue}
          previous={tokenDisplay(cmp.previous[name], cmp.previous)}
          coverage={`usage known ${coverageFor(period)} now · ${coverageFor(cmp.previous)} before`}
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
  const declineCoverage = (x: DeclineWaste) => `${x.usage_known} of ${x.declined}`;
  const delta = (key: string, previous: string) =>
    cmp && (
      <DeltaLine
        delta={cmp.deltas[key]}
        formatAbsolute={formatTokenValue}
        previous={previous}
        coverage={
          prev
            ? `decline usage known ${declineCoverage(d)} now · ${declineCoverage(prev)} before`
            : `decline usage known ${declineCoverage(d)}`
        }
        rowWithheld={cmp.rowWithheld}
      />
    );
  const rateDelta = delta('decline_rate', prev?.rate != null ? formatRate(prev.rate) : DASH);

  if (d.state === 'no_declines') {
    return (
      <td className={CELL_CLASS}>
        <span className="text-text-primary">0% · no declines</span>
        {rateDelta}
      </td>
    );
  }
  return (
    <td className={CELL_CLASS}>
      <span className="text-text-primary">{d.rate !== null ? formatRate(d.rate) : DASH}</span>
      <Sub>
        {d.declined} of {d.total} declined
      </Sub>
      {rateDelta}
      {d.usage_known === 0 ? (
        <Sub>usage unknown for all declined wakes</Sub>
      ) : (
        <>
          <Sub>Known totals for declined wakes</Sub>
          <dl className="text-2xs mt-1 space-y-1">
            {TOKEN_CLASSES.map(([key, label]) => {
              const total = d[key];
              const prevTotal = prev?.[key];
              return (
                <div key={key} className="flex flex-wrap items-baseline justify-end gap-x-2">
                  <dt className="text-text-muted font-sans">{label}</dt>
                  <dd className="text-text-primary">
                    {total.value !== null ? (
                      <span title={formatExactTokens(total.value)}>{formatTokenValue(total.value)}</span>
                    ) : (
                      <span className="text-text-muted font-sans">Not reported</span>
                    )}
                    {delta(
                      `decline_${key}`,
                      prevTotal && prevTotal.value !== null ? formatTokenValue(prevTotal.value) : 'Not reported',
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
          <Sub>
            usage known for {d.usage_known} of {d.declined} declined wakes
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
        {cur.runs === 0 && <Sub>No runs in this period</Sub>}
        {cmp ? (
          <Sub>
            Usage known for {coverageFor(cur)} runs now and {coverageFor(cmp.previous)} in the previous 7 days
          </Sub>
        ) : (
          cur.runs > 0 && <Sub>Usage known for {coverageFor(cur)} runs</Sub>
        )}
        {rowReason && <Sub>{withheldExplanation(rowReason)}</Sub>}
      </th>
      <td className={CELL_CLASS}>
        <span className="text-text-primary">{cur.runs}</span>
        {cmp && (
          <DeltaLine
            delta={cmp.deltas.runs}
            formatAbsolute={(n) => String(n)}
            previous={String(cmp.previous.runs)}
            coverage={`usage known ${coverageFor(cur)} now · ${coverageFor(cmp.previous)} before`}
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
  const byType = new Map(data.rows.map((r) => [r.run_type, r]));
  return (
    <ScrollTable label="Efficiency table">
      <table className="text-body min-w-content-form md:min-w-content-narrow w-full table-fixed border-collapse">
        <caption className="sr-only">
          Efficiency by run type for {selection.executor} · {modelLabel(selection.model)}
        </caption>
        <thead>
          <tr>
            <th scope="col" className={`${TH_CLASS} bg-surface sticky left-0 z-10 w-1/5 text-left`}>Run type</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Runs</th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Median fresh input<FootnoteMark n={1} /></th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Median re-read<FootnoteMark n={2} /></th>
            <th scope="col" className={`${TH_CLASS} text-right`}>Median output<FootnoteMark n={3} /></th>
            <th scope="col" className={`${TH_CLASS} w-1/5 text-right`}>Decline waste<FootnoteMark n={4} /></th>
          </tr>
        </thead>
        <tbody>
          {RUN_TYPES.map(([type, label]) => {
            const row = byType.get(type);
            return row ? <RunTypeRow key={type} label={label} row={row} compare={compare} /> : null;
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

const UNATTRIBUTED_LABELS: Array<[keyof UnattributedCounts, string]> = [
  ...RUN_TYPES.map(([key, label]) => [key, `${label} (no CLI/model on record)`] as [keyof UnattributedCounts, string]),
  ['task_unclassified', 'Task sessions without a run type'],
  ['recovery', 'Completion-recovery sessions'],
];

function UnattributedBlock({
  counts,
  period,
}: {
  counts: UnattributedCounts;
  period: 'this period' | 'the previous 7 days';
}): JSX.Element {
  const entries = UNATTRIBUTED_LABELS.filter(([key]) => counts[key] > 0);
  const total = entries.reduce((n, [key]) => n + counts[key], 0);
  if (total === 0) {
    return <p>All lifecycle runs in {period} have a CLI/model on record.</p>;
  }
  return (
    <div>
      <p>
        {total} lifecycle run{total === 1 ? '' : 's'} in {period} {total === 1 ? 'is' : 'are'} outside every
        cohort and {total === 1 ? 'is' : 'are'} not counted in the rows above.
      </p>
      <ul aria-label={`Unattributed lifecycle runs ${period}`} className="mt-1 list-disc pl-5">
        {entries.map(([key, label]) => (
          <li key={key}>
            {label}: {counts[key]}
          </li>
        ))}
      </ul>
    </div>
  );
}

function UsageSentence({ data, compare }: { data: EfficiencyResponse; compare: boolean }): JSX.Element {
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
      <span>Usage known for {cur.known} of {cur.total} runs in this period.</span>
      {prev && <span> {prev.known} of {prev.total} in the previous 7 days.</span>}{' '}
      Runs without usage stay in the count and are never treated as zero.
    </p>
  );
}

/* ------------------------------------------------------------------ */
/*  Section                                                            */
/* ------------------------------------------------------------------ */

export function EfficiencySection({ compare }: { compare: boolean }): JSX.Element {
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
    ? `Data through ${formatInstant(shown.data_through, shown.timezone)} (${shown.timezone}) · generated ${formatInstant(shown.generated_at, shown.timezone)}`
    : null;

  let status: string;
  let body: ReactNode;
  const optionsFailed = optionsQ.isError && optionsQ.data === undefined;

  if (optionsQ.isPending) {
    status = 'Loading efficiency cohorts';
    body = <TableSkeleton columns={COLUMNS} rows={5} />;
  } else if (optionsFailed) {
    status = 'Efficiency cohorts failed to load';
    body = <SectionError view="Efficiency" onRetry={() => void optionsQ.refetch()} retrying={optionsQ.isFetching} />;
  } else if (cohorts.length === 0) {
    status = 'No CLI/model cohorts have runs in this period';
    body = (
      <p className="text-body text-text-secondary">
        No CLI/model cohort has runs in this period, so there is nothing to compare within a cohort.
      </p>
    );
  } else if (chosen === null) {
    status = 'Choose a CLI to see Efficiency';
    body = (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-5">
        <p className="text-body text-text-primary">
          Choose one CLI. Its CLI default (not pinned) cohort is preselected and you can pick a named
          model. Token reporting differs by CLI and model, so there is no combined view.
        </p>
      </div>
    );
  } else if (dataQ.isPending) {
    status = 'Loading efficiency';
    body = <TableSkeleton columns={COLUMNS} rows={5} />;
  } else if (!dataQ.data) {
    status = 'Efficiency failed to load';
    body = <SectionError view="Efficiency" onRetry={() => void dataQ.refetch()} retrying={dataQ.isFetching} />;
  } else {
    const data = dataQ.data;
    const stale = dataQ.isError;
    status = stale
      ? 'Efficiency is stale: the latest refresh failed'
      : `Efficiency loaded for ${chosen.executor} · ${modelLabel(chosen.model)}`;
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
          <UnattributedBlock counts={data.unattributed.current} period="this period" />
          {compare && data.unattributed.previous && (
            <UnattributedBlock counts={data.unattributed.previous} period="the previous 7 days" />
          )}
        </div>
        <Footnotes>
          <p><sup>1</sup> Fresh input = uncached input + cache-write tokens reported by the provider.</p>
          <p><sup>2</sup> Re-read = cache-read tokens.</p>
          <p><sup>3</sup> Output includes reasoning once, whether the provider reports it inside output or separately.</p>
          <p>
            <sup>4</sup> Share of wakes that declined, with the usage known for those declined wakes. Token
            classes are kept separate.
          </p>
          <p>
            Medians are over runs with known usage. The three classes are never added together, and
            nothing is compared across CLIs or models. Not reported means the provider does not report
            that class; 0 is a reported zero.
          </p>
        </Footnotes>
      </>
    );
  }

  return (
    <UsageSection
      id="usage-efficiency"
      title="Efficiency"
      question="Within one CLI and one model, which run types use tokens inefficiently?"
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

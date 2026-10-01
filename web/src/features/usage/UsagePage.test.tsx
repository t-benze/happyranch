/**
 * Usage v1 page contract (THR-272 PR4; PRD TASK-9165 §3–§11).
 *
 * The page is rendered through the real AppProvider/QueryClient and router;
 * only the two read-only API functions (`usage.getWorkload` /
 * `usage.getEfficiency`) are replaced with deterministic fixtures shaped
 * exactly like the daemon's response models.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    usage: { ...actual.usage, getWorkload: vi.fn(), getEfficiency: vi.fn() },
  };
});

import { usage } from '@/lib/api';
import type {
  CohortOption,
  EfficiencyPeriod,
  EfficiencyResponse,
  EfficiencyRow,
  EfficiencyRunType,
  UnattributedCounts,
  UsageDelta,
  WorkloadAgent,
  WorkloadPeriod,
  WorkloadResponse,
} from './useUsageData';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { I18nTestBoundary } from '@/test/render';
import { UsagePage } from './UsagePage';

const getWorkload = vi.mocked(usage.getWorkload);
const getEfficiency = vi.mocked(usage.getEfficiency);

const SLUG = 'acme';
const TZ = 'Asia/Shanghai';

/* ------------------------------------------------------------------ */
/*  Fixture builders (shape-identical to runtime/daemon/routes/usage.py) */
/* ------------------------------------------------------------------ */

const CURRENT_WINDOW = {
  start_utc: '2026-09-22T06:03:00Z',
  end_utc: '2026-09-29T06:03:00Z',
  start_local: '2026-09-22T14:03:00+08:00',
  end_local: '2026-09-29T14:03:00+08:00',
};
const PREVIOUS_WINDOW = {
  start_utc: '2026-09-15T06:03:00Z',
  end_utc: '2026-09-22T06:03:00Z',
  start_local: '2026-09-15T14:03:00+08:00',
  end_local: '2026-09-22T14:03:00+08:00',
};

function meta(compare: boolean) {
  return {
    generated_at: '2026-09-29T06:03:00Z',
    data_through: '2026-09-29T06:03:00Z',
    timezone: TZ,
    current_window: CURRENT_WINDOW,
    previous_window: compare ? PREVIOUS_WINDOW : null,
  };
}

function abs(value: number): UsageDelta {
  return { kind: value === 0 ? 'no_change' : 'absolute', value, withheld_reason: null };
}
function pct(value: number): UsageDelta {
  return { kind: 'percent', value, withheld_reason: null };
}
function withheld(reason: string): UsageDelta {
  return { kind: 'withheld', value: null, withheld_reason: reason };
}
const NEW_FROM_ZERO = (value: number): UsageDelta => ({ kind: 'new_from_zero', value, withheld_reason: null });
const NO_CHANGE: UsageDelta = { kind: 'no_change', value: 0, withheld_reason: null };

function wPeriod(over: Partial<WorkloadPeriod> = {}): WorkloadPeriod {
  return {
    task_runs: 9,
    thread_wakes: 62,
    recorded_runtime: { seconds: 15000, known: 71, total: 71 },
    deliveries: 2,
    delivery_unclassified_results: 0,
    replies: 22,
    reply_outcome_coverage: { recorded: 22, total_consumed: 22 },
    ...over,
  };
}

function wAgent(agent: string, over: Partial<WorkloadAgent> = {}): WorkloadAgent {
  return { agent, current: wPeriod(), previous: null, deltas: null, ...over };
}

function workload(agents: WorkloadAgent[], compare = false): WorkloadResponse {
  return { ...meta(compare), agents };
}

function metric(value: number | null, n_reported: number, partial_count = 0) {
  return { value, n_reported, partial_count };
}

function ePeriod(over: Partial<EfficiencyPeriod> = {}): EfficiencyPeriod {
  return {
    runs: 20,
    usage_coverage: { known: 20, total: 20, ratio: 1 },
    fresh_input: metric(12000, 20),
    reread: metric(54000, 20),
    output: metric(4100, 20),
    decline_waste: null,
    ...over,
  };
}

function declines(over: Partial<NonNullable<EfficiencyPeriod['decline_waste']>> = {}) {
  return {
    state: 'reported' as const,
    declined: 34,
    total: 96,
    rate: 34 / 96,
    usage_known: 31,
    fresh_input: { value: 56000, n_reported: 31 },
    reread: { value: 603000, n_reported: 31 },
    output: { value: 18000, n_reported: 31 },
    ...over,
  };
}

const NO_DECLINES = {
  state: 'no_declines' as const,
  declined: 0,
  total: 8,
  rate: 0,
  usage_known: 0,
  fresh_input: { value: null, n_reported: 0 },
  reread: { value: null, n_reported: 0 },
  output: { value: null, n_reported: 0 },
};

const RUN_TYPES: EfficiencyRunType[] = [
  'worker_task', 'manager_decision', 'thread_reply', 'thread_followup', 'dream',
];

function eRow(run_type: EfficiencyRunType, over: Partial<EfficiencyRow> = {}): EfficiencyRow {
  const thread = run_type === 'thread_reply' || run_type === 'thread_followup';
  return {
    run_type,
    current: ePeriod({ decline_waste: thread ? NO_DECLINES : null }),
    previous: null,
    deltas: null,
    ...over,
  };
}

function unattributed(over: Partial<UnattributedCounts> = {}): UnattributedCounts {
  return {
    worker_task: 0, manager_decision: 0, thread_reply: 0, thread_followup: 0, dream: 0,
    task_unclassified: 0, recovery: 0, ...over,
  };
}

const COHORTS: CohortOption[] = [
  { executor: 'claude', model: null, model_unpinned: true, current_runs: 3, previous_runs: 0 },
  { executor: 'claude', model: 'opus', model_unpinned: false, current_runs: 5, previous_runs: 0 },
  { executor: 'claude', model: 'sonnet', model_unpinned: false, current_runs: 40, previous_runs: 0 },
  { executor: 'codex', model: 'gpt-5', model_unpinned: false, current_runs: 12, previous_runs: 0 },
];

function efficiency(
  over: Partial<EfficiencyResponse> = {},
  compare = false,
): EfficiencyResponse {
  return {
    ...meta(compare),
    cohorts: COHORTS,
    unattributed: { current: unattributed(), previous: compare ? unattributed() : null },
    rows: [],
    ...over,
  };
}

function fullRows(overrides: Partial<Record<EfficiencyRunType, Partial<EfficiencyRow>>> = {}) {
  return RUN_TYPES.map((t) => eRow(t, overrides[t] ?? {}));
}

/* ------------------------------------------------------------------ */
/*  Render                                                              */
/* ------------------------------------------------------------------ */

function renderPage() {
  const client = makeQueryClient();
  const utils = render(
    <MemoryRouter initialEntries={[`/orgs/${SLUG}/usage`]}>
      <I18nTestBoundary>
        <AppProvider client={client}>
          <Routes>
            <Route path="/orgs/:slug/usage" element={<UsagePage />} />
          </Routes>
        </AppProvider>
      </I18nTestBoundary>
    </MemoryRouter>,
  );
  return { ...utils, client };
}

/** Default backend: populated Workload, cohort options, populated selection. */
function serve(opts: {
  workload?: (compare: boolean) => WorkloadResponse | Promise<WorkloadResponse>;
  options?: (compare: boolean) => EfficiencyResponse | Promise<EfficiencyResponse>;
  selected?: (params: Record<string, unknown>) => EfficiencyResponse | Promise<EfficiencyResponse>;
} = {}) {
  getWorkload.mockImplementation(async (_slug, compare = false) =>
    opts.workload ? opts.workload(compare) : workload([wAgent('product_lead')], compare),
  );
  getEfficiency.mockImplementation(async (_slug, params = {}) => {
    const compare = params.compare ?? false;
    if (params.executor) {
      return opts.selected
        ? opts.selected(params as Record<string, unknown>)
        : efficiency({ rows: fullRows() }, compare);
    }
    return opts.options ? opts.options(compare) : efficiency({}, compare);
  });
}

async function selectCohort(cli: string, model: string) {
  fireEvent.click(await screen.findByRole('button', { name: cli }));
  fireEvent.click(await screen.findByRole('button', { name: model }));
}

function efficiencyTable() {
  return screen.findByRole('table', { name: /Efficiency by run type/ });
}

async function rowFor(table: HTMLElement, label: string) {
  const header = within(table).getByRole('rowheader', { name: new RegExp(`^${label}`) });
  return header.closest('tr') as HTMLElement;
}

beforeEach(() => {
  getWorkload.mockReset();
  getEfficiency.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

/* ================================================================== */
/*  A. Page structure + shared controls                                 */
/* ================================================================== */

describe('Usage v1 — structure and shared controls', () => {
  it('renders exactly the Workload and Efficiency sections with a fixed "Last 7 days" window from the response', async () => {
    serve();
    renderPage();

    expect(await screen.findByRole('heading', { level: 2, name: 'Workload' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Efficiency' })).toBeInTheDocument();
    expect(
      await screen.findByText('Last 7 days · Sep 22, 14:03 – Sep 29, 14:03 (Asia/Shanghai)'),
    ).toBeInTheDocument();
  });

  it('Compare is an accessible switch, off by default, and both views are first requested with compare=false', async () => {
    serve();
    renderPage();

    const toggle = await screen.findByRole('switch', { name: 'Compare with previous 7 days' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(getWorkload).toHaveBeenCalledWith(SLUG, false));
    expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false });
  });

  it('turning Compare on refetches Workload, the cohort options and the selected cohort with compare=true', async () => {
    serve();
    renderPage();
    await selectCohort('claude', 'sonnet');
    await efficiencyTable();

    fireEvent.click(screen.getByRole('switch', { name: 'Compare with previous 7 days' }));

    expect(screen.getByRole('switch', { name: 'Compare with previous 7 days' })).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => expect(getWorkload).toHaveBeenCalledWith(SLUG, true));
    expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: true });
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: true, executor: 'claude', model: 'sonnet' }),
    );
  });

  it('shows Data through and generated-at in the response timezone', async () => {
    serve();
    renderPage();

    const workloadSection = await screen.findByRole('region', { name: 'Workload' });
    expect(
      await within(workloadSection).findByText(
        'Data through Sep 29, 14:03 (Asia/Shanghai) · generated Sep 29, 14:03',
      ),
    ).toBeInTheDocument();
  });

  it('removes the token-centric page: no export, cost, token burn hero, window choice, breakdown, cache or team cards, or aggregate CLI/model option', async () => {
    serve();
    renderPage();
    await screen.findByRole('table', { name: 'Workload by agent' });

    expect(screen.queryByRole('button', { name: /export/i })).toBeNull();
    expect(screen.queryByText(/not metered/i)).toBeNull();
    expect(screen.queryByText(/\$/)).toBeNull();
    expect(screen.queryByText(/token burn/i)).toBeNull();
    expect(screen.queryAllByText(/token usage and cache savings/i)).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /^All/i })).toBeNull();
    // The window is fixed (PRD §3): no 24h / 7d / 30d choice.
    expect(screen.queryAllByRole('button', { name: /^(24h|7d|30d)$/ })).toHaveLength(0);
    // No blended token breakdown, ranking, cache-savings or team cards.
    expect(screen.queryAllByRole('button', { name: /^(Agent|Thread|Model)$/ })).toHaveLength(0);
    expect(screen.queryAllByText(/where it went/i)).toHaveLength(0);
    expect(screen.queryAllByText(/top threads/i)).toHaveLength(0);
    expect(screen.queryAllByText(/cache saved|served from cache|from cache|of all reads/i)).toHaveLength(0);
    expect(screen.queryAllByText(/^by team$/i)).toHaveLength(0);
    // No guessed cross-model labels.
    expect(screen.queryAllByText(/\((mixed|cli-unreported|unknown —[^)]*)\)/)).toHaveLength(0);
  });
});

/* ================================================================== */
/*  B. Workload                                                          */
/* ================================================================== */

describe('Usage v1 — Workload', () => {
  it('renders the six PRD columns in order and exactly the response agents (no synthetic rows, no token values)', async () => {
    serve({
      workload: () => workload([wAgent('product_lead'), wAgent('build_agent', { current: wPeriod({ task_runs: 34 }) })]),
    });
    renderPage();

    const table = await screen.findByRole('table', { name: 'Workload by agent' });
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent ?? '');
    expect(headers.map((h) => h.replace(/\d+$/, '').replace(/\(.*\)/, '').trim())).toEqual([
      'Agent', 'Task runs', 'Thread wakes', 'Recorded runtime', 'Deliveries', 'Replies',
    ]);
    const rowHeaders = within(table).getAllByRole('rowheader').map((h) => h.textContent);
    expect(rowHeaders).toEqual(['product_lead', 'build_agent']);
    expect(within(table).queryByText(/tokens?/i)).toBeNull();
    expect(within(table).queryByText(/\d(\.\d)?[KM]\b/)).toBeNull();
  });

  it('labels Recorded runtime as agent runtime with coverage, and never renders missing runtime as zero', async () => {
    serve({
      workload: () =>
        workload([
          wAgent('product_lead', { current: wPeriod({ recorded_runtime: { seconds: 15000, known: 5, total: 6 } }) }),
          wAgent('qa_agent', { current: wPeriod({ recorded_runtime: { seconds: 0, known: 0, total: 4 } }) }),
        ]),
    });
    renderPage();

    const table = await screen.findByRole('table', { name: 'Workload by agent' });
    expect(within(table).getByRole('columnheader', { name: /Recorded runtime.*agent runtime/i })).toBeInTheDocument();
    const lead = await rowFor(table, 'product_lead');
    expect(within(lead).getByText('4h 10m')).toBeInTheDocument();
    expect(within(lead).getByText('recorded for 5 of 6 runs')).toBeInTheDocument();
    const qa = await rowFor(table, 'qa_agent');
    expect(within(qa).getByText('Not recorded')).toBeInTheDocument();
    expect(within(qa).getByText('recorded for 0 of 4 runs')).toBeInTheDocument();
    expect(within(qa).queryByText(/^0s$|^0m$|^0h/)).toBeNull();
    expect(
      screen.getByText(/this is not human working time/i),
    ).toBeInTheDocument();
  });

  it('shows Replies with recorded X of Y and names the unknown portion "reply outcome not recorded"', async () => {
    serve({
      workload: () =>
        workload([
          wAgent('product_lead', {
            current: wPeriod({ replies: 22, reply_outcome_coverage: { recorded: 22, total_consumed: 24 } }),
          }),
        ]),
    });
    renderPage();

    const row = await rowFor(await screen.findByRole('table', { name: 'Workload by agent' }), 'product_lead');
    expect(within(row).getByText('22')).toBeInTheDocument();
    expect(within(row).getByText('recorded 22 of 24')).toBeInTheDocument();
    expect(within(row).getByText('2 reply outcome not recorded')).toBeInTheDocument();
  });

  it('discloses delivery_unclassified_results in a footnote only when non-zero', async () => {
    serve({
      workload: () =>
        workload([
          wAgent('product_lead', { current: wPeriod({ delivery_unclassified_results: 3 }) }),
          wAgent('qa_agent'),
        ]),
    });
    const { unmount } = renderPage();
    expect(
      await screen.findByText(
        '3 completed results could not be classified as worker delivery and are not counted in Deliveries (product_lead 3).',
      ),
    ).toBeInTheDocument();
    unmount();

    serve({ workload: () => workload([wAgent('qa_agent')]) });
    renderPage();
    await screen.findByRole('table', { name: 'Workload by agent' });
    expect(screen.queryByText(/could not be classified as worker delivery/)).toBeNull();
  });

  it('states runtime coverage for the period below the table', async () => {
    serve({
      workload: () =>
        workload([
          wAgent('a', { current: wPeriod({ recorded_runtime: { seconds: 60, known: 5, total: 6 } }) }),
          wAgent('b', { current: wPeriod({ recorded_runtime: { seconds: 60, known: 2, total: 2 } }) }),
        ]),
    });
    renderPage();
    expect(
      await screen.findByText('Runtime recorded for 7 of 8 task runs and thread wakes in this period.'),
    ).toBeInTheDocument();
  });

  it('renders server deltas neutrally in native units, with the prior value in accessible text', async () => {
    serve({
      workload: (compare) =>
        workload(
          [
            wAgent('product_lead', {
              current: wPeriod({ task_runs: 9, thread_wakes: 62, deliveries: 2 }),
              previous: compare
                ? wPeriod({ task_runs: 7, thread_wakes: 63, deliveries: 2, recorded_runtime: { seconds: 12300, known: 4, total: 4 } })
                : null,
              deltas: compare
                ? {
                    task_runs: abs(2),
                    thread_wakes: abs(-1),
                    recorded_runtime_seconds: abs(2700),
                    deliveries: NO_CHANGE,
                    replies: abs(3),
                  }
                : null,
            }),
          ],
          compare,
        ),
    });
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: 'Compare with previous 7 days' }));

    const table = await screen.findByRole('table', { name: 'Workload by agent' });
    const row = await waitFor(async () => {
      const r = await rowFor(table, 'product_lead');
      expect(within(r).getByText('+2')).toBeInTheDocument();
      return r;
    });
    expect(within(row).getByText('−1')).toBeInTheDocument();
    expect(within(row).getByText('+45m')).toBeInTheDocument();
    expect(within(row).getByText('No change')).toBeInTheDocument();
    expect(within(row).getByText('previous 7 days: 7', { exact: false })).toBeInTheDocument();
    expect(within(row).getByText('before: recorded for 4 of 4 runs')).toBeInTheDocument();
    // Neutral styling: no good/bad colour on any delta.
    for (const el of within(row).getAllByText(/^(\+|−)/)) {
      expect(el.classList.contains('text-feedback-success')).toBe(false);
      expect(el.classList.contains('text-feedback-danger')).toBe(false);
    }
  });

  it('withholds the Replies delta with a dash, both coverages and a plain explanation (no raw reason code)', async () => {
    serve({
      workload: (compare) =>
        workload(
          [
            wAgent('product_lead', {
              current: wPeriod({ reply_outcome_coverage: { recorded: 22, total_consumed: 24 } }),
              previous: compare ? wPeriod({ reply_outcome_coverage: { recorded: 10, total_consumed: 10 } }) : null,
              deltas: compare
                ? {
                    task_runs: abs(1),
                    thread_wakes: abs(1),
                    recorded_runtime_seconds: abs(60),
                    deliveries: abs(1),
                    replies: withheld('reply_outcome_not_recorded'),
                  }
                : null,
            }),
          ],
          compare,
        ),
    });
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: 'Compare with previous 7 days' }));

    const row = await waitFor(async () => {
      const r = await rowFor(await screen.findByRole('table', { name: 'Workload by agent' }), 'product_lead');
      expect(within(r).getByText('recorded 22 of 24 now · 10 of 10 before')).toBeInTheDocument();
      return r;
    });
    expect(within(row).getByText('—')).toBeInTheDocument();
    expect(
      within(row).getByText('Comparison withheld: the reply outcome was not recorded for some wakes.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/reply_outcome_not_recorded/)).toBeNull();
  });

  it('empty Workload states that nothing started and shows column definitions without synthetic rows', async () => {
    serve({ workload: () => workload([]) });
    renderPage();

    expect(
      await screen.findByText('No task runs or thread wakes started in this period.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Workload by agent' })).toBeNull();
    const defs = screen.getByRole('list', { name: 'Workload column definitions' });
    for (const term of ['Task runs', 'Thread wakes', 'Recorded runtime', 'Deliveries', 'Replies']) {
      expect(within(defs).getByText(term)).toBeInTheDocument();
    }
  });
});

/* ================================================================== */
/*  C. Efficiency selection                                              */
/* ================================================================== */

describe('Usage v1 — Efficiency cohort selection', () => {
  it('renders no Efficiency data and presses no CLI on first load, and explains why there is no combined view', async () => {
    serve();
    renderPage();

    expect(
      await screen.findByText(
        'Choose one CLI. Its CLI default (not pinned) cohort is preselected and you can pick a named model. Token reporting differs by CLI and model, so there is no combined view.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Choose a CLI first')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: /Efficiency by run type/ })).toBeNull();
    const cliGroup = screen.getByRole('group', { name: 'CLI' });
    for (const pill of within(cliGroup).getAllByRole('button')) {
      expect(pill).toHaveAttribute('aria-pressed', 'false');
    }
    for (const call of getEfficiency.mock.calls) {
      expect(call[1]?.executor).toBeUndefined();
    }
  });

  it('offers distinct CLIs, then that CLI’s models with the unpinned cohort as its own option and no aggregate option', async () => {
    serve();
    renderPage();

    const cliGroup = await screen.findByRole('group', { name: 'CLI' });
    expect(within(cliGroup).getAllByRole('button').map((b) => b.textContent)).toEqual(['claude', 'codex']);

    fireEvent.click(within(cliGroup).getByRole('button', { name: 'claude' }));
    const modelGroup = screen.getByRole('group', { name: 'Model' });
    expect(within(modelGroup).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'CLI default (not pinned)', 'opus', 'sonnet',
    ]);
    expect(within(modelGroup).queryByRole('button', { name: /all/i })).toBeNull();
    expect(within(cliGroup).getByRole('button', { name: 'claude' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('selecting the unpinned cohort requests model_unpinned=true without a model; a named model requests that model', async () => {
    serve();
    renderPage();

    await selectCohort('claude', 'CLI default (not pinned)');
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'claude', model_unpinned: true }),
    );
    expect(await efficiencyTable()).toHaveAccessibleName('Efficiency by run type for claude · CLI default (not pinned)');

    fireEvent.click(screen.getByRole('button', { name: 'sonnet' }));
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'claude', model: 'sonnet' }),
    );
  });

  it('switching CLI preselects the new CLI’s unpinned cohort instead of keeping the old model', async () => {
    serve();
    renderPage();
    await selectCohort('claude', 'sonnet');
    await efficiencyTable();

    fireEvent.click(screen.getByRole('button', { name: 'codex' }));
    const modelGroup = screen.getByRole('group', { name: 'Model' });
    expect(within(modelGroup).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'CLI default (not pinned)', 'gpt-5',
    ]);
    expect(within(modelGroup).getByRole('button', { name: 'CLI default (not pinned)' })).toHaveAttribute('aria-pressed', 'true');
    expect(await efficiencyTable()).toHaveAccessibleName('Efficiency by run type for codex · CLI default (not pinned)');
    expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'codex', model_unpinned: true });
  });
});

/* ================================================================== */
/*  C2. Efficiency default model (THR-272 seq109 ruling)                 */
/* ================================================================== */

describe('Usage v1 — Efficiency default model', () => {
  const UNPINNED = 'CLI default (not pinned)';

  function cohort(executor: string, model: string | null, current_runs: number, previous_runs = 0): CohortOption {
    return { executor, model, model_unpinned: model === null, current_runs, previous_runs };
  }

  function modelPill(name: string) {
    return within(screen.getByRole('group', { name: 'Model' })).getByRole('button', { name });
  }

  function modelPills() {
    return within(screen.getByRole('group', { name: 'Model' }))
      .getAllByRole('button')
      .map((b) => b.textContent);
  }

  function pressedModels() {
    return within(screen.getByRole('group', { name: 'Model' }))
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-pressed') === 'true')
      .map((b) => b.textContent);
  }

  function efficiencyStatus() {
    return within(screen.getByRole('region', { name: 'Efficiency' })).getByRole('status');
  }

  function zeroRunRows() {
    return RUN_TYPES.map((t) =>
      eRow(t, {
        current: ePeriod({
          runs: 0,
          usage_coverage: { known: 0, total: 0, ratio: null },
          fresh_input: metric(null, 0),
          reread: metric(null, 0),
          output: metric(null, 0),
          decline_waste:
            t === 'thread_reply' || t === 'thread_followup' ? { ...NO_DECLINES, total: 0, rate: null } : null,
        }),
      }),
    );
  }

  it('first load selects nothing: no CLI or model pressed and no cohort requested', async () => {
    serve();
    renderPage();

    expect(await screen.findByText('Choose a CLI first')).toBeInTheDocument();
    for (const pill of within(screen.getByRole('group', { name: 'CLI' })).getAllByRole('button')) {
      expect(pill).toHaveAttribute('aria-pressed', 'false');
    }
    expect(within(screen.getByRole('group', { name: 'Model' })).queryAllByRole('button')).toEqual([]);
    expect(screen.queryByRole('table', { name: /Efficiency by run type/ })).toBeNull();
    expect(efficiencyStatus()).toHaveTextContent('Choose a CLI to see Efficiency');
    for (const call of getEfficiency.mock.calls) {
      expect(call[1]?.executor).toBeUndefined();
    }
  });

  it('choosing a CLI preselects its unpinned cohort even when a named model has more runs', async () => {
    serve(); // claude: unpinned 3 runs, opus 5, sonnet 40
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'claude' }));

    expect(pressedModels()).toEqual([UNPINNED]);
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'claude', model_unpinned: true }),
    );
    expect(await efficiencyTable()).toHaveAccessibleName(`Efficiency by run type for claude · ${UNPINNED}`);
    expect(efficiencyStatus()).toHaveTextContent(`Efficiency loaded for claude · ${UNPINNED}`);
    for (const call of getEfficiency.mock.calls) {
      expect(call[1]?.model).toBeUndefined();
    }
  });

  it('changing CLI resets to the new CLI’s unpinned cohort and does not carry the picked model', async () => {
    serve({
      options: (compare) =>
        efficiency(
          { cohorts: [cohort('claude', null, 3), cohort('claude', 'sonnet', 40), cohort('codex', null, 2), cohort('codex', 'sonnet', 9)] },
          compare,
        ),
    });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'claude' }));
    fireEvent.click(modelPill('sonnet'));
    expect(pressedModels()).toEqual(['sonnet']);

    fireEvent.click(screen.getByRole('button', { name: 'codex' }));

    expect(pressedModels()).toEqual([UNPINNED]);
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'codex', model_unpinned: true }),
    );
    expect(getEfficiency).not.toHaveBeenCalledWith(SLUG, { compare: false, executor: 'codex', model: 'sonnet' });
    expect(await efficiencyTable()).toHaveAccessibleName(`Efficiency by run type for codex · ${UNPINNED}`);
  });

  it('a CLI with no unpinned cohort row still offers and presses the unpinned pill and shows its zero-run rows', async () => {
    serve({
      // codex has no model=null cohort in this window.
      selected: (params) =>
        params.model_unpinned ? efficiency({ rows: zeroRunRows() }) : efficiency({ rows: fullRows() }),
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'codex' }));

    expect(modelPills()).toEqual([UNPINNED, 'gpt-5']);
    expect(pressedModels()).toEqual([UNPINNED]);
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'codex', model_unpinned: true }),
    );
    const table = await efficiencyTable();
    expect(table).toHaveAccessibleName(`Efficiency by run type for codex · ${UNPINNED}`);
    for (const [, label] of [
      ['worker_task', 'Worker task'], ['manager_decision', 'Manager decision'], ['thread_reply', 'Thread reply'],
      ['thread_followup', 'Thread follow-up'], ['dream', 'Dream'],
    ]) {
      const row = await rowFor(table, label);
      expect(within(row).getByText('No runs in this period')).toBeInTheDocument();
      expect(within(row).getAllByRole('cell')[0]).toHaveTextContent(/^0$/);
    }
    expect(getEfficiency).not.toHaveBeenCalledWith(SLUG, { compare: false, executor: 'codex', model: 'gpt-5' });

    // The synthesized pill belongs only to the chosen CLI; there is never an aggregate option.
    expect(within(screen.getByRole('group', { name: 'Model' })).queryByRole('button', { name: /all/i })).toBeNull();
    expect(within(screen.getByRole('group', { name: 'CLI' })).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'claude', 'codex',
    ]);
  });

  it('a manual named-model pick persists across the options refetch caused by a Compare toggle', async () => {
    serve();
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'claude' }));
    expect(pressedModels()).toEqual([UNPINNED]);
    fireEvent.click(modelPill('opus'));
    expect(pressedModels()).toEqual(['opus']);
    expect(await efficiencyTable()).toHaveAccessibleName('Efficiency by run type for claude · opus');

    fireEvent.click(screen.getByRole('switch', { name: 'Compare with previous 7 days' }));
    await waitFor(() => expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: true }));
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: true, executor: 'claude', model: 'opus' }),
    );

    expect(pressedModels()).toEqual(['opus']);
    expect(await efficiencyTable()).toHaveAccessibleName('Efficiency by run type for claude · opus');
    expect(getEfficiency).not.toHaveBeenCalledWith(SLUG, { compare: true, executor: 'claude', model_unpinned: true });
  });

  it('turning Compare off falls a removed previous-only named model back to the unpinned cohort, not a top model', async () => {
    serve({
      options: (compare) =>
        efficiency(
          {
            // No model=null row in either window: the fallback must still be the unpinned cohort.
            cohorts: compare
              ? [cohort('claude', 'haiku', 0, 9), cohort('claude', 'opus', 5, 2), cohort('claude', 'sonnet', 40, 30)]
              : [cohort('claude', 'opus', 5), cohort('claude', 'sonnet', 40)],
          },
          compare,
        ),
    });
    renderPage();
    const compareSwitch = await screen.findByRole('switch', { name: 'Compare with previous 7 days' });
    fireEvent.click(compareSwitch);
    await waitFor(() => expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: true }));

    fireEvent.click(await screen.findByRole('button', { name: 'claude' }));
    fireEvent.click(await waitFor(() => modelPill('haiku')));
    expect(pressedModels()).toEqual(['haiku']);

    fireEvent.click(compareSwitch);

    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Model' })).queryByRole('button', { name: 'haiku' })).toBeNull());
    expect(pressedModels()).toEqual([UNPINNED]);
    await waitFor(() =>
      expect(getEfficiency).toHaveBeenCalledWith(SLUG, { compare: false, executor: 'claude', model_unpinned: true }),
    );
    expect(await efficiencyTable()).toHaveAccessibleName(`Efficiency by run type for claude · ${UNPINNED}`);
    expect(getEfficiency).not.toHaveBeenCalledWith(SLUG, { compare: false, executor: 'claude', model: 'sonnet' });
    expect(screen.queryByText(/Choose one CLI/)).toBeNull();
  });

  it('turning Compare off clears both choices when the chosen CLI itself is no longer offered', async () => {
    serve({
      options: (compare) =>
        efficiency(
          {
            cohorts: compare
              ? [cohort('claude', 'sonnet', 40, 30), cohort('gemini', 'pro', 0, 4)]
              : [cohort('claude', 'sonnet', 40)],
          },
          compare,
        ),
    });
    renderPage();
    const compareSwitch = await screen.findByRole('switch', { name: 'Compare with previous 7 days' });
    fireEvent.click(compareSwitch);

    fireEvent.click(await screen.findByRole('button', { name: 'gemini' }));
    expect(pressedModels()).toEqual([UNPINNED]);
    fireEvent.click(modelPill('pro'));
    expect(pressedModels()).toEqual(['pro']);

    fireEvent.click(compareSwitch);

    expect(await screen.findByText('Choose a CLI first')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'gemini' })).toBeNull();
    for (const pill of within(screen.getByRole('group', { name: 'CLI' })).getAllByRole('button')) {
      expect(pill).toHaveAttribute('aria-pressed', 'false');
    }
    expect(screen.queryByRole('table', { name: /Efficiency by run type/ })).toBeNull();
    expect(efficiencyStatus()).toHaveTextContent('Choose a CLI to see Efficiency');
  });
});

/* ================================================================== */
/*  D. Efficiency rows                                                   */
/* ================================================================== */

describe('Usage v1 — Efficiency rows', () => {
  it('always renders the five run types in the fixed PRD order with the six PRD columns', async () => {
    serve({ selected: () => efficiency({ rows: [...fullRows()].reverse() }) });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const table = await efficiencyTable();
    expect(within(table).getAllByRole('rowheader').map((h) => h.firstChild?.textContent)).toEqual([
      'Worker task', 'Manager decision', 'Thread reply', 'Thread follow-up', 'Dream',
    ]);
    const headers = within(table).getAllByRole('columnheader').map((h) => (h.textContent ?? '').replace(/\d+$/, '').trim());
    expect(headers).toEqual([
      'Run type', 'Runs', 'Median fresh input', 'Median re-read', 'Median output', 'Decline waste',
    ]);
  });

  it('renders a genuine 0 as 0 and a null class as "Not reported" — never 0 — with per-row usage coverage', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            worker_task: {
              current: ePeriod({
                runs: 20,
                usage_coverage: { known: 18, total: 20, ratio: 0.9 },
                fresh_input: metric(0, 18),
                reread: metric(null, 0),
                output: metric(4100, 18),
              }),
            },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const row = await rowFor(await efficiencyTable(), 'Worker task');
    const cells = within(row).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent(/^0$/);
    expect(within(cells[2]).getByText('Not reported')).toBeInTheDocument();
    expect(within(cells[2]).queryByText('0')).toBeNull();
    expect(within(row).getByText('Usage known for 18 of 20 runs')).toBeInTheDocument();
  });

  it('lifecycle runs without any usage keep Runs and show Unknown token metrics, never zero', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            dream: {
              current: ePeriod({
                runs: 4,
                usage_coverage: { known: 0, total: 4, ratio: 0 },
                fresh_input: metric(null, 0),
                reread: metric(null, 0),
                output: metric(null, 0),
              }),
            },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const row = await rowFor(await efficiencyTable(), 'Dream');
    const cells = within(row).getAllByRole('cell');
    expect(cells[0]).toHaveTextContent(/^4/);
    expect(within(row).getAllByText('Unknown')).toHaveLength(3);
    expect(within(row).getByText('Usage known for 0 of 4 runs')).toBeInTheDocument();
  });

  it('labels a partial Fresh input median with its class-specific denominator and never as a complete median', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            worker_task: {
              current: ePeriod({
                runs: 20,
                usage_coverage: { known: 20, total: 20, ratio: 1 },
                fresh_input: metric(12000, 18, 2),
                reread: metric(54000, 17),
              }),
            },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const row = await rowFor(await efficiencyTable(), 'Worker task');
    const [, fresh, reread] = within(row).getAllByRole('cell');
    expect(within(fresh).getByText('Partial')).toBeInTheDocument();
    expect(within(fresh).getByText('median of 18 of 20 runs; cache write not reported for 2')).toBeInTheDocument();
    expect(within(reread).getByText('17 of 20 class-reported')).toBeInTheDocument();
    expect(within(reread).queryByText('Partial')).toBeNull();
  });

  it('a selected cohort with no runs shows "No runs in this period" and em dashes, not token zeros', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            manager_decision: {
              current: ePeriod({
                runs: 0,
                usage_coverage: { known: 0, total: 0, ratio: null },
                fresh_input: metric(null, 0),
                reread: metric(null, 0),
                output: metric(null, 0),
              }),
            },
            thread_followup: {
              current: ePeriod({
                runs: 0,
                usage_coverage: { known: 0, total: 0, ratio: null },
                fresh_input: metric(null, 0),
                reread: metric(null, 0),
                output: metric(null, 0),
                decline_waste: { ...NO_DECLINES, total: 0, rate: null },
              }),
            },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');
    const table = await efficiencyTable();

    for (const label of ['Manager decision', 'Thread follow-up']) {
      const row = await rowFor(table, label);
      expect(within(row).getByText('No runs in this period')).toBeInTheDocument();
      const cells = within(row).getAllByRole('cell');
      expect(cells[0]).toHaveTextContent(/^0$/);
      for (const cell of cells.slice(1, 4)) expect(cell).toHaveTextContent(/^—$/);
      expect(within(row).queryByText(/no declines/)).toBeNull();
    }
  });

  it('decline waste: rate plus separately labelled known totals, "0% · no declines", and all-unknown declines', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            thread_reply: { current: ePeriod({ runs: 96, decline_waste: declines() }) },
            thread_followup: { current: ePeriod({ runs: 8, decline_waste: NO_DECLINES }) },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');
    const table = await efficiencyTable();

    const reply = within(await rowFor(table, 'Thread reply')).getAllByRole('cell')[4];
    expect(within(reply).getByText('35%')).toBeInTheDocument();
    expect(within(reply).getByText('34 of 96 declined')).toBeInTheDocument();
    expect(within(reply).getByText('Known totals for declined wakes')).toBeInTheDocument();
    expect(within(reply).getByText('Fresh input').nextSibling).toHaveTextContent('56.0K');
    expect(within(reply).getByText('Re-read').nextSibling).toHaveTextContent('603.0K');
    expect(within(reply).getByText('Output').nextSibling).toHaveTextContent('18.0K');
    expect(within(reply).getByText('usage known for 31 of 34 declined wakes')).toBeInTheDocument();

    const followup = within(await rowFor(table, 'Thread follow-up')).getAllByRole('cell')[4];
    expect(followup).toHaveTextContent(/^0% · no declines$/);

    for (const label of ['Worker task', 'Manager decision', 'Dream']) {
      expect(within(await rowFor(table, label)).getAllByRole('cell')[4]).toHaveTextContent(/^$/);
    }
  });

  it('declines whose usage is entirely unknown show the rate and "usage unknown for all declined wakes"', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows({
            thread_reply: {
              current: ePeriod({
                runs: 10,
                decline_waste: declines({
                  declined: 4, total: 10, rate: 0.4, usage_known: 0,
                  fresh_input: { value: null, n_reported: 0 },
                  reread: { value: null, n_reported: 0 },
                  output: { value: null, n_reported: 0 },
                }),
              }),
            },
          }),
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const cell = within(await rowFor(await efficiencyTable(), 'Thread reply')).getAllByRole('cell')[4];
    expect(within(cell).getByText('40%')).toBeInTheDocument();
    expect(within(cell).getByText('usage unknown for all declined wakes')).toBeInTheDocument();
    expect(within(cell).queryByText('Known totals for declined wakes')).toBeNull();
  });

  it('shows unattributed lifecycle counts outside the five rows, and the usage-known sentence', async () => {
    serve({
      selected: () =>
        efficiency({
          rows: fullRows(),
          unattributed: {
            current: unattributed({ worker_task: 1, thread_reply: 2, task_unclassified: 1, recovery: 2 }),
            previous: null,
          },
        }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');
    await efficiencyTable();

    const block = screen.getByRole('list', { name: 'Unattributed lifecycle runs this period' });
    expect(within(block).getByText('Worker task (no CLI/model on record): 1')).toBeInTheDocument();
    expect(within(block).getByText('Thread reply (no CLI/model on record): 2')).toBeInTheDocument();
    expect(within(block).getByText('Task sessions without a run type: 1')).toBeInTheDocument();
    expect(within(block).getByText('Completion-recovery sessions: 2')).toBeInTheDocument();
    expect(
      screen.getByText('6 lifecycle runs in this period are outside every cohort and are not counted in the rows above.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Usage known for 100 of 100 runs in this period.')).toBeInTheDocument();
  });
});

/* ================================================================== */
/*  E. Efficiency compare                                                */
/* ================================================================== */

describe('Usage v1 — Efficiency comparison', () => {
  function compareRows() {
    return fullRows({
      worker_task: {
        current: ePeriod({ runs: 24 }),
        previous: ePeriod({ runs: 21, fresh_input: metric(13000, 21) }),
        deltas: {
          runs: abs(3),
          fresh_input: pct(-7.6923),
          reread: NEW_FROM_ZERO(54000),
          output: NO_CHANGE,
        },
      },
      thread_reply: {
        current: ePeriod({ runs: 20, usage_coverage: { known: 18, total: 20, ratio: 0.9 }, decline_waste: declines() }),
        previous: ePeriod({ runs: 12, usage_coverage: { known: 10, total: 12, ratio: 0.833 }, decline_waste: declines() }),
        deltas: Object.fromEntries(
          ['runs', 'fresh_input', 'reread', 'output', 'decline_rate', 'decline_fresh_input', 'decline_reread', 'decline_output']
            .map((k) => [k, withheld('usage_coverage_below_95_percent')]),
        ),
      },
      thread_followup: {
        current: ePeriod({ runs: 8, decline_waste: declines({ declined: 1, total: 8, rate: 0.125 }) }),
        previous: ePeriod({ runs: 8, decline_waste: declines({ declined: 2, total: 8, rate: 0.25 }) }),
        deltas: {
          // The daemon's Efficiency count delta for equal non-zero runs.
          runs: { kind: 'absolute', value: 0, withheld_reason: null },
          fresh_input: withheld('invalid_baseline'),
          reread: pct(14.2),
          output: pct(0.4),
          decline_rate: pct(-50),
          decline_fresh_input: pct(10),
          decline_reread: pct(-3),
          decline_output: NO_CHANGE,
        },
      },
    }).map((r) =>
      r.previous
        ? r
        : {
            ...r,
            previous: ePeriod({ decline_waste: r.current.decline_waste }),
            deltas: { runs: NO_CHANGE, fresh_input: NO_CHANGE, reread: NO_CHANGE, output: NO_CHANGE },
          },
    );
  }

  async function renderCompared() {
    serve({
      selected: (params) =>
        params.compare
          ? efficiency({ rows: compareRows() }, true)
          : efficiency({ rows: fullRows() }),
    });
    renderPage();
    await selectCohort('claude', 'sonnet');
    await efficiencyTable();
    fireEvent.click(screen.getByRole('switch', { name: 'Compare with previous 7 days' }));
    const table = await efficiencyTable();
    await within(table).findByText('+3');
    return table;
  }

  it('renders each server delta kind: count, percent, New from 0, No change — never NaN or infinity', async () => {
    const table = await renderCompared();
    const row = await rowFor(table, 'Worker task');
    expect(within(row).getByText('+3')).toBeInTheDocument();
    expect(within(row).getByText('−7.7%')).toBeInTheDocument();
    expect(within(row).getByText('New from 0')).toBeInTheDocument();
    expect(within(row).getByText('No change')).toBeInTheDocument();
    expect(within(row).getByText('previous 7 days: 13.0K', { exact: false })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/NaN|Infinity|∞/);
  });

  it('a row below 95% coverage withholds every delta: dashes, both coverages, plain explanation, no active delta (P0)', async () => {
    const table = await renderCompared();
    const row = await rowFor(table, 'Thread reply');

    expect(
      within(row).getByText('Comparison withheld: usage is known for under 95% of runs in one of the periods.'),
    ).toBeInTheDocument();
    expect(within(row).getByText('Usage known for 18 of 20 runs now and 10 of 12 in the previous 7 days')).toBeInTheDocument();
    expect(within(row).queryByText(/^(\+|−)\d/)).toBeNull();
    expect(within(row).queryByText('No change')).toBeNull();
    expect(within(row).queryByText('New from 0')).toBeNull();
    const cells = within(row).getAllByRole('cell');
    for (const cell of cells) {
      expect(within(cell).getAllByText('—', { selector: '[data-delta="withheld"]' }).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText(/usage_coverage_below_95_percent/)).toBeNull();
  });

  it('a per-metric withheld baseline and the decline measures render from the server deltas', async () => {
    const table = await renderCompared();
    const row = await rowFor(table, 'Thread follow-up');
    const [runs, fresh, , , decline] = within(row).getAllByRole('cell');

    expect(within(runs).getByText('No change')).toBeInTheDocument();
    expect(within(runs).queryByText('+0')).toBeNull();

    expect(within(fresh).getByText('—')).toBeInTheDocument();
    expect(
      within(fresh).getByText('Comparison withheld: there is no comparable value in one of the periods.'),
    ).toBeInTheDocument();
    expect(within(fresh).getByText('usage known 20 of 20 now · 20 of 20 before')).toBeInTheDocument();
    expect(screen.queryByText(/invalid_baseline/)).toBeNull();

    expect(within(row).getByText('+14%')).toBeInTheDocument();
    expect(within(row).getByText('+0.4%')).toBeInTheDocument();
    expect(within(decline).getByText('−50%')).toBeInTheDocument();
    expect(within(decline).getByText('+10%')).toBeInTheDocument();
    expect(within(decline).getByText('−3%')).toBeInTheDocument();
    expect(within(decline).getByText('No change')).toBeInTheDocument();
  });
});

/* ================================================================== */
/*  F. Required §9 states                                                */
/* ================================================================== */

describe('Usage v1 — loading, error, stale', () => {
  it('shows table-shaped loading skeletons without flashing zeros or sample values', async () => {
    getWorkload.mockImplementation(() => new Promise(() => {}));
    getEfficiency.mockImplementation(() => new Promise(() => {}));
    renderPage();

    const section = await screen.findByRole('region', { name: 'Workload' });
    // Located by its assistive-technology busy state, not a test id.
    const busy = section.querySelectorAll('[aria-busy="true"]');
    expect(busy).toHaveLength(1);
    const skeleton = busy[0] as HTMLElement;
    expect(within(skeleton).getByText('Recorded runtime')).toBeInTheDocument();
    expect(skeleton.textContent ?? '').not.toMatch(/\d/);
    expect(within(section).getByRole('status')).toHaveTextContent('Loading workload');
  });

  it('a failed Workload names the view, keeps the controls and Efficiency, and retries', async () => {
    let fail = true;
    serve();
    getWorkload.mockImplementation(async () => {
      if (fail) throw new Error('boom');
      return workload([wAgent('product_lead')]);
    });
    renderPage();

    const section = await screen.findByRole('region', { name: 'Workload' });
    expect(await within(section).findByText('Couldn’t load Workload.')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Compare with previous 7 days' })).toBeInTheDocument();
    expect(await screen.findByRole('group', { name: 'CLI' })).toBeInTheDocument();
    expect(within(section).queryByRole('table')).toBeNull();

    fail = false;
    fireEvent.click(within(section).getByRole('button', { name: 'Retry' }));
    expect(await within(section).findByRole('table', { name: 'Workload by agent' })).toBeInTheDocument();
  });

  it('a failed Efficiency cohort read names the view and keeps controls', async () => {
    serve({ selected: () => Promise.reject(new Error('boom')) });
    renderPage();
    await selectCohort('claude', 'sonnet');

    const section = screen.getByRole('region', { name: 'Efficiency' });
    expect(await within(section).findByText('Couldn’t load Efficiency.')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(within(section).getByRole('group', { name: 'CLI' })).toBeInTheDocument();
  });

  it('keeps earlier figures visibly labelled stale with Data through when the latest refetch fails', async () => {
    serve();
    const { client } = renderPage();
    const table = await screen.findByRole('table', { name: 'Workload by agent' });
    expect(within(table).getByText('product_lead')).toBeInTheDocument();

    getWorkload.mockRejectedValue(new Error('refresh failed'));
    await act(async () => {
      await client.refetchQueries({ queryKey: ['usage', SLUG, 'workload'] });
    });

    const section = screen.getByRole('region', { name: 'Workload' });
    expect(
      await within(section).findByText('Stale: showing figures from an earlier load. The latest refresh failed.'),
    ).toBeInTheDocument();
    expect(within(section).getByText('Data through Sep 29, 14:03 (Asia/Shanghai)')).toBeInTheDocument();
    expect(within(section).getByText('product_lead')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

/* ================================================================== */
/*  G. Narrow viewport + accessibility                                   */
/* ================================================================== */

describe('Usage v1 — narrow viewport and accessibility', () => {
  it('tables sit in a labelled horizontal scroll region with a frozen identity column and a visible scroll hint', async () => {
    serve();
    renderPage();
    await selectCohort('claude', 'sonnet');
    await efficiencyTable();

    for (const [name, table] of [
      ['Workload table', screen.getByRole('table', { name: 'Workload by agent' })],
      ['Efficiency table', await efficiencyTable()],
    ] as const) {
      const region = screen.getByRole('region', { name: `${name}, scrolls sideways` });
      expect(region.contains(table)).toBe(true);
      expect(region.classList.contains('overflow-x-auto')).toBe(true);
      expect(region).toHaveAttribute('tabindex', '0');
      for (const header of within(table).getAllByRole('rowheader')) {
        expect(header.classList.contains('sticky')).toBe(true);
      }
    }
    expect(screen.getAllByText('Scroll sideways to see every column.')).toHaveLength(2);
  });

  it('exposes polite live status for each section', async () => {
    serve();
    renderPage();
    await screen.findByRole('table', { name: 'Workload by agent' });

    const workloadStatus = within(screen.getByRole('region', { name: 'Workload' })).getByRole('status');
    expect(workloadStatus).toHaveAttribute('aria-live', 'polite');
    expect(workloadStatus).toHaveTextContent('Workload loaded for 1 agent');
    const efficiencyStatus = within(screen.getByRole('region', { name: 'Efficiency' })).getByRole('status');
    expect(efficiencyStatus).toHaveTextContent('Choose a CLI to see Efficiency');
  });
});

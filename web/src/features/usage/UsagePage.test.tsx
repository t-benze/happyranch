import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the usage hooks so the page renders deterministically
vi.mock('@/hooks/usage', () => ({
  useUsageByAgent: vi.fn(),
  useUsageByThread: vi.fn(),
  useUsageByModel: vi.fn(),
}));

// Mock the agents roster hook — SPEND-03 joins per-agent burn to each agent's
// `team` (carried on the LIST payload) to build the by-team breakdown card.
vi.mock('@/hooks/agents', () => ({
  useAgentsList: vi.fn(),
}));

import { useUsageByAgent, useUsageByThread, useUsageByModel } from '@/hooks/usage';
import { useAgentsList } from '@/hooks/agents';
import { UsagePage, fmtDateRange } from './UsagePage';

// Stub URL.createObjectURL for CSV export tests
const createObjectURL = vi.fn(() => 'blob:stub');
URL.createObjectURL = createObjectURL;

const mockAgentQ = vi.mocked(useUsageByAgent);
const mockThreadQ = vi.mocked(useUsageByThread);
const mockModelQ = vi.mocked(useUsageByModel);
const mockAgentsList = vi.mocked(useAgentsList);

function loaded<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function loading() {
  return { data: undefined, isLoading: true, isError: false, error: null };
}

function errored() {
  return { data: undefined, isLoading: false, isError: true, error: new Error('fail') };
}

// Roster-list query shape: { agents: AgentSummary[] } where each agent carries
// a per-agent `team`. Helper wraps an agents array into a loaded query.
function agentsLoaded(agents: { name: string; team: string | null }[]) {
  return loaded({ agents });
}

describe('UsagePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: empty roster so the by-team card folds every burning agent into
    // the honest 'unattributed' bucket unless a test supplies a roster.
    mockAgentsList.mockReturnValue(agentsLoaded([]) as ReturnType<typeof useAgentsList>);
  });

  afterEach(() => {
    // Some SPEND-04 eyebrow tests pin the clock with fake timers; restore real
    // timers unconditionally so later tests use the wall clock.
    vi.useRealTimers();
  });

  it('renders the header with window toggles', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('Usage')).toBeDefined();
    expect(screen.getByText('Token usage and cache savings')).toBeDefined();
    expect(screen.getByRole('button', { name: '24h' })).toBeDefined();
    expect(screen.getByRole('button', { name: '7d' })).toBeDefined();
    expect(screen.getByRole('button', { name: '30d' })).toBeDefined();
    // Pasture: window toggle uses rounded-full (pill) buttons
    const btn24h = screen.getByRole('button', { name: '24h' });
    expect(btn24h.className).toContain('rounded-full');
  });

  it('shows loading skeletons when data is loading', () => {
    mockAgentQ.mockReturnValue(loading());
    mockThreadQ.mockReturnValue(loading());
    mockModelQ.mockReturnValue(loading());

    render(<UsagePage />);

    // Loading state: the hero card should show the animate-pulse skeleton
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('shows empty state when no token usage data', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const empties = screen.getAllByText('No token usage in this window');
    expect(empties.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('$0.00 · not metered')).toBeDefined();
    // Pasture: hero empty state uses font-display
    const heroZero = screen.getByText('0');
    expect(heroZero.className).toContain('font-display');
  });

  it('shows error state when a query fails', () => {
    mockAgentQ.mockReturnValue(errored());
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText(/Couldn't load usage data/)).toBeDefined();
  });

  it('renders hero totals from agent rollup', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 2000,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Hero should show total churn = 1600
    const churns = screen.getAllByText('1.6K');
    expect(churns.length).toBeGreaterThanOrEqual(1);
    // Pasture: hero burn numeral uses font-display
    const heroNum = churns[0]!;
    expect(heroNum.className).toContain('font-display');
    // Cache savings = 2000 (shown as "From cache" line)
    const caches = screen.getAllByText('2.0K');
    expect(caches.length).toBeGreaterThanOrEqual(1);
    // THR-099 display-metric contract: the compacted visible text keeps the
    // EXACT value available via title (never lost). Hero + Fresh both expose
    // churn 1600 → '1,600'; From-cache tile + cache-saved callout both expose
    // cacheRead 2000 → '2,000'; the Detail line exposes both raw in/out.
    expect(screen.getAllByTitle('1,600').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByTitle('2,000').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTitle('in 1,000 / out 500')).toBeDefined();
    // "not metered" caption
    expect(screen.getByText('$0.00 · not metered')).toBeDefined();
    // Pasture: section label uses text-text-secondary + uppercase tracking.
    // SPEND-04: the eyebrow keeps 'Token burn · {windowLabel}' and now appends
    // the actual [since..now] date range, so match the window-label prefix.
    expect(screen.getByText(/^Token burn · 7d · /)).toBeDefined();
  });

  it('renders breakdown table with agent data', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 200,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
        },
        {
          agent: 'qa_engineer',
          sessions: 1,
          input_tokens: 200,
          output_tokens: 80,
          cache_read_tokens: 50,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 280,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Agent names in the breakdown table
    expect(screen.getByText('dev_agent')).toBeDefined();
    expect(screen.getByText('qa_engineer')).toBeDefined();
    // Segmentation toggles — Pasture: rounded-full pill buttons
    expect(screen.getByRole('button', { name: 'Agent' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Thread' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Model' })).toBeDefined();
    const agentBtn = screen.getByRole('button', { name: 'Agent' });
    expect(agentBtn.className).toContain('rounded-full');
  });

  it('shows top threads table when thread data is available', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-001',
          sessions: 5,
          input_tokens: 5000,
          output_tokens: 2000,
          cache_read_tokens: 3000,
          cache_creation_tokens: 0,
          reasoning_tokens: 500,
          total_tokens: 7500,
          model_any: 'claude-sonnet-4-5[1m]',
          non_null_sessions: 5,
          model_distinct: 1,
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('Top threads by burn')).toBeDefined();
    expect(screen.getByText('THR-001')).toBeDefined();
    // classifyModel renders the observed model id verbatim when uniform
    expect(screen.getByText('claude-sonnet-4-5[1m]')).toBeDefined();
  });

  // ---- classifyModel label cases in Top Threads ----

  it('renders (mixed) label when >1 distinct model on a thread', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-002',
          sessions: 4,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
          non_null_sessions: 4,
          model_distinct: 2,
          model_any: 'claude-opus-4-8[1m]',
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('(mixed)')).toBeDefined();
  });

  it('renders (cli-unreported) label for codex NULL-model rows', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-003',
          sessions: 3,
          input_tokens: 500,
          output_tokens: 200,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 700,
          non_null_sessions: 0,
          null_codex_sessions: 3,
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('(cli-unreported)')).toBeDefined();
  });

  it('renders (unknown — pre-fix) label for pre-cutover claude NULL-model rows', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-004',
          sessions: 2,
          input_tokens: 300,
          output_tokens: 100,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 400,
          non_null_sessions: 0,
          null_claude_sessions: 2,
          null_claude_max_created_at: '2026-06-10T09:00:00+00:00',
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('(unknown — pre-fix)')).toBeDefined();
  });

  it('renders (unknown — ANOMALY) label for post-cutover claude NULL-model rows', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-005',
          sessions: 1,
          input_tokens: 200,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 250,
          non_null_sessions: 0,
          null_claude_sessions: 1,
          null_claude_max_created_at: '2026-06-13T00:00:00+00:00',
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('(unknown — ANOMALY)')).toBeDefined();
  });

  // ---- Keyboard navigation (ArrowLeft/ArrowRight roving-focus) ----

  it('supports ArrowRight roving-focus on the window toggle group', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const btns = screen.getAllByRole('button', { name: /24h|7d|30d/ });
    expect(btns.length).toBe(3);

    // Focus first button
    btns[0]!.focus();
    expect(document.activeElement).toBe(btns[0]);

    // ArrowRight -> move to second button
    fireEvent.keyDown(btns[0]!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(btns[1]);

    // ArrowRight -> move to third button
    fireEvent.keyDown(btns[1]!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(btns[2]);
  });

  it('supports ArrowLeft roving-focus on the window toggle group', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const btns = screen.getAllByRole('button', { name: /24h|7d|30d/ });

    // Focus last button
    btns[2]!.focus();
    expect(document.activeElement).toBe(btns[2]);

    // ArrowLeft -> move to second button
    fireEvent.keyDown(btns[2]!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(btns[1]);

    // ArrowLeft -> move to first button
    fireEvent.keyDown(btns[1]!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(btns[0]);
  });

  // ---- CSV Export ----

  it('renders Export button when data is loaded', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 1,
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 25,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 150,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('Export')).toBeDefined();
  });

  it('does not render Export button when there is no data', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Export button should not be present when there's nothing to export
    expect(screen.queryByText('Export')).toBeNull();
  });

  it('Export button triggers CSV download with correct filename', async () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 2,
          input_tokens: 200,
          output_tokens: 100,
          cache_read_tokens: 50,
          cache_creation_tokens: 0,
          reasoning_tokens: 10,
          total_tokens: 310,
        },
        {
          agent: 'qa_engineer',
          sessions: 1,
          input_tokens: 80,
          output_tokens: 40,
          cache_read_tokens: 5,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 120,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const exportBtn = screen.getByText('Export');
    expect(exportBtn).toBeDefined();

    // reset spy
    createObjectURL.mockClear();

    fireEvent.click(exportBtn);

    // Should create a blob URL
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const callArgs = createObjectURL.mock.calls[0] as unknown as [Blob] | undefined;
    expect(callArgs).toBeDefined();
    const blob = callArgs![0];
    expect(blob.type).toContain('text/csv');
    // JSDOM Blob doesn't expose .text(), but we can verify type and size.
    expect(blob.size).toBeGreaterThan(0);
  });

  it('switching breakdown segment triggers export with different data', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 1,
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 150,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(
      loaded([
        {
          thread_id: 'THR-001',
          sessions: 1,
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 150,
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Switch to thread segment
    const threadBtn = screen.getByRole('button', { name: 'Thread' });
    fireEvent.click(threadBtn);

    const exportBtn = screen.getByText('Export');
    expect(exportBtn).toBeDefined();

    createObjectURL.mockClear();
    fireEvent.click(exportBtn);

    // Should have the thread data
    const callArgs = createObjectURL.mock.calls[0] as unknown as [Blob] | undefined;
    expect(callArgs).toBeDefined();
    const blob = callArgs![0];
    expect(blob).toBeDefined();
  });

  // ---- Pasture detail section: fresh vs cache split ----

  it('renders Fresh / From cache / Detail split in hero', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 2000,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Pasture: "Fresh" section, "From cache" section, "Detail" section
    expect(screen.getByText('Fresh')).toBeDefined();
    expect(screen.getByText('From cache')).toBeDefined();
    expect(screen.getByText('Detail')).toBeDefined();
    // The cache percentage info
    expect(screen.getByText(/of all reads/)).toBeDefined();
  });

  it('renders "Where it went" section heading in Pasture style', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const h2 = screen.getByText('Where it went');
    expect(h2).toBeDefined();
    // Pasture: uppercase + text-text-secondary
    expect(h2.className).toContain('uppercase');
    expect(h2.className).toContain('text-text-secondary');
  });

  it('supports ArrowRight/ArrowLeft roving-focus on the breakdown segmented control', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const btns = screen.getAllByRole('button', { name: /^(Agent|Thread|Model)$/ });
    expect(btns.length).toBe(3);

    btns[0]!.focus();
    fireEvent.keyDown(btns[0]!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(btns[1]);

    fireEvent.keyDown(btns[1]!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(btns[0]);
  });

  // ---- SPEND-02: positive "cache saved" callout ----

  it('renders the green "cache saved" callout with saved tokens + served-from-cache %', () => {
    // cache_read 2000, total_tokens (fresh churn) 1600 -> served = 2000/(1600+2000) = 56%
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 2000,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // N saved = total cache_read_tokens (fmtNum -> "2.0K"); % uses the SAME
    // denominator as the hero "of all reads" stat: cache / (fresh + cache).
    const callout = screen.getByText(
      /Cache saved 2\.0K tokens .* 56% served from cache/,
    );
    expect(callout).toBeDefined();
    // Styled with the design-system success token (no hardcoded hex)
    expect(callout.closest('[class*="feedback-success"]')).not.toBeNull();
  });

  it('shows an honest zero state in the cache-saved callout when cache_read is 0', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 2,
          input_tokens: 1000,
          output_tokens: 600,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 1600,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Honest zero — not a hidden element.
    expect(
      screen.getByText(/Cache saved 0 tokens .* 0% served from cache/),
    ).toBeDefined();
  });

  // ---- SPEND-03: by-team breakdown card ----

  // Shared fixture: three burning agents across two real teams plus one agent
  // that is absent from the roster (→ honest 'unattributed' bucket).
  const SPEND03_AGENT_ROLLUP = [
    { agent: 'dev_agent', sessions: 2, input_tokens: 1000, output_tokens: 500,
      cache_read_tokens: 0, cache_creation_tokens: 0, reasoning_tokens: 0, total_tokens: 1600 },
    { agent: 'code_reviewer', sessions: 1, input_tokens: 300, output_tokens: 100,
      cache_read_tokens: 0, cache_creation_tokens: 0, reasoning_tokens: 0, total_tokens: 400 },
    { agent: 'consultant_a', sessions: 1, input_tokens: 200, output_tokens: 100,
      cache_read_tokens: 0, cache_creation_tokens: 0, reasoning_tokens: 0, total_tokens: 300 },
    { agent: 'ghost_agent', sessions: 1, input_tokens: 30, output_tokens: 20,
      cache_read_tokens: 0, cache_creation_tokens: 0, reasoning_tokens: 0, total_tokens: 50 },
  ];
  const SPEND03_ROSTER = [
    { name: 'dev_agent', team: 'engineering' },
    { name: 'code_reviewer', team: 'engineering' },
    { name: 'consultant_a', team: 'consultant' },
    // ghost_agent intentionally absent → unattributed
  ];

  function renderUsage03(): HTMLElement {
    mockAgentQ.mockReturnValue(loaded(SPEND03_AGENT_ROLLUP));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));
    mockAgentsList.mockReturnValue(
      agentsLoaded(SPEND03_ROSTER) as ReturnType<typeof useAgentsList>,
    );
    render(<UsagePage />);
    return screen.getByText('By team').closest('div') as HTMLElement;
  }

  it('renders a by-team card whose rows derive teams from the live roster', () => {
    const card = renderUsage03();
    // Teams come from the data, never fabricated.
    expect(within(card).getByText('engineering')).toBeDefined();
    expect(within(card).getByText('consultant')).toBeDefined();
  });

  it('each team total equals the summed burn of that team\'s agents', () => {
    const card = renderUsage03();
    // engineering = dev_agent 1600 + code_reviewer 400 = 2000 — rendered compact
    // via StatValue (THR-099), exact figure preserved in the title.
    expect(within(card).getByTitle('2,000')).toHaveTextContent('2.0K');
    // consultant = consultant_a 300 (<1000 stays exact)
    expect(within(card).getByText('300')).toBeDefined();
  });

  it('folds roster-less agents into an honest unattributed bucket', () => {
    const card = renderUsage03();
    // ghost_agent (absent from roster) → unattributed = 50
    expect(within(card).getByText('unattributed')).toBeDefined();
    expect(within(card).getByText('50')).toBeDefined();
  });

  it('renders a colored design-system dot per team row, distinct across teams', () => {
    const card = renderUsage03();
    const dotFor = (label: string): HTMLElement => {
      const row = within(card).getByText(label).closest('li') as HTMLElement;
      const dot = row.querySelector('span[aria-hidden="true"]') as HTMLElement;
      expect(dot).not.toBeNull();
      expect(dot.className).toContain('rounded-full');
      // Design-system token only — a bg-* utility, never a hardcoded hex.
      expect(dot.className).toMatch(/\bbg-[a-z]/);
      expect(dot.getAttribute('style')).toBeNull();
      return dot;
    };
    const eng = dotFor('engineering');
    const con = dotFor('consultant');
    const unattributed = dotFor('unattributed');
    // Deterministic categorical palette: two real teams get DIFFERENT tokens.
    expect(eng.className).not.toEqual(con.className);
    // Unattributed is the neutral token, distinct from any real-team color.
    expect(unattributed.className).toContain('bg-border-strong');
    expect(eng.className).not.toContain('bg-border-strong');
  });

  it('keeps the by-agent breakdown table alongside the new by-team card', () => {
    renderUsage03();
    // The existing by-agent table is untouched: agent rows still render.
    expect(screen.getByText('dev_agent')).toBeDefined();
    expect(screen.getByText('code_reviewer')).toBeDefined();
    // And the segment control still offers the Agent breakdown.
    expect(screen.getByRole('button', { name: 'Agent' })).toBeDefined();
  });

  it('shows the by-team empty state when there is no agent burn', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));
    render(<UsagePage />);
    const card = screen.getByText('By team').closest('div') as HTMLElement;
    expect(within(card).getByText('No token usage in this window')).toBeDefined();
  });

  it('shows the honest by-team error state — not an all-unattributed table — when the roster request fails', () => {
    // Roster (agents-list) request FAILS while the usage rollup SUCCEEDS with
    // non-empty burn. An unavailable roster must NOT be treated as proof that
    // no agents have teams: the by-team card must NOT silently fold every
    // burning agent into the 'unattributed' bucket (which fabricates a
    // 'no agents have teams' reality). It must surface the honest error state.
    mockAgentQ.mockReturnValue(loaded(SPEND03_AGENT_ROLLUP));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));
    mockAgentsList.mockReturnValue(errored() as ReturnType<typeof useAgentsList>);

    render(<UsagePage />);
    const card = screen.getByText('By team').closest('div') as HTMLElement;

    // No fabricated 'unattributed' bucket from joining against the empty []
    // roster fallback when the roster itself failed.
    expect(within(card).queryByText('unattributed')).toBeNull();
    // Honest error/unavailable state instead — mirrors the usage-rollup case.
    expect(
      within(card).getByText("Couldn't load usage by team — retry"),
    ).toBeDefined();
  });

  // ---- SPEND-04: hero eyebrow date range ----

  it('fmtDateRange formats a same-month window as "Mon D–D"', () => {
    // Local-component constructor so the helper's local get* calls are TZ-stable.
    const start = new Date(2026, 5, 17); // Jun 17 2026
    const end = new Date(2026, 5, 24); // Jun 24 2026
    expect(fmtDateRange(start, end)).toBe('Jun 17–24');
  });

  it('fmtDateRange formats a cross-month window as "Mon D – Mon D"', () => {
    const start = new Date(2026, 4, 28); // May 28 2026
    const end = new Date(2026, 5, 3); // Jun 3 2026
    expect(fmtDateRange(start, end)).toBe('May 28 – Jun 3');
  });

  it('renders the actual [since..now] date range in the hero eyebrow', () => {
    // Pin the clock: 7d window ending Jun 24 12:00 local → since Jun 17 12:00.
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 24, 12, 0, 0));
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 1,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Eyebrow keeps 'Token burn · {windowLabel}' and appends the honest range.
    expect(screen.getByText('Token burn · 7d · Jun 17–24')).toBeDefined();
  });

  it('renders the date range in the hero eyebrow zero state too', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 24, 12, 0, 0));
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    expect(screen.getByText('Token burn · 7d · Jun 17–24')).toBeDefined();
  });

  // ---- SPEND-04: per-row model pill on the burn breakdown table ----

  it('renders a model pill on a burn row when the rollup carries an observed model', () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
          model_any: 'claude-sonnet-4-5[1m]',
          non_null_sessions: 3,
          model_distinct: 1,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // The breakdown row (default 'agent' segment) carries the observed model as
    // a small rounded pill — threadRollup is empty so this is the only source.
    const pill = screen.getByText('claude-sonnet-4-5[1m]');
    expect(pill.className).toContain('rounded-full');
  });

  it("renders a '(mixed)' model pill when a burn row spans >1 distinct model", () => {
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 4,
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 100,
          total_tokens: 1600,
          model_any: 'claude-opus-4-8[1m]',
          non_null_sessions: 4,
          model_distinct: 2,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    const pill = screen.getByText('(mixed)');
    expect(pill.className).toContain('rounded-full');
  });

  it('omits the model pill on a burn row when there is no observed model (honesty fence)', () => {
    // All sessions NULL-model (codex) → no observed model on the row. The pill
    // must be OMITTED rather than fabricated; the classifyModel placeholder
    // '(cli-unreported)' must NOT surface as a breakdown-row pill.
    mockAgentQ.mockReturnValue(
      loaded([
        {
          agent: 'dev_agent',
          sessions: 3,
          input_tokens: 500,
          output_tokens: 200,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          reasoning_tokens: 0,
          total_tokens: 700,
          non_null_sessions: 0,
          null_codex_sessions: 3,
        },
      ]),
    );
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<UsagePage />);

    // Row still renders (agent label present) but no fabricated model pill.
    expect(screen.getByText('dev_agent')).toBeDefined();
    expect(screen.queryByText('(cli-unreported)')).toBeNull();
  });
});

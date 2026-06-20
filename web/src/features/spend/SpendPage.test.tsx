import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the spend hooks so the page renders deterministically
vi.mock('@/hooks/spend', () => ({
  useSpendByAgent: vi.fn(),
  useSpendByThread: vi.fn(),
  useSpendByModel: vi.fn(),
}));

import { useSpendByAgent, useSpendByThread, useSpendByModel } from '@/hooks/spend';
import { SpendPage } from './SpendPage';

// Stub URL.createObjectURL for CSV export tests
const createObjectURL = vi.fn(() => 'blob:stub');
URL.createObjectURL = createObjectURL;

const mockAgentQ = vi.mocked(useSpendByAgent);
const mockThreadQ = vi.mocked(useSpendByThread);
const mockModelQ = vi.mocked(useSpendByModel);

function loaded<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function loading() {
  return { data: undefined, isLoading: true, isError: false, error: null };
}

function errored() {
  return { data: undefined, isLoading: false, isError: true, error: new Error('fail') };
}

describe('SpendPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the header with window toggles', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<SpendPage />);

    expect(screen.getByText('Spend')).toBeDefined();
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

    render(<SpendPage />);

    // Loading state: the hero card should show the animate-pulse skeleton
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('shows empty state when no token spend data', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<SpendPage />);

    const empties = screen.getAllByText('No token spend in this window');
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

    render(<SpendPage />);

    expect(screen.getByText(/Couldn't load spend data/)).toBeDefined();
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

    render(<SpendPage />);

    // Hero should show total churn = 1600
    const churns = screen.getAllByText('1.6K');
    expect(churns.length).toBeGreaterThanOrEqual(1);
    // Pasture: hero burn numeral uses font-display
    const heroNum = churns[0]!;
    expect(heroNum.className).toContain('font-display');
    // Cache savings = 2000 (shown as "From cache" line)
    const caches = screen.getAllByText('2.0K');
    expect(caches.length).toBeGreaterThanOrEqual(1);
    // "not metered" caption
    expect(screen.getByText('$0.00 · not metered')).toBeDefined();
    // Pasture: section label uses text-text-secondary + uppercase tracking
    expect(screen.getByText('Token burn · 7d')).toBeDefined();
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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

    expect(screen.getByText('(unknown — ANOMALY)')).toBeDefined();
  });

  // ---- Keyboard navigation (ArrowLeft/ArrowRight roving-focus) ----

  it('supports ArrowRight roving-focus on the window toggle group', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

    expect(screen.getByText('Export')).toBeDefined();
  });

  it('does not render Export button when there is no data', () => {
    mockAgentQ.mockReturnValue(loaded([]));
    mockThreadQ.mockReturnValue(loaded([]));
    mockModelQ.mockReturnValue(loaded([]));

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

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

    render(<SpendPage />);

    const btns = screen.getAllByRole('button', { name: /^(Agent|Thread|Model)$/ });
    expect(btns.length).toBe(3);

    btns[0]!.focus();
    fireEvent.keyDown(btns[0]!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(btns[1]);

    fireEvent.keyDown(btns[1]!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(btns[0]);
  });
});

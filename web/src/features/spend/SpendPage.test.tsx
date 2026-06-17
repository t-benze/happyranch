import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the spend hooks so the page renders deterministically
vi.mock('@/hooks/spend', () => ({
  useSpendByAgent: vi.fn(),
  useSpendByThread: vi.fn(),
  useSpendByModel: vi.fn(),
}));

import { useSpendByAgent, useSpendByThread, useSpendByModel } from '@/hooks/spend';
import { SpendPage } from './SpendPage';

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
    // Cache savings = 2000
    const caches = screen.getAllByText('2.0K');
    expect(caches.length).toBeGreaterThanOrEqual(1);
    // "not metered" caption
    expect(screen.getByText('$0.00 · not metered')).toBeDefined();
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
    // Segmentation toggles
    expect(screen.getByRole('button', { name: 'Agent' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Thread' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Model' })).toBeDefined();
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
          model_any: 'sonnet',
        },
      ]),
    );
    mockModelQ.mockReturnValue(loaded([]));

    render(<SpendPage />);

    expect(screen.getByText('Top threads by churn')).toBeDefined();
    expect(screen.getByText('THR-001')).toBeDefined();
    expect(screen.getByText('sonnet')).toBeDefined();
  });
});

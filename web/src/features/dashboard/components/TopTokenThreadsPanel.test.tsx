import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { tokens } from '@/lib/api';
import { TopTokenThreadsPanel } from './TopTokenThreadsPanel';

type TokenUsageRollup = tokens.TokenUsageRollup;

// The panel fetches its own data; mock the hook so the render is deterministic
// and provider-free.
vi.mock('@/hooks/tokens', () => ({ useTopThreadTokens: vi.fn() }));
import { useTopThreadTokens } from '@/hooks/tokens';

const mockHook = vi.mocked(useTopThreadTokens);

function rollup(over: Partial<TokenUsageRollup>): TokenUsageRollup {
  return {
    sessions: 1,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    ...over,
  };
}

function loaded(data: TokenUsageRollup[]) {
  return { data, isLoading: false, isError: false, error: null };
}

describe('TopTokenThreadsPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('ranks by churn DESC, separates cache from total, and labels models', () => {
    mockHook.mockReturnValue(
      loaded([
        // Big cache, tiny churn — must rank BELOW the high-churn thread.
        rollup({
          thread_id: 'THR-low',
          input_tokens: 10,
          output_tokens: 5,
          reasoning_tokens: 0,
          cache_read_tokens: 9_999_999,
          total_tokens: 15,
          non_null_sessions: 0,
          null_codex_sessions: 2,
        }),
        // Tiny cache, big churn — must rank FIRST.
        rollup({
          thread_id: 'THR-high',
          input_tokens: 1000,
          output_tokens: 500,
          reasoning_tokens: 200,
          cache_read_tokens: 1,
          total_tokens: 1700,
          non_null_sessions: 3,
          model_distinct: 1,
          model_any: 'claude-opus-4-8[1m]',
        }),
      ]),
    );

    const { container } = render(<TopTokenThreadsPanel />);

    const items = screen.getAllByRole('listitem');
    expect(items.map((li) => within(li).getByTitle(/THR-/).textContent)).toEqual([
      'THR-high',
      'THR-low',
    ]);

    // High-churn row: observed model id verbatim, churn total shown.
    const high = items[0];
    expect(within(high).getByText('claude-opus-4-8[1m]')).toBeInTheDocument();
    expect(within(high).getByText('1,700')).toBeInTheDocument();

    // Low row: codex NULL → (cli-unreported); the huge cache number is shown
    // but is NOT the churn total (15).
    const low = items[1];
    expect(within(low).getByText('(cli-unreported)')).toBeInTheDocument();
    expect(within(low).getByText('15')).toBeInTheDocument();
    expect(within(low).getByText(/9,999,999/)).toBeInTheDocument();

    // Window selector present, 7d default.
    expect(screen.getByRole('button', { name: '7d' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    // Rendered-HTML evidence (captured in vitest stdout).
    console.log('\n===PANEL_HTML_START===\n' + container.innerHTML + '\n===PANEL_HTML_END===\n');
  });

  it('shows an empty state when the window has no usage', () => {
    mockHook.mockReturnValue(loaded([]));
    render(<TopTokenThreadsPanel />);
    expect(screen.getByText('No token usage in window.')).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', () => {
    mockHook.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    });
    render(<TopTokenThreadsPanel />);
    expect(screen.getByText('Failed to load token usage.')).toBeInTheDocument();
  });
});

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

  it('ranks by churn DESC and separates cache from total', () => {
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

    // High-churn row: churn total shown compact (THR-099) with full precision
    // preserved in the StatValue title.
    const high = items[0];
    expect(within(high).getByTitle('1,700')).toHaveTextContent('1.7K');

    // Low row: the huge cache number is shown (compact, full precision in
    // title) but is NOT the churn total (15).
    const low = items[1];
    expect(within(low).getByTitle('15')).toHaveTextContent('15'); // <1000 stays exact
    expect(within(low).getByTitle('9,999,999')).toHaveTextContent('10.0M');

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

  // BUG 2 — cache count cramped: the cache number must render with a
  // distinguishable label that has at least 6px (ml-1.5) of separation
  // from the number, and distinct styling from the number.
  it('BUG 2: cache figure renders with distinct spacing and label styling', () => {
    mockHook.mockReturnValue(
      loaded([
        rollup({
          thread_id: 'THR-cache',
          input_tokens: 100,
          output_tokens: 50,
          total_tokens: 150,
          cache_read_tokens: 1234567,
          non_null_sessions: 1,
          model_distinct: 1,
          model_any: 'claude-opus-4-8[1m]',
        }),
      ]),
    );

    render(<TopTokenThreadsPanel />);

    // The cache column shows the compact number (full precision preserved in
    // the StatValue title) and a "cache" label (THR-099).
    const cacheSpan = screen.getByTitle('1,234,567');
    expect(cacheSpan).toHaveTextContent('1.2M');

    // The "cache" label text must be present as a distinct element
    // (an inner span with its own class for styling).
    const cacheLabel = within(cacheSpan).getByText('cache');
    expect(cacheLabel).toBeInTheDocument();
    expect(cacheLabel.tagName).toBe('SPAN');

    // The label span must have a margin-left class ≥ ml-1.5 (6px) for adequate
    // separation from the number at text-xs scale.
    const labelClass = cacheLabel.getAttribute('class') ?? '';
    const hasAdequateMargin =
      labelClass.includes('ml-1.5') ||
      labelClass.includes('ml-2') ||
      labelClass.includes('ml-2.5') ||
      labelClass.includes('ml-3');
    expect(hasAdequateMargin).toBe(true);

    // The label must have dimmer styling than the number so it reads as a
    // secondary label, not part of the figure.
    // text-text-disabled is the design-system class for dimmed secondary text.
    expect(labelClass).toMatch(/text-text-disabled|text-text-muted\b.*!text/);
  });
});

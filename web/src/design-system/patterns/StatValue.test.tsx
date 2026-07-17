import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatValue } from './StatValue';

/**
 * StatValue — overflow-safe display-metric primitive (THR-099 number-overflow
 * fix). Renders a compact, tabular-nums numeral in a reserved-width, no-clip
 * container while preserving FULL precision via the `title` tooltip. Display
 * metrics ONLY: identifiers keep IdBadge / truncate+title and must not be
 * routed through StatValue.
 *
 * Class assertions use `toHaveClass` / classList tokens (NOT word-boundary
 * regex) so a restricted-theme class rename can't silently pass.
 */
describe('StatValue — compact + overflow-safe display metric', () => {
  test('renders the compact token text, not the raw full-precision number', () => {
    render(<StatValue value={3_707_054} />);
    expect(screen.getByText('3.7M')).toBeInTheDocument();
    expect(screen.queryByText('3,707,054')).not.toBeInTheDocument();
  });

  test('title carries the FULL precision (value.toLocaleString) so nothing is lost', () => {
    render(<StatValue value={126_335_691} />);
    const el = screen.getByText('126.3M');
    expect(el).toHaveAttribute('title', '126,335,691');
  });

  test('applies tabular-nums and a no-wrap, reserved-width container', () => {
    render(<StatValue value={346_100} />);
    const el = screen.getByText('346.1K');
    expect(el).toHaveClass('tabular-nums');
    expect(el).toHaveClass('whitespace-nowrap');
    // reserved min-width so the numeral has a stable, non-clipping box
    expect(el.classList.contains('min-w-16')).toBe(true);
  });

  test('format="count" renders an exact grouped integer, not a compacted token', () => {
    render(<StatValue value={1234} format="count" />);
    expect(screen.getByText('1,234')).toBeInTheDocument();
  });

  test('renders an optional muted suffix (e.g. "cache")', () => {
    render(<StatValue value={100} suffix="cache" />);
    const suffix = screen.getByText('cache');
    expect(suffix).toBeInTheDocument();
    expect(suffix).toHaveClass('text-text-disabled');
  });

  test('align="inline" drops the right-reserved box for centered/hero placement but keeps no-clip safety', () => {
    render(<StatValue value={346_100} align="inline" className="font-display" />);
    const el = screen.getByText('346.1K');
    expect(el).toHaveClass('whitespace-nowrap');
    expect(el).toHaveClass('font-display');
    // inline mode does not force the right-aligned reserved box
    expect(el.classList.contains('min-w-16')).toBe(false);
    expect(el.classList.contains('text-right')).toBe(false);
  });

  test('compacts any numeric input — so identifiers (ports, ids) must NOT be routed through it', () => {
    // A port like 8765 would render "8.8K": proof StatValue is display-metric
    // only. Identifiers keep IdBadge / truncate+title, never StatValue.
    render(<StatValue value={8765} />);
    expect(screen.getByText('8.8K')).toBeInTheDocument();
  });
});

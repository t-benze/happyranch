import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Tabs, TabsList, TabsTrigger } from './Tabs';

/**
 * THR-099 — the `segmented` variant is an ADDITIVE bordered-pill control for the
 * thread-list All/Open/Archived filter (a-threads `.seg`): the list is a
 * rounded, bordered container and the active trigger fills green (accent-soft).
 * The existing `pills`/`underline` variants are untouched; this test pins the
 * new variant's container + active-fill classes so a regression is caught.
 */
describe('Tabs — segmented variant (additive)', () => {
  function renderSegmented(value: string) {
    return render(
      <Tabs value={value}>
        <TabsList variant="segmented" aria-label="Status filter">
          <TabsTrigger variant="segmented" value="all">
            All
          </TabsTrigger>
          <TabsTrigger variant="segmented" value="open">
            Open
          </TabsTrigger>
        </TabsList>
      </Tabs>,
    );
  }

  test('renders a bordered pill CONTAINER for the segmented list', () => {
    renderSegmented('all');
    const list = screen.getByRole('tablist', { name: /status filter/i });
    expect(list).toHaveClass('rounded-full', 'border', 'bg-surface-sunken');
    expect(list).toHaveAttribute('data-variant', 'segmented');
  });

  test('the active trigger fills green (accent-soft); the inactive one does not', () => {
    renderSegmented('all');
    const active = screen.getByRole('tab', { name: 'All' });
    const inactive = screen.getByRole('tab', { name: 'Open' });
    expect(active).toHaveAttribute('data-state', 'active');
    expect(inactive).toHaveAttribute('data-state', 'inactive');
    // The active-fill is expressed as a data-[state=active] utility on both
    // triggers; Radix drives which one paints via data-state.
    expect(active.className).toContain('data-[state=active]:bg-accent-soft');
  });
});

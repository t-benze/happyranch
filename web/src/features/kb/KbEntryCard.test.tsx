import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { KbEntryCard } from './KbEntryCard';

// KB types are freeform. Prove the actual card consumes shared vocabulary,
// including the future consequence of adding Todo words to that vocabulary.
describe('KbEntryCard shared type colours', () => {
  it.each([
    ['armed', 'text-status-open', 'bg-tier-green-tint'],
    ['firing', 'text-status-open', 'bg-tier-green-tint'],
    ['fired', 'text-status-open', 'bg-tier-green-tint'],
    ['failed', 'text-attention-text', 'bg-attention-soft'],
    ['timeout', 'text-attention-text', 'bg-attention-soft'],
    ['paused', 'text-status-archived', 'bg-transparent'],
    ['cancelled', 'text-status-archived', 'bg-transparent'],
    ['expired', 'text-status-archived', 'bg-transparent'],
    ['sop', 'text-status-open', 'bg-tier-green-tint'],
    ['reference', 'text-info', 'bg-info-soft'],
    ['ruling', 'text-attention-text', 'bg-attention-soft'],
    ['pattern', 'text-status-archived', 'bg-transparent'],
    ['precedent', 'text-status-archived', 'bg-transparent'],
    ['ADR', 'text-status-archived', 'bg-transparent'],
    ['novel-type', 'text-status-archived', 'bg-transparent'],
    [' FAILED ', 'text-attention-text', 'bg-attention-soft'],
  ])('renders freeform type %s using its shared tone', (type, foreground, fill) => {
    render(
      <MemoryRouter>
        <KbEntryCard to="/orgs/happyranch/kb/example" entry={{
          slug: 'example', title: 'Example entry', type, topic: 'testing',
          tags: [], body: '', updated_at: '2026-09-09T00:00:00Z',
          authored_by: 'frontend_engineer', source_task: null,
        }} />
      </MemoryRouter>,
    );
    const badge = screen.getByText(type.trim());
    expect(badge.textContent).toBe(type);
    expect(badge).toHaveClass(foreground, fill);
    if (fill === 'bg-transparent') {
      expect(badge).toHaveClass('border', 'border-border-default');
    }
    expect(screen.getByRole('link')).toHaveAttribute('href', '/orgs/happyranch/kb/example');
  });
});

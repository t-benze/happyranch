import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ResponderStatusStrip } from './ResponderStatusStrip';

describe('ResponderStatusStrip', () => {
  it('renders empty when no statuses', () => {
    const { container } = render(<ResponderStatusStrip statuses={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one row per participant with status label', () => {
    render(
      <ResponderStatusStrip
        statuses={[
          { agent_name: 'alpha', status: 'pending', responded_at: null },
          { agent_name: 'bravo', status: 'replied', responded_at: '2026-05-30T10:00:00Z' },
          { agent_name: 'charlie', status: 'declined', responded_at: '2026-05-30T10:01:00Z' },
        ]}
      />,
    );
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('pending…')).toBeInTheDocument();
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(screen.getByText('declined')).toBeInTheDocument();
  });

  it('renders failed status in amber', () => {
    const { container } = render(
      <ResponderStatusStrip
        statuses={[{ agent_name: 'delta', status: 'failed', responded_at: null }]}
      />,
    );
    expect(screen.getByText('failed')).toBeInTheDocument();
    const failedSpan = container.querySelector('.text-amber-600');
    expect(failedSpan).not.toBeNull();
  });
});

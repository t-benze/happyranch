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
          { agent_name: 'alpha', status: 'queued', responded_at: null, started_at: null },
          { agent_name: 'bravo', status: 'replied', responded_at: '2026-05-30T10:00:00Z', started_at: null },
          { agent_name: 'charlie', status: 'declined', responded_at: '2026-05-30T10:01:00Z', started_at: null },
        ]}
      />,
    );
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('queued')).toBeInTheDocument();
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(screen.getByText('declined')).toBeInTheDocument();
  });

  it('renders failed status in amber', () => {
    const { container } = render(
      <ResponderStatusStrip
        statuses={[{ agent_name: 'delta', status: 'failed', responded_at: null, started_at: null }]}
      />,
    );
    expect(screen.getByText('failed')).toBeInTheDocument();
    const failedSpan = container.querySelector('.text-amber-600');
    expect(failedSpan).not.toBeNull();
  });

  it('renders queued and working-with-elapsed', () => {
    const now = 1_000_000_000_000;
    const started = new Date(now - 45_000).toISOString(); // 45s ago
    render(
      <ResponderStatusStrip
        nowMs={now}
        statuses={[
          { agent_name: 'alpha', status: 'working', responded_at: null, started_at: started },
          { agent_name: 'bravo', status: 'queued', responded_at: null, started_at: null },
        ]}
      />,
    );
    expect(screen.getByText('working 45s')).toBeInTheDocument();
    expect(screen.getByText('queued')).toBeInTheDocument();
  });
});

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { TypingBubble } from './TypingBubble';

const now = 1_000_000_000_000;
const ago = (s: number) => new Date(now - s * 1000).toISOString();

describe('TypingBubble', () => {
  it('shows the agent name and live elapsed while working', () => {
    render(
      <TypingBubble agentName="engineering_head" status="working" startedAt={ago(45)} nowMs={now} />,
    );
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
    expect(screen.getByText('replying… 45s')).toBeInTheDocument();
    expect(
      screen.getByLabelText('engineering_head is replying'),
    ).toBeInTheDocument();
  });

  it('drops the elapsed suffix when no start time is known', () => {
    render(<TypingBubble agentName="alpha" status="working" startedAt={null} nowMs={now} />);
    expect(screen.getByText('replying…')).toBeInTheDocument();
  });

  it('renders a queued caption for a waiting agent', () => {
    render(<TypingBubble agentName="bravo" status="queued" startedAt={null} nowMs={now} />);
    expect(screen.getByText('bravo')).toBeInTheDocument();
    expect(screen.getByText('queued')).toBeInTheDocument();
    expect(screen.getByLabelText('bravo is queued')).toBeInTheDocument();
  });
});

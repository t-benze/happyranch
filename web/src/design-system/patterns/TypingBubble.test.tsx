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

  // a-thread-detail `.replying`: compact inline indicator — no heavy card,
  // just a bold name row above a small dots chat-bubble. Token-aware class
  // checks (classList.contains) rather than word-boundary regex (MEM-327).
  it('renders as a compact inline indicator, not a bordered card', () => {
    render(<TypingBubble agentName="charlie" status="working" startedAt={null} nowMs={now} />);
    const root = screen.getByLabelText('charlie is replying');
    // No card chrome: the surface-raised/padding card is gone.
    expect(root.classList.contains('p-4')).toBe(false);
    expect(root.classList.contains('bg-surface-raised')).toBe(false);
    expect(root.classList.contains('rounded-lg')).toBe(false);
    // Agent name is bold plain text (AgentChip role-dot dropped per design).
    const name = screen.getByText('charlie');
    expect(name.classList.contains('font-semibold')).toBe(true);
  });

  it('holds the dots inside a small surface-sunken chat-bubble', () => {
    render(<TypingBubble agentName="delta" status="working" startedAt={null} nowMs={now} />);
    const dot = screen.getByLabelText('delta is replying').querySelector('.typing-dot');
    expect(dot).not.toBeNull();
    const bubble = dot!.closest('div.bg-surface-sunken');
    expect(bubble).not.toBeNull();
    expect(bubble!.classList.contains('border')).toBe(true);
    expect(bubble!.classList.contains('rounded-lg')).toBe(true);
  });

  it('tints the caption info-blue while working, muted while queued', () => {
    const { rerender } = render(
      <TypingBubble agentName="echo" status="working" startedAt={null} nowMs={now} />,
    );
    expect(screen.getByText('replying…').classList.contains('text-info')).toBe(true);
    rerender(<TypingBubble agentName="echo" status="queued" startedAt={null} nowMs={now} />);
    expect(screen.getByText('queued').classList.contains('text-text-muted')).toBe(true);
  });

  // The generic `trailing` slot is still supported for arbitrary inline
  // controls, but the "Abort reply" control no longer lives here — it moved
  // INTO the composer input pill (THR-099 Phase A). Use a neutral label so the
  // test documents the generic capability without implying abort placement.
  it('renders an optional trailing control in the name row', () => {
    render(
      <TypingBubble
        agentName="foxtrot"
        status="working"
        startedAt={null}
        nowMs={now}
        trailing={<button type="button">Custom action</button>}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Custom action' }),
    ).toBeInTheDocument();
  });

  it('renders no abort control beside the replying indicator by default', () => {
    render(<TypingBubble agentName="golf" status="working" startedAt={null} nowMs={now} />);
    expect(screen.queryByRole('button', { name: /Abort reply/i })).toBeNull();
  });
});

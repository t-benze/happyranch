import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ResponderStatusStrip } from './ResponderStatusStrip';

describe('ResponderStatusStrip', () => {
  it('renders empty when no statuses', () => {
    const { container } = render(<ResponderStatusStrip statuses={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one row per terminal participant with status label', () => {
    render(
      <ResponderStatusStrip
        statuses={[
          { agent_name: 'bravo', status: 'replied', responded_at: '2026-05-30T10:00:00Z', started_at: null, decline_reason: null, category: null },
          { agent_name: 'charlie', status: 'declined', responded_at: '2026-05-30T10:01:00Z', started_at: null, decline_reason: null, category: null },
        ]}
      />,
    );
    expect(screen.getByText('bravo')).toBeInTheDocument();
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(screen.getByText('charlie')).toBeInTheDocument();
    expect(screen.getByText('declined')).toBeInTheDocument();
  });

  it('renders failed status in amber', () => {
    const { container } = render(
      <ResponderStatusStrip
        statuses={[{ agent_name: 'delta', status: 'failed', responded_at: null, started_at: null, decline_reason: null, category: null }]}
      />,
    );
    expect(screen.getByText('failed')).toBeInTheDocument();
    const failedSpan = container.querySelector('.text-amber-600');
    expect(failedSpan).not.toBeNull();
  });

  it('omits in-flight (queued/working) statuses — those are shown by TypingBubble', () => {
    const now = 1_000_000_000_000;
    const started = new Date(now - 45_000).toISOString(); // 45s ago
    const { container } = render(
      <ResponderStatusStrip
        nowMs={now}
        statuses={[
          { agent_name: 'alpha', status: 'working', responded_at: null, started_at: started, decline_reason: null, category: null },
          { agent_name: 'bravo', status: 'queued', responded_at: null, started_at: null, decline_reason: null, category: null },
        ]}
      />,
    );
    // No terminal entries → strip renders nothing.
    expect(container.firstChild).toBeNull();
  });

  it('renders the terminal record independent of a concurrent working indicator', () => {
    // The persisted terminal entry must show even while another responder is
    // still working — the strip is driven by responder_status, not the
    // transient typing indicator.
    render(
      <ResponderStatusStrip
        statuses={[
          { agent_name: 'alpha', status: 'working', responded_at: null, started_at: null, decline_reason: null, category: null },
          {
            agent_name: 'charlie',
            status: 'declined',
            responded_at: '2026-05-30T10:01:00Z',
            started_at: null,
            decline_reason: null,
            category: 'declined',
          },
        ]}
      />,
    );
    expect(screen.getByText('charlie')).toBeInTheDocument();
    expect(screen.getByText('declined')).toBeInTheDocument();
  });

  it('renders category-distinguished terminal labels so the four causes are separable', () => {
    render(
      <ResponderStatusStrip
        statuses={[
          {
            agent_name: 'a-declined',
            status: 'declined',
            responded_at: '2026-05-30T10:00:00Z',
            started_at: null,
            decline_reason: 'not my area',
            category: 'declined',
          },
          {
            agent_name: 'b-nocallback',
            status: 'failed',
            responded_at: null,
            started_at: null,
            decline_reason: 'no_callback: clean exit',
            category: 'no_callback',
          },
          {
            agent_name: 'c-reprompt',
            status: 'failed',
            responded_at: null,
            started_at: null,
            decline_reason: 'no_callback_after_reprompt: still nothing',
            category: 'no_callback_after_reprompt',
          },
          {
            agent_name: 'd-infra',
            status: 'failed',
            responded_at: null,
            started_at: null,
            decline_reason: 'runner_crash rc=143',
            category: 'infra_fail',
          },
        ]}
      />,
    );
    expect(screen.getByText('declined')).toBeInTheDocument();
    expect(screen.getByText('reply failed (no callback)')).toBeInTheDocument();
    expect(screen.getByText('reply failed (no callback after re-prompt)')).toBeInTheDocument();
    expect(screen.getByText('reply failed (infra: rc=143)')).toBeInTheDocument();
  });

  it('shows a bare infra label when no rc/detail is parseable from decline_reason', () => {
    render(
      <ResponderStatusStrip
        statuses={[
          {
            agent_name: 'infra-bare',
            status: 'failed',
            responded_at: null,
            started_at: null,
            decline_reason: '529 overloaded',
            category: 'infra_fail',
          },
        ]}
      />,
    );
    expect(screen.getByText('reply failed (infra)')).toBeInTheDocument();
  });

  it('falls back to the generic label when category is null on a declined/failed row', () => {
    // Older/replied data carries no category — keep today's generic labels.
    render(
      <ResponderStatusStrip
        statuses={[
          {
            agent_name: 'legacy-declined',
            status: 'declined',
            responded_at: '2026-05-30T10:00:00Z',
            started_at: null,
            decline_reason: null,
            category: null,
          },
          {
            agent_name: 'legacy-failed',
            status: 'failed',
            responded_at: null,
            started_at: null,
            decline_reason: null,
            category: null,
          },
        ]}
      />,
    );
    expect(screen.getByText('declined')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('renders a replied entry unchanged (replied, emerald) — no regression', () => {
    const { container } = render(
      <ResponderStatusStrip
        statuses={[
          {
            agent_name: 'echo',
            status: 'replied',
            responded_at: '2026-05-30T10:00:00Z',
            started_at: null,
            decline_reason: null,
            category: null,
          },
        ]}
      />,
    );
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(container.querySelector('.text-emerald-600')).not.toBeNull();
  });
});

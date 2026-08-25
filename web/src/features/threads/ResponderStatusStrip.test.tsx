import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import type { ResponderStatusEntry } from '@/lib/api/types';
import { ResponderStatusStrip } from './ResponderStatusStrip';

/** One responder entry — purpose defaults to 'reply' (TASK-5553 wire field). */
function rs(
  agent_name: string,
  status: ResponderStatusEntry['status'],
  over: Partial<ResponderStatusEntry> = {},
): ResponderStatusEntry {
  return {
    agent_name,
    purpose: 'reply',
    status,
    responded_at: null,
    started_at: null,
    decline_reason: null,
    category: null,
    ...over,
  };
}

describe('ResponderStatusStrip', () => {
  it('renders empty when no statuses', () => {
    const { container } = render(<ResponderStatusStrip statuses={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one row per terminal participant with status label', () => {
    render(
      <ResponderStatusStrip
        statuses={[
          rs('bravo', 'replied', { responded_at: '2026-05-30T10:00:00Z' }),
          rs('charlie', 'declined', { responded_at: '2026-05-30T10:01:00Z' }),
        ]}
      />,
    );
    expect(screen.getByText('bravo')).toBeInTheDocument();
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(screen.getByText('charlie')).toBeInTheDocument();
    expect(screen.getByText('declined')).toBeInTheDocument();
  });

  it('renders a failed status with the danger pill token', () => {
    const { container } = render(<ResponderStatusStrip statuses={[rs('delta', 'failed')]} />);
    expect(screen.getByText('failed')).toBeInTheDocument();
    const failedSpan = container.querySelector('.text-danger');
    expect(failedSpan).not.toBeNull();
  });

  it('omits in-flight (queued/working) statuses — those are shown by TypingBubble', () => {
    const now = 1_000_000_000_000;
    const started = new Date(now - 45_000).toISOString(); // 45s ago
    const { container } = render(
      <ResponderStatusStrip
        nowMs={now}
        statuses={[rs('alpha', 'working', { started_at: started }), rs('bravo', 'queued')]}
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
          rs('alpha', 'working'),
          rs('charlie', 'declined', {
            responded_at: '2026-05-30T10:01:00Z',
            category: 'declined',
          }),
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
          rs('a-declined', 'declined', {
            responded_at: '2026-05-30T10:00:00Z',
            decline_reason: 'not my area',
            category: 'declined',
          }),
          rs('b-nocallback', 'failed', {
            decline_reason: 'no_callback: clean exit',
            category: 'no_callback',
          }),
          rs('c-reprompt', 'failed', {
            decline_reason: 'no_callback_after_reprompt: still nothing',
            category: 'no_callback_after_reprompt',
          }),
          rs('d-infra', 'failed', {
            decline_reason: 'runner_crash rc=143',
            category: 'infra_fail',
          }),
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
          rs('infra-bare', 'failed', {
            decline_reason: '529 overloaded',
            category: 'infra_fail',
          }),
        ]}
      />,
    );
    expect(screen.getByText('reply failed (infra)')).toBeInTheDocument();
  });

  it('falls back to the generic label when category is null on a declined/failed row', () => {
    // Older/replied data carries no category — keep today's generic labels.
    render(
      <ResponderStatusStrip statuses={[rs('legacy-declined', 'declined', { responded_at: '2026-05-30T10:00:00Z' }), rs('legacy-failed', 'failed')]} />,
    );
    expect(screen.getByText('declined')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('renders a founder-aborted reply as a NEUTRAL "aborted" state, not red "reply failed"', () => {
    // Backend reap persists an abort as status=failed / category=infra_fail with
    // decline_reason='founder_aborted'. That marker must divert to a neutral
    // 'aborted' label — NOT the red "reply failed (infra)" wording/danger token.
    const { container } = render(
      <ResponderStatusStrip
        statuses={[
          rs('aborted-agent', 'failed', {
            decline_reason: 'founder_aborted',
            category: 'infra_fail',
          }),
        ]}
      />,
    );
    expect(screen.getByText('aborted')).toBeInTheDocument();
    // Neutral, not danger — no reply-failed wording, no danger token.
    expect(screen.queryByText(/reply failed/i)).toBeNull();
    expect(container.querySelector('.text-danger')).toBeNull();
    expect(container.querySelector('.text-text-muted')).not.toBeNull();
  });

  it('keeps the danger token for a GENUINE infra failure (not founder_aborted)', () => {
    // Regression fence: only decline_reason==='founder_aborted' diverts; a real
    // infra failure must still render red "reply failed (infra…)" with danger.
    const { container } = render(
      <ResponderStatusStrip
        statuses={[
          rs('crashed-agent', 'failed', {
            decline_reason: 'runner_crash rc=143',
            category: 'infra_fail',
          }),
        ]}
      />,
    );
    expect(screen.getByText('reply failed (infra: rc=143)')).toBeInTheDocument();
    expect(screen.queryByText('aborted')).toBeNull();
    expect(container.querySelector('.text-danger')).not.toBeNull();
  });

  it('renders a replied entry with the accent pill token — no regression', () => {
    const { container } = render(
      <ResponderStatusStrip
        statuses={[rs('echo', 'replied', { responded_at: '2026-05-30T10:00:00Z' })]}
      />,
    );
    expect(screen.getByText('replied')).toBeInTheDocument();
    expect(container.querySelector('.text-accent-text')).not.toBeNull();
  });

  it('renders the terminal replied marker for a SYSTEM-row-anchored REPLY (TASK-5553)', () => {
    // A REPLY whose coalesced range anchored on a system row settles → the
    // system row's responder entry reads 'replied' with purpose='reply'; the
    // strip must surface the marker (it is rendered under the SystemDivider).
    render(
      <ResponderStatusStrip
        statuses={[
          rs('investment_advisor', 'replied', {
            purpose: 'reply',
            responded_at: '2026-05-30T10:00:00Z',
          }),
        ]}
      />,
    );
    expect(screen.getByText('investment_advisor')).toBeInTheDocument();
    expect(screen.getByText('replied')).toBeInTheDocument();
  });
});

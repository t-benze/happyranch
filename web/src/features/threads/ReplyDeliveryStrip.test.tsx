import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReplyDeliveryEntry } from '@/lib/api/types';
import { ReplyDeliveryStrip, replyDeliveryCaption } from './ReplyDeliveryStrip';

const nowMs = Date.parse('2026-05-13T17:46:00Z');

function entry(over: Partial<ReplyDeliveryEntry>): ReplyDeliveryEntry {
  return {
    agent_name: 'ops_lead',
    state: 'queued',
    from_seq: 1,
    through_seq: 4,
    coalesced_message_count: 4,
    started_at: null,
    updated_at: '2026-05-13T17:45:00Z',
    last_terminal_reason: null,
    ...over,
  };
}

describe('ReplyDeliveryStrip (GH-688 Phase 1 Slice C)', () => {
  it('renders nothing for an empty projection (fully-settled thread)', () => {
    const { container } = render(<ReplyDeliveryStrip entries={[]} nowMs={nowMs} />);
    expect(container.firstChild).toBeNull();
  });

  it('queued renders the coalesced count + inclusive range, never a subprocess claim', () => {
    render(
      <ReplyDeliveryStrip
        entries={[entry({ agent_name: 'dev_agent', state: 'queued', coalesced_message_count: 3 })]}
        nowMs={nowMs}
      />,
    );
    const disclosure = screen.getByRole('group', { name: '1 queued delivery' });
    expect(disclosure).not.toHaveAttribute('open');
    fireEvent.click(within(disclosure).getByText('1 queued delivery'));
    const row = screen.getByText('dev_agent');
    expect(row).toHaveClass('break-all');
    expect(screen.getByText('3 messages coalesced · messages 1–4')).toBeInTheDocument();
    expect(screen.queryByText(/replying/)).not.toBeInTheDocument();
  });

  it('running renders elapsed + immutable range', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({
            agent_name: 'ops_lead',
            state: 'running',
            from_seq: 1,
            through_seq: 3,
            started_at: '2026-05-13T17:45:00Z',
          }),
        ]}
        nowMs={nowMs}
      />,
    );
    expect(screen.getByText(/replying 1m/)).toBeInTheDocument();
    expect(screen.getByText(/messages 1–3/)).toBeInTheDocument();
    expect(screen.getByText('ops_lead')).toHaveClass('break-all');
  });

  it('retry_required renders as a diagnostic with the last terminal reason', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({
            agent_name: 'support_lead',
            state: 'retry_required',
            from_seq: 2,
            through_seq: 5,
            coalesced_message_count: 4,
            last_terminal_reason: 'timeout',
          }),
        ]}
        nowMs={nowMs}
      />,
    );
    expect(
      screen.getByText('retry required · messages 2–5 · last: timeout'),
    ).toBeInTheDocument();
    // retry_required is NEVER portrayed as an active subprocess.
    expect(screen.queryByText(/replying/)).not.toBeInTheDocument();
  });

  it('held renders as healthy neutral waiting without stale fault or subprocess copy', () => {
    render(
      <ReplyDeliveryStrip
        entries={[entry({
          agent_name: 'consultant_head',
          state: 'held',
          from_seq: 247,
          through_seq: 249,
          coalesced_message_count: 3,
          last_terminal_reason: 'stale timeout must not render',
        })]}
        nowMs={nowMs}
      />,
    );
    const heldList = screen.getByRole('list', { name: 'Held reply deliveries' });
    expect(heldList).toBeInTheDocument();
    expect(heldList.querySelector('li > span[aria-hidden="true"]')).toHaveClass('bg-feedback-success');
    expect(screen.getByText('waiting for current exchange · messages 247–249')).toBeInTheDocument();
    expect(screen.queryByText(/timeout|retry|required|replying/i)).not.toBeInTheDocument();
  });

  it('renders multiple pairs without key collisions', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({ agent_name: 'alice', state: 'queued' }),
          entry({ agent_name: 'bob', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
        ]}
        nowMs={nowMs}
      />,
    );
    expect(screen.getByText('2 current deliveries')).toBeInTheDocument();
    fireEvent.click(screen.getByText('1 queued delivery'));
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  it('renders the authoritative precedence running, queued, held, retry', () => {
    const { container } = render(
      <ReplyDeliveryStrip entries={[
        entry({ agent_name: 'retry', state: 'retry_required' }),
        entry({ agent_name: 'held', state: 'held' }),
        entry({ agent_name: 'queued', state: 'queued' }),
        entry({ agent_name: 'running', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
      ]} nowMs={nowMs} />,
    );
    fireEvent.click(screen.getByText('1 queued delivery'));
    const text = container.textContent ?? '';
    expect(text.indexOf('running')).toBeLessThan(text.indexOf('queued'));
    expect(text.indexOf('queued')).toBeLessThan(text.indexOf('held'));
    expect(text.indexOf('held')).toBeLessThan(text.indexOf('retry'));
  });

  it('prioritizes concurrent running deliveries and keeps full identities distinguishable', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({ agent_name: 'frontend_engineer_primary', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
          entry({ agent_name: 'frontend_engineer_secondary', state: 'running', started_at: '2026-05-13T17:45:30Z' }),
          entry({ agent_name: 'qa_engineer', state: 'queued' }),
        ]}
        nowMs={nowMs}
      />,
    );
    const active = screen.getByRole('list', { name: 'Active reply deliveries' });
    expect(within(active).getByText('frontend_engineer_primary')).toBeVisible();
    expect(within(active).getByText('frontend_engineer_secondary')).toBeVisible();
    expect(screen.getByRole('group', { name: '1 queued delivery' })).not.toHaveAttribute('open');
  });

  it('uses truthful singular captions and exposes queued detail with native disclosure semantics', () => {
    render(<ReplyDeliveryStrip entries={[entry({ coalesced_message_count: 1, from_seq: 9, through_seq: 9 })]} nowMs={nowMs} />);
    const disclosure = screen.getByRole('group', { name: '1 queued delivery' });
    const summary = within(disclosure).getByText('1 queued delivery');
    summary.focus();
    expect(summary).toHaveFocus();
    fireEvent.click(summary);
    expect(disclosure).toHaveAttribute('open');
    expect(screen.getByText('1 message coalesced · message 9')).toBeInTheDocument();
  });
});

describe('replyDeliveryCaption (shared with the transcript tail)', () => {
  it('queued', () => {
    expect(
      replyDeliveryCaption(
        entry({ agent_name: 'x', state: 'queued', coalesced_message_count: 3 }),
        nowMs,
      ),
    ).toBe('3 messages coalesced · messages 1–4');
  });

  it('running with and without elapsed', () => {
    expect(
      replyDeliveryCaption(
        entry({
          agent_name: 'x',
          state: 'running',
          from_seq: 1,
          through_seq: 3,
          started_at: '2026-05-13T17:45:00Z',
        }),
        nowMs,
      ),
    ).toBe('replying 1m · messages 1–3');
    expect(
      replyDeliveryCaption(
        entry({ agent_name: 'x', state: 'running', started_at: null }),
        nowMs,
      ),
    ).toBe('replying · messages 1–4');
  });

  it('retry_required with and without a stored reason', () => {
    expect(
      replyDeliveryCaption(
        entry({
          agent_name: 'x',
          state: 'retry_required',
          last_terminal_reason: 'timeout',
        }),
        nowMs,
      ),
    ).toBe('retry required · messages 1–4 · last: timeout');
    expect(
      replyDeliveryCaption(
        entry({ agent_name: 'x', state: 'retry_required', last_terminal_reason: null }),
        nowMs,
      ),
    ).toBe('retry required · messages 1–4');
  });
});

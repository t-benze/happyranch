import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

describe('ReplyDeliveryStrip (GH-688 Phase 1 Slice C; TASK-5553 hierarchy)', () => {
  it('renders nothing for an empty projection (fully-settled thread)', () => {
    const { container } = render(<ReplyDeliveryStrip entries={[]} nowMs={nowMs} />);
    expect(container.firstChild).toBeNull();
  });

  it('prioritizes RUNNING deliveries above queued and retry_required', () => {
    render(
      <ReplyDeliveryStrip
        nowMs={nowMs}
        entries={[
          entry({ agent_name: 'zeta', state: 'queued' }),
          entry({ agent_name: 'alpha', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
          entry({ agent_name: 'mid', state: 'retry_required' }),
          entry({ agent_name: 'beta', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
        ]}
      />,
    );
    const rows = screen.getAllByRole('listitem');
    // RUNNING first (alpha, beta — stable by name), then queued (zeta), then retry.
    expect(rows.map((r) => within(r).getByText(/alpha|beta|zeta|mid/).textContent)).toEqual([
      'alpha',
      'beta',
      'zeta',
      'mid',
    ]);
  });

  it('running renders elapsed + inclusive range inline (active delivery priority)', () => {
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
    expect(screen.getByText(/replying 1m · messages 1–3/)).toBeInTheDocument();
  });

  it('queued collapses to a short label; the coalesced detail sits behind the disclosure', async () => {
    const user = userEvent.setup();
    render(
      <ReplyDeliveryStrip
        entries={[entry({ agent_name: 'dev_agent', state: 'queued', coalesced_message_count: 3 })]}
        nowMs={nowMs}
      />,
    );
    const row = screen.getByText('dev_agent');
    expect(row).toBeInTheDocument();
    // Short honest label — NOT an active-subprocess claim.
    expect(screen.getByText('queued')).toBeInTheDocument();
    expect(screen.queryByText(/replying/)).not.toBeInTheDocument();
    // Coalescing detail is collapsed by default.
    expect(screen.queryByText('3 messages coalesced · messages 1–4')).not.toBeInTheDocument();

    // Disclosure is a real button: keyboard/screen-reader accessible.
    const disclosure = screen.getByRole('button', {
      name: 'Show dev_agent reply delivery details',
    });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(disclosure).toHaveAttribute('aria-controls');
    await user.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('3 messages coalesced · messages 1–4')).toBeInTheDocument();
    await user.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('3 messages coalesced · messages 1–4')).not.toBeInTheDocument();
  });

  it('retry_required renders as a collapsed diagnostic; reason behind the disclosure', async () => {
    const user = userEvent.setup();
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
    // Diagnostic short label, never portrayed as an active subprocess.
    expect(screen.getByText('retry required')).toBeInTheDocument();
    expect(screen.queryByText(/replying/)).not.toBeInTheDocument();
    expect(screen.queryByText(/messages 2–5/)).not.toBeInTheDocument();
    const disclosure = screen.getByRole('button', {
      name: 'Show support_lead reply delivery details',
    });
    await user.click(disclosure);
    expect(
      screen.getByText('retry required · messages 2–5 · last: timeout'),
    ).toBeInTheDocument();
  });

  it('renders multiple pairs individually (genuine concurrent work is never merged)', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({ agent_name: 'alice', state: 'queued' }),
          entry({ agent_name: 'bob', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
          entry({ agent_name: 'carol', state: 'queued' }),
        ]}
        nowMs={nowMs}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getByText('carol')).toBeInTheDocument();
    // Each collapsed row gets its own disclosure button.
    expect(
      screen.getAllByRole('button', { name: /reply delivery details/ }),
    ).toHaveLength(2);
  });

  it('never truncates agent identity (no indistinguishable prefixes)', () => {
    render(
      <ReplyDeliveryStrip
        entries={[
          entry({ agent_name: 'engineering_manager', state: 'running', started_at: '2026-05-13T17:45:00Z' }),
          entry({ agent_name: 'engineering_head', state: 'queued' }),
        ]}
        nowMs={nowMs}
      />,
    );
    // Full names present verbatim; no truncate utility on the identity span.
    expect(screen.getByText('engineering_manager')).toBeInTheDocument();
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
    const names = screen.getAllByText(/^engineering_/);
    for (const n of names) {
      expect(n.className).not.toMatch(/truncate/);
    }
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

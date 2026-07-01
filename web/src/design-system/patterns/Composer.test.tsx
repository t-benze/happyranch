import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer, type PendingAttachment } from './Composer';

const NOOP_SEND = vi.fn(async () => {});

beforeEach(() => {
  localStorage.clear();
  NOOP_SEND.mockClear();
});

afterEach(() => {
  localStorage.clear();
});

describe('Composer / drafts', () => {
  it('restores a saved draft on mount', () => {
    localStorage.setItem('happyranch:draft:test-org:THR-001', 'in-progress text');
    // orgSlug is a plain prop now (the pattern no longer calls useOrgSlug);
    // it keys the draft alongside threadId.
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={NOOP_SEND}
      />,
    );
    expect(screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i }).value)
      .toBe('in-progress text');
  });

  it('clears the draft after a successful send', async () => {
    const user = userEvent.setup();
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={NOOP_SEND}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hello');
    // Debounced write — wait the 300ms.
    await new Promise((r) => setTimeout(r, 320));
    expect(localStorage.getItem('happyranch:draft:test-org:THR-001')).toBe('hello');
    await user.keyboard('{Enter}');
    expect(NOOP_SEND).toHaveBeenCalled();
    expect(localStorage.getItem('happyranch:draft:test-org:THR-001')).toBeNull();
  });

  it('preserves the draft when send rejects', async () => {
    const user = userEvent.setup();
    const failingSend = vi.fn().mockRejectedValueOnce(new Error('network down'));
    render(
      <Composer
        agents={[]}
        threadId="THR-002"
        orgSlug="test-org"
        pending={false}
        onSend={failingSend}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'retry-me');
    await new Promise((r) => setTimeout(r, 320));
    expect(localStorage.getItem('happyranch:draft:test-org:THR-002')).toBe('retry-me');
    await user.keyboard('{Enter}');
    expect(failingSend).toHaveBeenCalled();
    // Draft must survive the rejection — both in localStorage and in the textarea.
    expect(localStorage.getItem('happyranch:draft:test-org:THR-002')).toBe('retry-me');
    expect(ta.value).toBe('retry-me');
  });

  it('can send with only an attachment selected', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });
    render(<ControlledComposer onSend={onSend} />);

    await user.upload(screen.getByLabelText(/Attach files/i), file);
    expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /report\.pdf/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    expect(onSend).toHaveBeenCalledWith('', [
      expect.objectContaining({
        file,
      }),
    ]);
  });
});

function ControlledComposer({
  onSend,
}: {
  onSend: (markdown: string, attachments: PendingAttachment[]) => Promise<void>;
}) {
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  return (
    <Composer
      agents={[]}
      threadId="THR-attachment"
      orgSlug="test-org"
      pending={false}
      onSend={onSend}
      attachments={attachments}
      onAttachmentsChange={setAttachments}
    />
  );
}

import type { AgentSummary } from '@/lib/api/agents';

const TEST_AGENTS: AgentSummary[] = [
  { name: 'design_lead',  team: 'design', role: 'manager', executor: 'claude', description: null, repos: {}, system_prompt: '' },
  { name: 'design_dev_1', team: 'design', role: 'worker',  executor: 'claude', description: null, repos: {}, system_prompt: '' },
];

describe('Composer / mentions', () => {
  it('typing @de opens the autocomplete', async () => {
    const user = userEvent.setup();
    render(
      <Composer
        agents={TEST_AGENTS}
        threadId="THR-002"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, '@de');
    expect(await screen.findByText('design_lead')).toBeInTheDocument();
    expect(screen.getByText('design_dev_1')).toBeInTheDocument();
  });

  it('selecting an agent inserts @name into the draft', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <Composer
        agents={TEST_AGENTS}
        threadId="THR-003"
        orgSlug="test-org"
        pending={false}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hi @de');
    await user.keyboard('{Enter}'); // selects first match: design_lead
    expect(ta.value).toBe('hi @design_lead ');
    await user.type(ta, 'please review');
    await user.keyboard('{Enter}');
    // Broadcast model: onSend receives only markdown; no addressedTo second arg.
    expect(onSend).toHaveBeenCalledWith('hi @design_lead please review', []);
  });

  it('send with no mentions falls back to @all', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <Composer
        agents={TEST_AGENTS}
        threadId="THR-004"
        orgSlug="test-org"
        pending={false}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'plain message');
    await user.keyboard('{Enter}');
    // Broadcast model: onSend receives only markdown.
    expect(onSend).toHaveBeenCalledWith('plain message', []);
  });

  it('Shift+Enter inserts a newline and does not send', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <Composer
        agents={TEST_AGENTS}
        threadId="THR-shift"
        orgSlug="test-org"
        pending={false}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'line one');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    await user.type(ta, 'line two');
    expect(ta.value).toBe('line one\nline two');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('literal @all is recognized regardless of agents list', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <Composer
        agents={[]}
        threadId="THR-005"
        orgSlug="test-org"
        pending={false}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'heads-up @all');
    await user.keyboard('{Enter}');
    // Broadcast model: onSend receives only markdown.
    expect(onSend).toHaveBeenCalledWith('heads-up @all', []);
  });
});

/* ------------------------------------------------------------------ */
/*  Abort replies control — threads-only surface                      */
/* ------------------------------------------------------------------ */

describe('Composer / abort replies', () => {
  it('does not render abort button when onAbortReplies is not provided', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
      />,
    );
    expect(
      screen.queryByRole('button', { name: /Abort replies/i }),
    ).not.toBeInTheDocument();
  });

  it('renders abort button next to Send when onAbortReplies is provided', () => {
    const onAbort = vi.fn();
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={false}
        onAbortReplies={onAbort}
      />,
    );
    const abortBtn = screen.getByRole('button', { name: /Abort replies/i });
    const sendBtn = screen.getByRole('button', { name: /^Send$/i });
    // Abort button must be immediately before Send in DOM order
    expect(abortBtn.nextSibling).toBe(sendBtn);
  });

  it('abort button is disabled when hasInFlightResponders is false', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={false}
        onAbortReplies={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Abort replies/i }),
    ).toBeDisabled();
  });

  it('abort button is enabled when hasInFlightResponders is true', () => {
    const onAbort = vi.fn();
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={true}
        onAbortReplies={onAbort}
      />,
    );
    const btn = screen.getByRole('button', { name: /Abort replies/i });
    expect(btn).toBeEnabled();
  });

  it('clicking enabled abort button calls onAbortReplies', async () => {
    const onAbort = vi.fn();
    const user = userEvent.setup();
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={true}
        onAbortReplies={onAbort}
      />,
    );
    await user.click(screen.getByRole('button', { name: /Abort replies/i }));
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('abort button is disabled when Composer is disabled', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        disabled={true}
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={true}
        onAbortReplies={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Abort replies/i }),
    ).toBeDisabled();
  });

  it('abort button shows "Aborting…" when isAborting is true', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-001"
        orgSlug="test-org"
        pending={false}
        onSend={vi.fn(async () => {})}
        hasInFlightResponders={true}
        isAborting={true}
        onAbortReplies={vi.fn()}
      />,
    );
    const btn = screen.getByRole('button', { name: /Aborting…/i });
    expect(btn).toBeDisabled();
  });
});

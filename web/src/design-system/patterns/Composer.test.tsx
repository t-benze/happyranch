import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import corpus from '../../../../protocol/thread-mention-corpus.json';
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

  // TASK-8599 C5.3: selections must be identified by a stable non-metadata id,
  // so the same File selected twice yields two distinct chips and removing one
  // leaves the other.
  it('keeps duplicate identical File selections distinct', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    const file = new File(['dup'], 'dup.txt', { type: 'text/plain' });
    render(<ControlledComposer onSend={onSend} />);

    await user.upload(screen.getByLabelText(/Attach files/i), [file, file]);
    expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(2);
    expect(screen.getAllByText('dup.txt')).toHaveLength(2);

    await user.click(screen.getAllByRole('button', { name: 'Remove attachment' })[0]);
    expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(1);
    expect(screen.getAllByText('dup.txt')).toHaveLength(1);
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

// THR-099 Phase A (founder seq57): the abort-reply control was moved back
// INSIDE the composer input pill as an optional, in-flight-gated trailing action
// next to the circular send button — reversing the earlier "moved OUT" decision.
// THR-099 abort-reply correction: it is now an ICON-ONLY compact square button
// (red stop-square) — no visible "Abort reply"/"Aborting…" text; that copy lives
// only in the accessible name + tooltip.
describe('Composer / abort replies', () => {
  it('renders no abort control by default (prop omitted)', () => {
    render(
      <Composer agents={[]} threadId="THR-a" orgSlug="test-org" onSend={NOOP_SEND} />,
    );
    expect(screen.queryByRole('button', { name: /Abort reply/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Aborting/i })).toBeNull();
  });

  it('renders the abort control ONLY while replies are in flight (active)', () => {
    const onAbort = vi.fn();
    const { rerender } = render(
      <Composer
        agents={[]}
        threadId="THR-b"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: false, isPending: false, onAbort }}
      />,
    );
    // Inactive → not rendered.
    expect(screen.queryByRole('button', { name: /Abort reply/i })).toBeNull();
    // Active → rendered, enabled, labelled "Abort reply".
    rerender(
      <Composer
        agents={[]}
        threadId="THR-b"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: false, onAbort }}
      />,
    );
    const btn = screen.getByRole('button', { name: /Abort reply/i });
    expect(btn).toBeEnabled();
    // Icon-only: an accessible name from aria-label, but NO visible text on
    // screen, and a rendered (stop-square) icon.
    expect(btn.textContent?.trim()).toBe('');
    expect(btn.querySelector('svg')).not.toBeNull();
  });

  it('is icon-only — carries no visible "Abort reply"/"Aborting…" text', () => {
    const { rerender } = render(
      <Composer
        agents={[]}
        threadId="THR-icon"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: false, onAbort: vi.fn() }}
      />,
    );
    // Accessible via role+name, but the literal copy is not visible text.
    expect(screen.getByRole('button', { name: /Abort reply/i }).textContent?.trim()).toBe('');
    expect(screen.queryByText(/Abort reply/i)).toBeNull();
    rerender(
      <Composer
        agents={[]}
        threadId="THR-icon"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: true, onAbort: vi.fn() }}
      />,
    );
    expect(screen.getByRole('button', { name: /Aborting…/i }).textContent?.trim()).toBe('');
    expect(screen.queryByText(/Aborting…/i)).toBeNull();
  });

  it('shows "Aborting…" and disables the control while pending', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-c"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: true, onAbort: vi.fn() }}
      />,
    );
    const btn = screen.getByRole('button', { name: /Aborting…/i });
    expect(btn).toBeDisabled();
    expect(screen.queryByRole('button', { name: /^Abort reply$/i })).toBeNull();
  });

  it('sits inside the input pill, to the LEFT of the circular send button', () => {
    render(
      <Composer
        agents={[]}
        threadId="THR-d"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: false, onAbort: vi.fn() }}
      />,
    );
    const abort = screen.getByRole('button', { name: /Abort reply/i });
    const send = screen.getByRole('button', { name: /^Send$/i });
    // Same flex pill container holds both, abort before send in DOM order.
    expect(abort.parentElement).toBe(send.parentElement);
    expect(
      abort.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('fires onAbort when clicked', async () => {
    const user = userEvent.setup();
    const onAbort = vi.fn();
    render(
      <Composer
        agents={[]}
        threadId="THR-e"
        orgSlug="test-org"
        onSend={NOOP_SEND}
        abortReplies={{ active: true, isPending: false, onAbort }}
      />,
    );
    await user.click(screen.getByRole('button', { name: /Abort reply/i }));
    expect(onAbort).toHaveBeenCalledTimes(1);
  });
});

describe('Composer / explicit prose caret', () => {
  it.each(['mail@design', '`@design`', '```\n@design', '\\@design', '@design. suffix'])('suppresses literal/terminated hint at %j', async (body) => {
    render(<Composer agents={TEST_AGENTS} threadId="THR-lexer" orgSlug="test-org" onSend={NOOP_SEND} />);
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', {name:/compose/i});
    fireEvent.change(ta,{target:{value:body}});
    const caret = body.startsWith('@design.') ? 8 : body.indexOf('@design') + 7;
    ta.focus(); ta.setSelectionRange(caret,caret); fireEvent.click(ta);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(ta).toHaveValue(body); expect(ta).toHaveFocus();
    expect(NOOP_SEND).not.toHaveBeenCalled();
  });
  it('quote-exit hint insertion preserves suffix, draft, caret and focus', async () => {
    const user=userEvent.setup();
    render(<Composer agents={TEST_AGENTS} threadId="THR-lexer" orgSlug="test-org" onSend={NOOP_SEND} />);
    const ta=screen.getByRole<HTMLTextAreaElement>('textbox',{name:/compose/i});
    const prefix=' > ```\n> @design_dev_1\n';
    fireEvent.change(ta,{target:{value:prefix+'@de suffix'}});
    ta.focus(); ta.setSelectionRange(prefix.length+3,prefix.length+3); fireEvent.click(ta);
    await user.click(await screen.findByRole('option',{name:/design_lead/}));
    expect(ta).toHaveValue(prefix+'@design_lead  suffix');
    expect(ta).toHaveFocus();
    expect([ta.selectionStart,ta.selectionEnd]).toEqual([prefix.length+13,prefix.length+13]);
    expect(NOOP_SEND).not.toHaveBeenCalled();
  });
});

// C19: actual Composer/MentionTextarea, shared accepted occurrence input.
// Exact insertion/focus/caret catches first-occurrence and whole-token replacement;
// mounted browser events only, never real server/storage/IME proof.
describe('THR310 repeated occurrence insertion', () => {
  it.each(corpus.cases.filter(row => ['L49','L50'].includes(row.id)))('$id retains suffix and focus', async row => {
    const user=userEvent.setup();
    localStorage.setItem('happyranch:draft:mention-qa:THR-REPEAT',row.input);
    render(<Composer agents={[{name:'case_worker',addressable_name:'Current',team:null}]} threadId="THR-REPEAT" orgSlug="mention-qa" pending={false} onSend={NOOP_SEND} />);
    const ta=screen.getByRole<HTMLTextAreaElement>('textbox',{name:/compose/i});
    ta.focus();ta.setSelectionRange(row.caret_utf16!,row.caret_utf16!);fireEvent.click(ta);
    await screen.findByRole('option');
    fireEvent.keyDown(ta,{key:'Enter',code:'Enter',isComposing:true});
    expect(ta.value).toBe(row.input);expect(NOOP_SEND).not.toHaveBeenCalled();
    await user.keyboard('{Enter}');
    expect(ta.value).toBe(row.id==='L49'?'@Current @Current rent suffix':'@Current @Current  suffix');
    expect([ta.selectionStart,ta.selectionEnd]).toEqual([18,18]);expect(ta).toHaveFocus();expect(NOOP_SEND).not.toHaveBeenCalled();
  });
});

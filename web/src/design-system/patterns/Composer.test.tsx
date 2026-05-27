import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from './Composer';

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
    localStorage.setItem('grassland:draft:test-org:THR-001', 'in-progress text');
    // Note: useThreadDraft needs orgSlug. Composer reads it via useOrgSlug()
    // (the codebase already wires <OrgProvider> in routes; tests need it too).
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-001"
          pending={false}
          onSend={NOOP_SEND}
        />
      </WithOrgSlug>,
    );
    expect(screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i }).value)
      .toBe('in-progress text');
  });

  it('clears the draft after a successful send', async () => {
    const user = userEvent.setup();
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-001"
          pending={false}
          onSend={NOOP_SEND}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hello');
    // Debounced write — wait the 300ms.
    await new Promise((r) => setTimeout(r, 320));
    expect(localStorage.getItem('grassland:draft:test-org:THR-001')).toBe('hello');
    await user.keyboard('{Enter}');
    expect(NOOP_SEND).toHaveBeenCalled();
    expect(localStorage.getItem('grassland:draft:test-org:THR-001')).toBeNull();
  });

  it('preserves the draft when send rejects', async () => {
    const user = userEvent.setup();
    const failingSend = vi.fn().mockRejectedValueOnce(new Error('network down'));
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-002"
          pending={false}
          onSend={failingSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'retry-me');
    await new Promise((r) => setTimeout(r, 320));
    expect(localStorage.getItem('grassland:draft:test-org:THR-002')).toBe('retry-me');
    await user.keyboard('{Enter}');
    expect(failingSend).toHaveBeenCalled();
    // Draft must survive the rejection — both in localStorage and in the textarea.
    expect(localStorage.getItem('grassland:draft:test-org:THR-002')).toBe('retry-me');
    expect(ta.value).toBe('retry-me');
  });
});

// Helper: wraps children in a StaticOrgProvider so Composer's
// useOrgSlug() resolves to a known slug under jsdom.
import { StaticOrgProvider } from '@/lib/orgSlug';
function WithOrgSlug({ slug, children }: { slug: string; children: React.ReactNode }) {
  return <StaticOrgProvider slug={slug}>{children}</StaticOrgProvider>;
}

import type { AgentSummary } from '@/lib/api/agents';

const TEST_AGENTS: AgentSummary[] = [
  { name: 'design_lead',  team: 'design', role: 'manager', executor: 'claude', tier: 'green', description: null, scorecard: null, avg_confidence: null },
  { name: 'design_dev_1', team: 'design', role: 'worker',  executor: 'claude', tier: 'green', description: null, scorecard: null, avg_confidence: null },
];

describe('Composer / mentions', () => {
  it('typing @de opens the autocomplete', async () => {
    const user = userEvent.setup();
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-002"
          pending={false}
          onSend={vi.fn(async () => {})}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, '@de');
    expect(await screen.findByText('design_lead')).toBeInTheDocument();
    expect(screen.getByText('design_dev_1')).toBeInTheDocument();
  });

  it('selecting an agent inserts @name and sends with addressedTo set', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-003"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hi @de');
    await user.keyboard('{Enter}'); // selects first match: design_lead
    expect(ta.value).toBe('hi @design_lead ');
    await user.type(ta, 'please review');
    await user.keyboard('{Enter}');
    expect(onSend).toHaveBeenCalledWith('hi @design_lead please review', ['design_lead']);
  });

  it('send with no mentions falls back to @all', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-004"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'plain message');
    await user.keyboard('{Enter}');
    expect(onSend).toHaveBeenCalledWith('plain message', ['@all']);
  });

  it('Shift+Enter inserts a newline and does not send', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-shift"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
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
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-005"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'heads-up @all');
    await user.keyboard('{Enter}');
    expect(onSend).toHaveBeenCalledWith('heads-up @all', ['@all']);
  });
});

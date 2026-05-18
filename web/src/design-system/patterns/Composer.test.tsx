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
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(NOOP_SEND).toHaveBeenCalled();
    expect(localStorage.getItem('grassland:draft:test-org:THR-001')).toBeNull();
  });
});

// Helper: wraps children in a StaticOrgProvider so Composer's
// useOrgSlug() resolves to a known slug under jsdom.
import { StaticOrgProvider } from '@/lib/orgSlug';
function WithOrgSlug({ slug, children }: { slug: string; children: React.ReactNode }) {
  return <StaticOrgProvider slug={slug}>{children}</StaticOrgProvider>;
}

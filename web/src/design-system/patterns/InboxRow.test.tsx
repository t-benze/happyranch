import { describe, expect, test, vi } from 'vitest';
import { createEvent, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { InboxRow } from './InboxRow';

describe.each(['default', 'thread'] as const)('InboxRow — %s navigation', (layout) => {
  test('keeps a native link, plain-click/Enter selection and no pin control', async () => {
    const onSelect = vi.fn();
    render(<InboxRow threadId="THR-10" subject="Navigate" status="open" needsYou={false} active={false} layout={layout} href="/threads/THR-10" onSelect={onSelect} />);
    const row = screen.getByRole('link', { name: /Navigate/ });
    expect(row).toHaveAttribute('href', '/threads/THR-10');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    await userEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(1);
    row.focus();
    expect(row).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  test('leaves modified and non-primary clicks to native anchor behavior', () => {
    const onSelect = vi.fn();
    render(<InboxRow threadId="THR-10" subject="Navigate" status="open" needsYou={false} active={false} layout={layout} href="#thread-10" onSelect={onSelect} />);
    const row = screen.getByRole('link');
    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { button: 2 }]) {
      const event = createEvent.click(row, init);
      fireEvent(row, event);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(onSelect).not.toHaveBeenCalled();
  });
});

/**
 * THREADS-05 — the inbox row maps the honest, data-derivable subset of the
 * Direction-A semantic pill set: an `active` pill for open threads, a `done`
 * pill for archived (terminal) threads, and an additive `from dream` pill when
 * the thread was composed from a dream (`composed_from_dream_id`). The
 * non-derivable states (waiting-on-you / review / merged / live / idle) are
 * intentionally absent — no backing field exists on the thread-list payload.
 */
describe('InboxRow — semantic status pills (THREADS-05)', () => {
  test('open thread renders the "active" pill', () => {
    render(
      <InboxRow
        threadId="THR-001"
        subject="Launch plan"
        status="open"
        needsYou={false}
        active={false}
        href="#"
      />,
    );
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.queryByText('archived')).not.toBeInTheDocument();
  });

  test('archived thread renders the "done" pill (design vocabulary, not "archived")', () => {
    render(
      <InboxRow
        threadId="THR-002"
        subject="Closed topic"
        status="archived"
        needsYou={false}
        active={false}
        href="#"
      />,
    );
    expect(screen.getByText('done')).toBeInTheDocument();
    expect(screen.queryByText('archived')).not.toBeInTheDocument();
    expect(screen.queryByText('active')).not.toBeInTheDocument();
  });

  test('dream-originated thread renders an additive "from dream" pill', () => {
    render(
      <InboxRow
        threadId="THR-003"
        subject="Dream reflection"
        status="open"
        needsYou={false}
        active={false}
        fromDream
        href="#"
      />,
    );
    // Additive: the status pill still renders alongside the dream pill.
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('from dream')).toBeInTheDocument();
    expect(screen.getByLabelText(/Dream-originated/)).toBeInTheDocument();
  });

  test('non-dream thread omits the "from dream" pill', () => {
    render(
      <InboxRow
        threadId="THR-004"
        subject="Ordinary thread"
        status="open"
        needsYou={false}
        active={false}
        href="#"
      />,
    );
    expect(screen.queryByText('from dream')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Dream-originated/)).not.toBeInTheDocument();
  });

  test('needsYou renders the leading "needs you" marker', () => {
    render(
      <InboxRow
        threadId="THR-005"
        subject="Awaiting reply"
        status="open"
        needsYou
        active={false}
        href="#"
      />,
    );
    expect(screen.getByLabelText('needs you')).toBeInTheDocument();
  });
});

/**
 * THR-099 — the `layout="thread"` row model is id-first and two-line: a
 * status-driven leading dot (open=green accent, archived=grey), the mono thread
 * id, the serif subject, the status BADGE routed through the shared semanticTone
 * vocabulary (open→"open"/blue, archived→"archived"/grey — NOT active/done), an
 * inline `from dream` pill, and `last <last_speaker>`. The default layout is
 * left untouched (its own describe above still asserts active/done).
 */
describe('InboxRow — thread layout (THR-099 id-first row)', () => {
  test('open thread renders the "open" badge (semanticTone vocab, not "active")', () => {
    render(
      <InboxRow
        threadId="THR-021"
        subject="Settings dialog parity"
        status="open"
        needsYou={false}
        active={false}
        layout="thread"
        href="#"
      />,
    );
    expect(screen.getByText('open')).toHaveClass('text-info', 'bg-info-soft');
    expect(screen.queryByText('active')).not.toBeInTheDocument();
    // mono thread id leads the row.
    expect(screen.getByText('THR-021')).toBeInTheDocument();
  });

  test('archived thread renders the "archived" badge (not "done")', () => {
    render(
      <InboxRow
        threadId="THR-011"
        subject="Artifacts folder support"
        status="archived"
        needsYou={false}
        active={false}
        layout="thread"
        href="#"
      />,
    );
    expect(screen.getByText('archived')).toHaveClass('text-status-archived', 'border', 'border-border-default', 'bg-transparent');
    expect(screen.queryByText('done')).not.toBeInTheDocument();
    expect(screen.queryByText('active')).not.toBeInTheDocument();
  });

  test('renders inline "last <last_speaker>" from the honest last_speaker', () => {
    render(
      <InboxRow
        threadId="THR-020"
        subject="Dynamic workflow planning"
        status="open"
        needsYou={false}
        active={false}
        layout="thread"
        lastSpeaker={{ name: 'dev_agent', role: 'worker' }}
        href="#"
      />,
    );
    expect(screen.getByText('dev_agent')).toBeInTheDocument();
  });

  test('dream-originated thread keeps the additive "from dream" pill inline', () => {
    render(
      <InboxRow
        threadId="THR-006"
        subject="KB consolidation pass"
        status="open"
        needsYou={false}
        active={false}
        layout="thread"
        fromDream
        href="#"
      />,
    );
    expect(screen.getByText('from dream')).toBeInTheDocument();
    expect(screen.getByLabelText(/Dream-originated/)).toBeInTheDocument();
    // Additive: the status badge still renders alongside the dream pill.
    expect(screen.getByText('open')).toHaveClass('text-info', 'bg-info-soft');
  });

  test('renders the bounded list participant projection on its second line', () => {
    render(
      <InboxRow
        threadId="THR-006"
        subject="Participant visibility"
        status="open"
        needsYou={false}
        active={false}
        layout="thread"
        participants={['engineering_manager', 'dev_agent']}
        href="#"
      />,
    );
    expect(screen.getByText('engineering_manager · dev_agent')).toBeInTheDocument();
  });

  test('uses flush grouped-row geometry while the default consumer keeps its card shell', () => {
    const { rerender } = render(
      <InboxRow threadId="THR-007" subject="Long grouped row" status="open" needsYou={false} active={false} layout="thread" href="#" participants={['agent']} />,
    );
    expect(screen.getByRole('link')).not.toHaveClass('rounded-sm', 'border', 'shadow-pasture-sm');
    expect(screen.getByText('agent')).toHaveClass('ml-[18px]');
    rerender(<InboxRow threadId="THR-008" subject="Default row" status="open" needsYou={false} active={false} href="#" />);
    expect(screen.getByRole('link')).toHaveClass('rounded-sm', 'border', 'shadow-pasture-sm');
  });

  test('keeps semantic row colors immediate when the theme changes', () => {
    render(
      <InboxRow threadId="THR-009" subject="Theme switch" status="open" needsYou={false} active={false} layout="thread" href="#" />,
    );
    expect(screen.getByRole('link')).toHaveClass('bg-surface');
    expect(screen.getByRole('link')).not.toHaveClass('transition-colors');
  });
});

describe('InboxRow dream badge accessible name (THR-118 W3a)', () => {
  test.each(['default', 'thread'] as const)('%s layout: labels.dreamBadge names the badge; omitted keeps English', (layout) => {
    const { rerender } = render(
      <InboxRow
        threadId="THR-007"
        subject="Dream reflection"
        status="open"
        needsYou={false}
        active={false}
        fromDream
        layout={layout}
        href="#"
        labels={{ fromDream: '来自梦境', dreamBadge: '源自梦境' }}
      />,
    );
    const badge = screen.getByRole('img', { name: '源自梦境' });
    expect(screen.queryByRole('img', { name: 'Dream-originated' })).not.toBeInTheDocument();
    rerender(
      <InboxRow
        threadId="THR-007"
        subject="Dream reflection"
        status="open"
        needsYou={false}
        active={false}
        fromDream
        layout={layout}
        href="#"
      />,
    );
    expect(screen.getByRole('img', { name: 'Dream-originated' })).toBe(badge);
  });
});

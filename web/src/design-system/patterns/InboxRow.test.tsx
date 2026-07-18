import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { InboxRow } from './InboxRow';

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
 * THR-099 — the `layout="thread"` row model is id-first and single-line: a
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
    expect(screen.getByText('open')).toBeInTheDocument();
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
    expect(screen.getByText('archived')).toBeInTheDocument();
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
    expect(screen.getByText('open')).toBeInTheDocument();
  });
});

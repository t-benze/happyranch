/**
 * Deterministic UI tests for the Execution status card (TASK-5522).
 *
 * The card renders the server-derived `work_status` envelope field. Tests use
 * API fixture timestamps and assert role/text content — not CSS details. The
 * card must visibly differentiate heartbeat/liveness from actual agent-written
 * updates, and must never imply activity where only a heartbeat was observed.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ExecutionStatusCard } from './TaskDetailPage';
import type { WorkStatusResponse } from '@/lib/api/types';

const FIXED_START = '2026-08-23T13:06:02Z';
const FIXED_HEARTBEAT = '2026-08-23T13:38:57Z';
const FIXED_PROGRESS = '2026-08-23T13:36:12Z';

function local(iso: string): string {
  // The card formats with toLocaleString(); mirror it so assertions are
  // deterministic regardless of host timezone/locale.
  return new Date(iso).toLocaleString();
}

function fixture(overrides: Partial<WorkStatusResponse>): WorkStatusResponse {
  return {
    applicable: true,
    state: 'newly_started',
    label: 'Newly started — awaiting first update',
    reason: null,
    session_start_ts: FIXED_START,
    heartbeat: { timestamp: FIXED_HEARTBEAT, freshness: 'fresh' },
    latest_progress: null,
    ...overrides,
  };
}

/** (a) newly-started / awaiting-first-receipt */
function newlyStarted(): WorkStatusResponse {
  return fixture({});
}

/** (b) recent substantive progress */
function recentProgress(): WorkStatusResponse {
  return fixture({
    state: 'recent_progress',
    label: 'Recent update recorded',
    latest_progress: {
      timestamp: FIXED_PROGRESS,
      message: 'Phase 3 of 6: tests passing',
      agent: 'dev_agent',
    },
  });
}

/** (c) stale-but-alive, no receipt */
function staleNoReceipt(): WorkStatusResponse {
  return fixture({
    state: 'stale_no_receipt',
    label: 'Stale-but-alive — no substantive update recorded',
    session_start_ts: '2026-08-23T12:40:00Z',
  });
}

/** (d) stale-but-alive, old receipt */
function staleOldReceipt(): WorkStatusResponse {
  return fixture({
    state: 'stale_old_receipt',
    label: 'Stale-but-alive — last update older than 5 minutes',
    session_start_ts: '2026-08-23T12:00:00Z',
    latest_progress: {
      timestamp: '2026-08-23T13:00:00Z',
      message: 'old milestone',
      agent: 'dev_agent',
    },
  });
}

/** terminal → explicit non-applicable, never implies a live agent */
function notApplicable(): WorkStatusResponse {
  return fixture({
    applicable: false,
    state: 'not_applicable',
    label: 'Not applicable',
    reason: 'terminal',
    session_start_ts: null,
    heartbeat: { timestamp: null, freshness: 'unavailable' },
    latest_progress: null,
  });
}

describe('ExecutionStatusCard', () => {
  test('newly-started: renders state, start, fresh heartbeat, and the explicit no-update line', () => {
    render(<ExecutionStatusCard status={newlyStarted()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card).toBeTruthy();
    expect(card.textContent).toContain('Newly started — awaiting first update');
    expect(card.textContent).toContain('No substantive update recorded');
    // The heartbeat is its own labeled observation with a freshness suffix…
    expect(card.textContent).toContain('(fresh)');
    // …and the start timestamp is present.
    expect(card.textContent).toContain(local(FIXED_START));
  });

  test('recent progress: renders the receipt time AND the agent-written message', () => {
    render(<ExecutionStatusCard status={recentProgress()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain('Recent update recorded');
    expect(card.textContent).toContain('Phase 3 of 6: tests passing');
    expect(card.textContent).toContain(local(FIXED_PROGRESS));
    expect(card.textContent).not.toContain('No substantive update recorded');
  });

  test('stale-but-alive without receipt: actionable stale label + no-update line', () => {
    render(<ExecutionStatusCard status={staleNoReceipt()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain(
      'Stale-but-alive — no substantive update recorded',
    );
    expect(card.textContent).toContain('No substantive update recorded');
    expect(card.textContent).toContain('(fresh)'); // liveness still observed
  });

  test('stale-but-alive with old receipt: stale label + old content', () => {
    render(<ExecutionStatusCard status={staleOldReceipt()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain(
      'Stale-but-alive — last update older than 5 minutes',
    );
    expect(card.textContent).toContain('old milestone');
    expect(card.textContent).toContain(local('2026-08-23T13:00:00Z'));
  });

  test('terminal: explicit not-applicable, no heartbeat implying liveness', () => {
    render(<ExecutionStatusCard status={notApplicable()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain('Not applicable');
    expect(card.textContent).toContain('terminal');
    expect(card.textContent).not.toContain('(fresh)');
    expect(card.textContent).not.toContain('(stale)');
    expect(card.textContent).not.toContain('No substantive update recorded');
  });

  test('heartbeat never renders as a substantive update', () => {
    // Fresh heartbeat + no receipt: the Update row must be the explicit
    // no-update line — never a message built from the heartbeat timestamp.
    render(<ExecutionStatusCard status={newlyStarted()} />);
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain('No substantive update recorded');
    // The heartbeat timestamp appears only on the heartbeat row, never as an
    // Update-row message.
    expect(card.textContent).not.toMatch(new RegExp(`Update[^N]*${local(FIXED_HEARTBEAT)}`));
  });

  test('stale heartbeat freshness is labeled honestly', () => {
    render(
      <ExecutionStatusCard
        status={fixture({
          state: 'heartbeat_stale',
          label: 'Heartbeat stale — liveness not observed recently',
          heartbeat: { timestamp: FIXED_HEARTBEAT, freshness: 'stale' },
        })}
      />,
    );
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain('Heartbeat stale');
    expect(card.textContent).toContain('(stale)');
  });

  test('unavailable content is surfaced honestly, not fabricated', () => {
    render(
      <ExecutionStatusCard
        status={fixture({
          state: 'recent_progress',
          label: 'Recent update recorded',
          latest_progress: {
            timestamp: FIXED_PROGRESS,
            message: null, // absent/malformed stored content
            agent: 'dev_agent',
          },
        })}
      />,
    );
    const card = screen.getByRole('complementary', { name: 'Execution status' });
    expect(card.textContent).toContain('(content unavailable)');
  });
});

import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuditRow } from './AuditRow';
import type { AuditEntry } from '@/lib/api/types';

const baseEntry: AuditEntry = {
  id: 1,
  task_id: 'TASK-12',
  session_id: 'sess-abc',
  agent: 'content_writer',
  action: 'completion_report',
  payload: { status: 'completed', tokens: 1200 },
  created_at: '2026-05-19T11:00:00Z',
};

function wrap(ui: JSX.Element): JSX.Element {
  return <MemoryRouter>{ui}</MemoryRouter>;
}

describe('AuditRow', () => {
  test('renders a collapsed row by default', () => {
    render(wrap(<AuditRow entry={baseEntry} density="compact" />));
    expect(screen.getByText('content_writer')).toBeInTheDocument();
    expect(screen.getByText('completion_report')).toBeInTheDocument();
    expect(screen.queryByText(/"tokens"/)).not.toBeInTheDocument();
  });

  test('toggles expansion on click', async () => {
    const user = userEvent.setup();
    render(wrap(<AuditRow entry={baseEntry} density="compact" />));
    await user.click(screen.getByRole('button', { name: /toggle/i }));
    expect(screen.getByText(/"tokens"/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /toggle/i }));
    expect(screen.queryByText(/"tokens"/)).not.toBeInTheDocument();
  });

  test('renders task deep-link only when task_id present', () => {
    render(
      wrap(<AuditRow entry={{ ...baseEntry, task_id: null }} density="compact" />),
    );
    expect(screen.queryByText(/TASK-/)).not.toBeInTheDocument();
  });
});

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
  timestamp: '2026-05-19T11:00:00Z',
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

describe('AuditRow — job_* actions', () => {
  const jobEntry: AuditEntry = {
    id: 2,
    task_id: 'TASK-42',
    session_id: null,
    agent: 'eng_agent',
    action: 'job_submitted',
    payload: {
      // Audit-logger still emits the historical key for back-compat.
      script_request_id: 'JOB-001',
      title: 'Deploy to staging',
    },
    timestamp: '2026-05-19T12:00:00Z',
  };

  test('renders job id as a link when jobsBasePath is provided', () => {
    render(
      wrap(
        <AuditRow
          entry={jobEntry}
          density="compact"
          jobsBasePath="/orgs/test-org/jobs"
        />,
      ),
    );
    const link = screen.getByRole('link', { name: 'JOB-001' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/orgs/test-org/jobs/JOB-001');
  });

  test('renders title inline for job_submitted', () => {
    render(
      wrap(
        <AuditRow
          entry={jobEntry}
          density="compact"
          jobsBasePath="/orgs/test-org/jobs"
        />,
      ),
    );
    expect(screen.getByText(/submitted/)).toBeInTheDocument();
    expect(screen.getByText(/Deploy to staging/)).toBeInTheDocument();
  });

  test('renders reason inline for job_rejected', () => {
    render(
      wrap(
        <AuditRow
          entry={{
            ...jobEntry,
            action: 'job_rejected',
            payload: {
              script_request_id: 'JOB-002',
              reason: 'dangerous command',
            },
          }}
          density="compact"
          jobsBasePath="/orgs/test-org/jobs"
        />,
      ),
    );
    expect(screen.getByText(/rejected/)).toBeInTheDocument();
    expect(screen.getByText(/dangerous command/)).toBeInTheDocument();
  });

  test('renders exit code + duration for job_run_completed', () => {
    render(
      wrap(
        <AuditRow
          entry={{
            ...jobEntry,
            action: 'job_run_completed',
            payload: {
              script_request_id: 'JOB-003',
              exit_code: 0,
              duration_ms: 1234,
            },
          }}
          density="compact"
          jobsBasePath="/orgs/test-org/jobs"
        />,
      ),
    );
    expect(screen.getByText(/completed/)).toBeInTheDocument();
    expect(screen.getByText(/exit=0/)).toBeInTheDocument();
    expect(screen.getByText(/1234ms/)).toBeInTheDocument();
  });

  test('falls back to plain text for non-job actions', () => {
    render(
      wrap(
        <AuditRow
          entry={baseEntry}
          density="compact"
          jobsBasePath="/orgs/test-org/jobs"
        />,
      ),
    );
    // Non-job action: still renders action name, no job link
    expect(screen.getByText('completion_report')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /JOB-/ })).not.toBeInTheDocument();
  });
});

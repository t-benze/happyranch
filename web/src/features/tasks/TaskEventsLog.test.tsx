import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { TaskEvent } from '@/lib/api/types';
import { TaskEventsLog } from './TaskEventsLog';

const { tailCallbacks } = vi.hoisted(() => ({
  tailCallbacks: new Set<(event: TaskEvent) => void>(),
}));

vi.mock('@/hooks/tasks', () => ({
  useTaskTailSSE: (_taskId: string, onEvent: (event: TaskEvent) => void) => {
    tailCallbacks.add(onEvent);
  },
}));

const EVENT: TaskEvent = {
  timestamp: '2026-01-01T00:30:00Z',
  type: 'audit',
  action: 'task_started',
  agent: 'dev_agent',
  payload: { source: 'task-tail' },
};

function emit(event: TaskEvent): void {
  act(() => {
    for (const callback of tailCallbacks) callback(event);
  });
}

describe('TaskEventsLog', () => {
  beforeEach(() => {
    tailCallbacks.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows waiting before the task-tail subscription emits an event', () => {
    render(<TaskEventsLog taskId="TASK-LOCAL-TIME" />);

    expect(screen.getByText('Waiting for events…')).toBeInTheDocument();
  });

  test('renders task-tail timestamps in the viewer local date/time instead of raw UTC', async () => {
    vi.spyOn(Date.prototype, 'toLocaleString').mockImplementation(function (this: Date) {
      return new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/Los_Angeles',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
      }).format(this);
    });
    const user = userEvent.setup();
    render(<TaskEventsLog taskId="TASK-LOCAL-TIME" />);

    emit(EVENT);

    expect(screen.getByText('12/31/2025, 04:30:00 PM')).toHaveClass('font-mono');
    expect(screen.queryByText(EVENT.timestamp)).not.toBeInTheDocument();
    expect(screen.getByText('task_started')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /dev_agent/i })).toBeInTheDocument();

    const row = screen.getByRole('button', { name: /task_started/i });
    expect(row).toHaveAttribute('aria-expanded', 'false');
    await user.click(row);
    expect(screen.getByText(/task-tail/)).toBeInTheDocument();
    expect(row).toHaveAttribute('aria-expanded', 'true');
    await user.click(row);
    expect(screen.queryByText(/task-tail/)).not.toBeInTheDocument();

    emit(EVENT);
    expect(screen.getAllByText('task_started')).toHaveLength(1);
  });
});

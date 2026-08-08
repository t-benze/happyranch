import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { TaskEvent } from '@/lib/api/types';
import { TaskEventsLog } from './TaskEventsLog';

type TailSubscription = {
  onEvent: (event: TaskEvent) => void;
  onOpen?: () => void;
  onError?: () => void;
};

const { tailSubscriptions } = vi.hoisted(() => ({
  tailSubscriptions: new Map<string, TailSubscription[]>(),
}));

vi.mock('@/hooks/tasks', () => ({
  useTaskTailSSE: (
    taskId: string,
    onEvent: (event: TaskEvent) => void,
    callbacks?: Omit<TailSubscription, 'onEvent'>,
  ) => {
    const subscriptions = tailSubscriptions.get(taskId) ?? [];
    tailSubscriptions.set(taskId, [...subscriptions, { onEvent, ...callbacks }]);
  },
}));

const EVENT: TaskEvent = {
  timestamp: '2026-01-01T00:30:00Z',
  type: 'audit',
  action: 'task_started',
  agent: 'dev_agent',
  payload: { source: 'task-tail' },
};

function currentSubscription(taskId: string): TailSubscription {
  const subscriptions = tailSubscriptions.get(taskId);
  if (!subscriptions?.length) throw new Error(`No tail subscription for ${taskId}`);
  return subscriptions.at(-1) as TailSubscription;
}

function emit(taskId: string, event: TaskEvent): void {
  act(() => {
    currentSubscription(taskId).onEvent(event);
  });
}

describe('TaskEventsLog', () => {
  beforeEach(() => {
    tailSubscriptions.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows task-scoped loading, empty, and subscription-error states', () => {
    render(<TaskEventsLog taskId="TASK-LOCAL-TIME" />);

    expect(screen.getByText('Loading events for TASK-LOCAL-TIME…')).toBeInTheDocument();

    act(() => currentSubscription('TASK-LOCAL-TIME').onOpen?.());
    expect(screen.getByText('No events for TASK-LOCAL-TIME yet.')).toBeInTheDocument();

    act(() => currentSubscription('TASK-LOCAL-TIME').onError?.());
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Unable to load events for TASK-LOCAL-TIME.',
    );
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

    emit('TASK-LOCAL-TIME', EVENT);

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

    emit('TASK-LOCAL-TIME', EVENT);
    expect(screen.getAllByText('task_started')).toHaveLength(1);
  });
});

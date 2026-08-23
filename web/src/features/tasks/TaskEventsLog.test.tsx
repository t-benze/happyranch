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

  test('renders valid timestamps for synthetic terminal events (completed/failed/escalated) without falling back', () => {
    vi.spyOn(Date.prototype, 'toLocaleString').mockReturnValue('FORMATTED');
    render(<TaskEventsLog taskId="TASK-SYNTH" />);

    const completed: TaskEvent = {
      type: 'task_complete',
      outcome: 'completed',
      synthesized: true,
      timestamp: '2026-08-23T04:46:00.123456+00:00',
    };
    const failed: TaskEvent = {
      type: 'task_failed',
      outcome: 'failed',
      synthesized: true,
      timestamp: '2026-08-23T05:00:00.000000+00:00',
    };
    const escalated: TaskEvent = {
      type: 'task_blocked',
      outcome: 'escalated',
      synthesized: true,
      timestamp: '2026-08-23T05:30:00.000000+00:00',
    };

    emit('TASK-SYNTH', completed);
    emit('TASK-SYNTH', failed);
    emit('TASK-SYNTH', escalated);

    expect(screen.getAllByText('FORMATTED')).toHaveLength(3);
    expect(screen.queryByText('Time unavailable')).not.toBeInTheDocument();
    expect(screen.getByText('task_complete')).toBeInTheDocument();
    expect(screen.getByText('task_failed')).toBeInTheDocument();
    expect(screen.getByText('task_blocked')).toBeInTheDocument();
  });

  test('renders a stable fallback instead of Invalid Date when the timestamp is missing', () => {
    vi.spyOn(Date.prototype, 'toLocaleString').mockReturnValue('FORMATTED');
    render(<TaskEventsLog taskId="TASK-MISSING" />);

    // Real legacy SSE shape: a synthesized terminal event with no timestamp key.
    const missing = {
      type: 'task_complete',
      outcome: 'completed',
      synthesized: true,
    } as unknown as TaskEvent;
    emit('TASK-MISSING', missing);

    expect(screen.getByText('Time unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
    expect(screen.queryByText('FORMATTED')).not.toBeInTheDocument();
  });

  test('renders a stable fallback instead of Invalid Date for a malformed timestamp', () => {
    vi.spyOn(Date.prototype, 'toLocaleString').mockReturnValue('FORMATTED');
    render(<TaskEventsLog taskId="TASK-MALFORMED" />);

    const malformed: TaskEvent = {
      type: 'task_failed',
      outcome: 'failed',
      synthesized: true,
      timestamp: 'not-a-date',
    };
    emit('TASK-MALFORMED', malformed);

    expect(screen.getByText('Time unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
    expect(screen.queryByText('FORMATTED')).not.toBeInTheDocument();
  });
});

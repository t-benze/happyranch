import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import type { TaskRecord } from '@/lib/api/types';
import { TaskCard } from './TaskCard';

test('C8 standalone TaskCard keeps English defaults, chained rounding and sibling lineage links', () => {
  const now = new Date('2026-10-07T14:00:00Z').getTime();
  const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
  const ages = [[29000, 'just now'], [30000, '1m'], [3569000, '59m'], [3570000, '1h'],
    [84569000, '23h'], [84570000, '1d']] as const;
  const base: TaskRecord = {
    task_id: 'TASK-DEFAULT', team: 'engineering', brief: '# Raw brief / 原文',
    status: 'in_progress', block_kind: 'delegated', assigned_agent: 'raw_agent',
    parent_task_id: null, revisit_of_task_id: 'TASK-P', direct_revisits: ['TASK-R'],
    created_at: new Date(now).toISOString(), updated_at: new Date(now).toISOString(),
    closed_at: null, cancelled_at: null, session_timeout_seconds: null,
  };
  try {
    const { rerender } = render(<MemoryRouter><TaskCard task={base} to="/task" taskRoutes={{ detail: (id) => `/tasks/${id}` }} /></MemoryRouter>);
    for (const [elapsed, expected] of ages) {
      rerender(<MemoryRouter><TaskCard task={{ ...base, updated_at: new Date(now - elapsed).toISOString() }} to="/task" taskRoutes={{ detail: (id) => `/tasks/${id}` }} /></MemoryRouter>);
      expect(screen.getByRole('link', { name: /TASK-DEFAULT/ })).toHaveTextContent(expected);
    }
    expect(screen.getByRole('link', { name: /TASK-DEFAULT/ })).toHaveTextContent('· waiting on subtasks');
    expect(screen.getByRole('link', { name: /TASK-DEFAULT/ })).toHaveTextContent('Raw brief / 原文');
    expect(screen.getByRole('link', { name: 'supersedes TASK-P' })).toHaveAttribute('href', '/tasks/TASK-P');
    expect(screen.getByRole('link', { name: 'superseded by TASK-R' })).toHaveAttribute('href', '/tasks/TASK-R');
    expect(document.querySelector('a a')).toBeNull();
  } finally { clock.mockRestore(); }
});

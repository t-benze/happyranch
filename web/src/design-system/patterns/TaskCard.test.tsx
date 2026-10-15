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
    expect(screen.getByRole('link', { name: /TASK-DEFAULT/ })).not.toHaveTextContent('waiting on subtasks');
    expect(screen.getByRole('link', { name: /TASK-DEFAULT/ })).toHaveTextContent('Raw brief / 原文');
    expect(screen.getByRole('link', { name: 'supersedes TASK-P' })).toHaveAttribute('href', '/tasks/TASK-P');
    expect(screen.getByRole('link', { name: 'superseded by TASK-R' })).toHaveAttribute('href', '/tasks/TASK-R');
    expect(document.querySelector('a a')).toBeNull();
  } finally { clock.mockRestore(); }
});

test.each(['en', 'zh-CN'] as const)('resolved own qualifier and worse rollup suppression %s', (locale) => {
  const qualifier = locale === 'en' ? 'Continuation pending' : '等待后续处理';
  const task = {task_id:'CARD', team:'engineering', brief:'raw', status:'in_progress', block_kind:'delegated',
    assigned_agent:null, parent_task_id:null, revisit_of_task_id:null, created_at:'2026-10-14T00:00:00Z',
    updated_at:'2026-10-14T00:00:00Z', closed_at:null, cancelled_at:null, session_timeout_seconds:null} as TaskRecord;
  const {container, rerender} = render(<MemoryRouter><TaskCard task={task} to="/task" waitingQualifier={qualifier} /></MemoryRouter>);
  expect(container).toHaveTextContent(qualifier);
  for (const [status, severity_rollup] of [['in_progress','failed'], ['completed','in_progress']] as const) {
    rerender(<MemoryRouter><TaskCard task={{...task,status,severity_rollup}} to="/task" waitingQualifier={qualifier} /></MemoryRouter>);
    expect(container).not.toHaveTextContent(qualifier);
  }
});

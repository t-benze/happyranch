import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TraceTree } from './TraceTree';
import type { TaskRecallNode } from '@/lib/api/types';

const node: TaskRecallNode = {
  task_id: 'TASK-1',
  assigned_agent: 'engineering_head',
  brief: 'Root task',
  status: 'completed',
  output_summary: null,
  children: [
    {
      task_id: 'TASK-2',
      assigned_agent: 'backend_dev',
      brief: 'Child task',
      status: 'completed',
      output_summary: null,
      children: [],
    },
  ],
};

const costs = {
  'TASK-1': { tokens: 5000 },
  'TASK-2': { tokens: 1200, usd: 0.05 },
};

function wrap(ui: JSX.Element): JSX.Element {
  return <MemoryRouter>{ui}</MemoryRouter>;
}

describe('TraceTree', () => {
  test('recurses children with deeper indent', () => {
    render(wrap(<TraceTree root={node} costs={costs} density="compact" />));
    expect(screen.getByText('Root task')).toBeInTheDocument();
    expect(screen.getByText('Child task')).toBeInTheDocument();
  });

  test('renders token annotation when cost is supplied', () => {
    render(wrap(<TraceTree root={node} costs={costs} density="compact" />));
    expect(screen.getByText(/5,000 tok/)).toBeInTheDocument();
    expect(screen.getByText(/1,200 tok/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.05/)).toBeInTheDocument();
  });

  test('omits annotation when cost missing', () => {
    render(wrap(<TraceTree root={node} costs={{}} density="compact" />));
    expect(screen.queryByText(/tok$/)).not.toBeInTheDocument();
  });
});

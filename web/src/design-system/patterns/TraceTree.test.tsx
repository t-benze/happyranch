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

  test('renders compact token annotation when cost is supplied, full precision in title', () => {
    render(wrap(<TraceTree root={node} costs={costs} density="compact" />));
    // THR-099: token counts now render compact via StatValue; the exact figure
    // is preserved in the title tooltip so nothing is lost.
    expect(screen.getByTitle('5,000')).toHaveTextContent('5.0K');
    expect(screen.getByTitle('1,200')).toHaveTextContent('1.2K');
    expect(screen.getByText(/\$0\.05/)).toBeInTheDocument();
  });

  test('omits annotation when cost missing', () => {
    render(wrap(<TraceTree root={node} costs={{}} density="compact" />));
    expect(screen.queryByText(/tok$/)).not.toBeInTheDocument();
  });
});

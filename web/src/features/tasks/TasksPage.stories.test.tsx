import { composeStories } from '@storybook/react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import * as stories from '@/design-system/TasksList.stories';

const { Populated, Empty, Loading, InitialErrorRetry, LongContent } = composeStories(stories);
describe('C14 Tasks stories render shipping components with local state', () => {
  test('populated and filtering stay truthful', async () => {
    render(<MemoryRouter><Populated /></MemoryRouter>);
    await screen.findByText('End of list');
    expect(screen.getByText('Tasks')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open assistant' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
    await userEvent.type(screen.getByLabelText('Assigned agent (exact name)'), 'unknown-agent');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await screen.findByText('No tasks');
    expect(screen.getByText(/LOADED MATCHING ROOT TASKS/)).toHaveTextContent('0 LOADED');
  });
  test('loading excludes empty and error', () => {
    render(<MemoryRouter><Loading /></MemoryRouter>);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('empty is successful', async () => {
    render(<MemoryRouter><Empty /></MemoryRouter>);
    await screen.findByText('No tasks');
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('initial error has functional local retry', async () => {
    render(<MemoryRouter><InitialErrorRetry /></MemoryRouter>);
    await screen.findByText('Could not load tasks');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('End of list');
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('long fixture preserves all seven statuses and full identity access', async () => {
    render(<MemoryRouter><LongContent /></MemoryRouter>);
    await screen.findByText('End of list');
    await waitFor(() => expect(document.querySelectorAll('[data-tasks-responsive-list] li')).toHaveLength(7));
    expect(screen.getAllByTitle('long_exact_agent_name')).toHaveLength(7);
    expect(screen.getAllByTitle('THR-LONG-IDENTIFIER')).toHaveLength(7);
    expect(document.querySelectorAll('[data-tasks-responsive-list] .opacity-60')).toHaveLength(1);
  });
});

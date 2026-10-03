import { composeStories } from '@storybook/react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import { I18nProvider } from '@/hooks/i18n';
import * as stories from '@/design-system/TasksList.stories';

const { Populated, Empty, Loading, InitialErrorRetry, LongContent, NoEscalated } = composeStories(stories);

/**
 * `composeStories` applies only story-level decorators; the real Storybook
 * preview decorator supplies `<I18nProvider>` globally, and the TasksList
 * story decorator renders `<AppBar>` (a locale consumer). Mirror that provider
 * here rather than weakening the `useI18n` fail-closed contract.
 */
function renderStory(story: ReactElement) {
  return render(<I18nProvider><MemoryRouter>{story}</MemoryRouter></I18nProvider>);
}
describe('C14 Tasks stories render shipping components with local state', () => {
  test('populated and filtering stay truthful', async () => {
    renderStory(<Populated />);
    await screen.findByText('End of list');
    expect(screen.getByText('Tasks')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open assistant' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
    await userEvent.type(screen.getByLabelText('Assigned agent (exact name)'), 'unknown-agent');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    // The escalated traversal is independent of the agent filter, so its row
    // stays visible; 'No tasks' would contradict the rendered rows.
    await screen.findByText('Vet partner hotel candidates');
    expect(screen.getByText(/LOADED MATCHING ROOT TASKS/)).toHaveTextContent('0 LOADED');
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
  });
  test('loading excludes empty and error', () => {
    renderStory(<Loading />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('empty is successful', async () => {
    renderStory(<Empty />);
    await screen.findByText('No tasks');
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('initial error has functional local retry', async () => {
    renderStory(<InitialErrorRetry />);
    await screen.findByText('Could not load tasks');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('End of list');
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });
  test('long fixture preserves all seven statuses and full identity access', async () => {
    renderStory(<LongContent />);
    await screen.findByText('End of list');
    // Waiting-on-you roots are owned by their independent exact-status
    // traversal; they now render as the first group INSIDE the shared list
    // shell, so the shell holds all seven rows (one waiting + six ordinary).
    await waitFor(() => {
      const list = document.querySelector('[data-tasks-responsive-list]')!;
      const waitingRows = list.querySelectorAll('[aria-labelledby="waiting-on-you-heading"] li');
      const allRows = list.querySelectorAll('li');
      expect(waitingRows).toHaveLength(1);
      expect(allRows).toHaveLength(7);
      expect(allRows.length - waitingRows.length).toBe(6);
    });
    expect(screen.getAllByTitle('long_exact_agent_name')).toHaveLength(7);
    expect(screen.getAllByTitle('THR-LONG-IDENTIFIER')).toHaveLength(7);
    expect(document.querySelectorAll('[data-tasks-responsive-list] .opacity-60')).toHaveLength(1);
  });
  test('escalated group renders first with shared styling, absent when attention is empty', async () => {
    const { unmount } = renderStory(<Populated />);
    await screen.findByText('End of list');
    const list = screen.getByTestId('tasks-responsive-list');
    const escalatedSection = list.querySelector('[aria-labelledby="waiting-on-you-heading"]') as HTMLElement | null;
    expect(escalatedSection).not.toBeNull();
    // Same shared rows-card tokens as every ordinary group.
    for (const token of ['bg-surface-raised', 'rounded-xl', 'border', 'shadow-sm']) {
      expect(escalatedSection!.classList.contains(token)).toBe(true);
    }
    expect(escalatedSection!.classList.contains('bg-surface-page')).toBe(false);
    expect(escalatedSection!.closest('.tasks-group')!.classList.contains('mx-6')).toBe(false);
    expect(within(list).getAllByRole('heading')[0]).toHaveTextContent('Waiting on you');
    unmount();

    renderStory(<NoEscalated />);
    await screen.findByText('End of list');
    const noEscalatedList = screen.getByTestId('tasks-responsive-list');
    expect(within(noEscalatedList).queryByRole('heading', { name: /Waiting on you/ })).toBeNull();
    expect(noEscalatedList.querySelector('[aria-labelledby="waiting-on-you-heading"]')).toBeNull();
    expect(within(noEscalatedList).getAllByRole('heading')[0]).toHaveTextContent('Completed');
  });
});

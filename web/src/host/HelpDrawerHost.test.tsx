import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { HelpDrawerHost } from './HelpDrawerHost';

function setup(initialPath = '/orgs/demo/threads') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <HelpDrawerHost />
    </MemoryRouter>,
  );
}

describe('HelpDrawerHost', () => {
  it('opens the help dialog on `?`', () => {
    setup();
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => {
      fireEvent.keyDown(window, { key: '?' });
    });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument();
  });

  it('defaults to the Threads tab on a /threads route', () => {
    setup('/orgs/demo/threads/THR-1');
    act(() => {
      fireEvent.keyDown(window, { key: '?' });
    });
    const threadsTab = screen.getByRole('tab', { name: 'Threads' });
    expect(threadsTab).toHaveAttribute('data-state', 'active');
  });

  it('defaults to the Tasks tab on a /tasks route', () => {
    setup('/orgs/demo/tasks');
    act(() => {
      fireEvent.keyDown(window, { key: '?' });
    });
    const tasksTab = screen.getByRole('tab', { name: 'Tasks' });
    expect(tasksTab).toHaveAttribute('data-state', 'active');
  });

  it('falls back to Global on an unknown route', () => {
    setup('/orgs/demo/');
    act(() => {
      fireEvent.keyDown(window, { key: '?' });
    });
    const globalTab = screen.getByRole('tab', { name: 'Global' });
    expect(globalTab).toHaveAttribute('data-state', 'active');
  });

  it('is suppressed when focus is in an input', () => {
    setup();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: '?', bubbles: true });
    expect(screen.queryByRole('dialog')).toBeNull();
    document.body.removeChild(input);
  });
});

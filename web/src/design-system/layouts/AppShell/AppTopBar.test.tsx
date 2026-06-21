/**
 * THR-030 BUG-04/05/06 — persistent top app bar.
 *
 * - pageTitleFromPath maps the active route section to a page name, defaulting
 *   to "Home" for the root / unknown sections.
 * - The app bar renders the page name (left) plus the search launcher and the
 *   theme toggle (right).
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import { AppTopBar, pageTitleFromPath } from './AppTopBar';

describe('pageTitleFromPath', () => {
  test('maps known route sections to page names', () => {
    expect(pageTitleFromPath('/orgs/acme/dashboard')).toBe('Home');
    expect(pageTitleFromPath('/orgs/acme/threads')).toBe('Threads');
    expect(pageTitleFromPath('/orgs/acme/tasks/TASK-1')).toBe('Tasks');
    expect(pageTitleFromPath('/orgs/acme/kb')).toBe('Knowledge');
    expect(pageTitleFromPath('/orgs/acme/audit')).toBe('Audit');
  });

  test('defaults to Home for the root and unknown sections', () => {
    expect(pageTitleFromPath('/')).toBe('Home');
    expect(pageTitleFromPath('/orgs/acme')).toBe('Home');
    expect(pageTitleFromPath('/orgs/acme/totally-unknown')).toBe('Home');
  });
});

function renderBar(route: string): void {
  render(
    <MemoryRouter initialEntries={[route]}>
      <AppTopBar />
    </MemoryRouter>,
  );
}

describe('AppTopBar chrome', () => {
  test('renders the current page name from the route', () => {
    renderBar('/orgs/acme/agents');
    expect(screen.getByText('Agents')).toBeInTheDocument();
  });

  test('renders the search launcher and the theme toggle', () => {
    renderBar('/orgs/acme/dashboard');
    expect(screen.getByText('Ask or search…')).toBeInTheDocument();
    expect(screen.getByLabelText(/theme/i)).toBeInTheDocument();
  });
});

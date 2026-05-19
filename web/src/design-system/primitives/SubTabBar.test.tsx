import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { SubTabBar } from './SubTabBar';

function PathProbe(): JSX.Element {
  const loc = useLocation();
  return <pre data-testid="path">{loc.pathname}</pre>;
}

const TABS = [
  { value: 'activity', label: 'Activity', to: '/x' },
  { value: 'escalations', label: 'Escalations', to: '/x/escalations' },
  { value: 'traces', label: 'Traces', to: '/x/traces' },
];

describe('SubTabBar', () => {
  test('marks the active tab based on the active prop', () => {
    render(
      <MemoryRouter initialEntries={['/x']}>
        <SubTabBar tabs={TABS} active="activity" />
      </MemoryRouter>,
    );
    expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: 'Escalations' })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  test('clicking a tab navigates via React Router', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/x']}>
        <Routes>
          <Route
            path="/x/*"
            element={
              <>
                <SubTabBar tabs={TABS} active="activity" />
                <PathProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('tab', { name: 'Escalations' }));
    expect(screen.getByTestId('path').textContent).toBe('/x/escalations');
  });
});

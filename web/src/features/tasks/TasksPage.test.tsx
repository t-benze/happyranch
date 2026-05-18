import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PrototypeProvider } from '@/design-system/providers/PrototypeProvider';
import { TasksPage } from './TasksPage';

function renderAt(path: string) {
  return render(
    <PrototypeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/orgs/:slug/tasks" element={<TasksPage />} />
          <Route path="/orgs/:slug/tasks/:task_id" element={<TasksPage />} />
        </Routes>
      </MemoryRouter>
    </PrototypeProvider>,
  );
}

describe('TasksPage', () => {
  it('renders the inbox with fixture tasks', () => {
    renderAt('/orgs/hk-macau-tourism/tasks');
    expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
  });

  it('renders empty filter sidebar groups', () => {
    renderAt('/orgs/hk-macau-tourism/tasks');
    expect(screen.getByText(/Status/i)).toBeInTheDocument();
    expect(screen.getByText(/Team/i)).toBeInTheDocument();
  });
});

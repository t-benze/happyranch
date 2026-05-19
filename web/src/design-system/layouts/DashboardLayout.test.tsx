import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { DashboardLayout } from './DashboardLayout';

describe('DashboardLayout', () => {
  test('renders all four labeled slots', () => {
    render(
      <DashboardLayout
        health={<div>health-body</div>}
        pending={<div>pending-body</div>}
        activeByTeam={<div>active-body</div>}
        blocked={<div>blocked-body</div>}
      />,
    );
    expect(screen.getByText(/system health/i)).toBeInTheDocument();
    expect(screen.getByText(/pending your action/i)).toBeInTheDocument();
    expect(screen.getByText(/active tasks by team/i)).toBeInTheDocument();
    expect(screen.getByText(/blocked tasks/i)).toBeInTheDocument();
    expect(screen.getByText('health-body')).toBeInTheDocument();
    expect(screen.getByText('pending-body')).toBeInTheDocument();
    expect(screen.getByText('active-body')).toBeInTheDocument();
    expect(screen.getByText('blocked-body')).toBeInTheDocument();
  });
});

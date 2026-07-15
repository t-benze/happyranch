import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from './StatusBadge';

/**
 * StatusBadge — THR-037 Change B (Path B) task vocabulary. `blocked` is gone;
 * `escalated` is a first-class red status, `cancelled` is a muted terminal, and
 * a parked `in_progress` task keeps an active (green) badge with a derived
 * waiting qualifier.
 */
describe('StatusBadge — Path B task vocabulary', () => {
  test('escalated renders the red escalated token, no led dot', () => {
    const { container } = render(<StatusBadge status="escalated" />);
    const badge = screen.getByText('escalated');
    expect(badge).toHaveClass('text-status-escalated');
    // Escalated is not an "active" state → no led dot.
    expect(container.querySelector('[aria-hidden]')).toBeNull();
  });

  test('cancelled renders a muted terminal token, no led dot', () => {
    const { container } = render(<StatusBadge status="cancelled" />);
    const badge = screen.getByText('cancelled');
    expect(badge).toHaveClass('text-status-archived');
    expect(container.querySelector('[aria-hidden]')).toBeNull();
  });

  test('running in_progress shows the active led dot and no waiting qualifier', () => {
    const { container } = render(<StatusBadge status="in_progress" />);
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden]')).not.toBeNull();
    expect(screen.queryByText(/waiting on/)).not.toBeInTheDocument();
  });

  test('parked in_progress + delegated stays active green with "waiting on subtasks"', () => {
    const { container } = render(
      <StatusBadge status="in_progress" blockKind="delegated" />,
    );
    // Active (green) badge retained — the led dot still renders.
    expect(container.querySelector('[aria-hidden]')).not.toBeNull();
    // Derived muted qualifier names what it is waiting on.
    expect(screen.getByText('· waiting on subtasks')).toBeInTheDocument();
  });

  test('parked in_progress + blocked_on_job shows "waiting on jobs"', () => {
    render(<StatusBadge status="in_progress" blockKind="blocked_on_job" />);
    expect(screen.getByText('· waiting on jobs')).toBeInTheDocument();
  });

  test('superseded renders its prose label', () => {
    render(<StatusBadge status="superseded" />);
    expect(screen.getByText('superseded')).toBeInTheDocument();
  });

  // THR-099 Batch 1: thread `open` follows the design colour vocabulary —
  // BLUE (info), not the old GREEN. This asserts the new behaviour and is
  // RED against the pre-batch code (which used text-status-open green).
  test('thread open renders the blue info tone, not the old green', () => {
    render(<StatusBadge status="open" />);
    const badge = screen.getByText('open');
    expect(badge).toHaveClass('text-info');
    expect(badge).not.toHaveClass('text-status-open');
  });

  test('thread archived stays the neutral grey tone', () => {
    render(<StatusBadge status="archived" />);
    expect(screen.getByText('archived')).toHaveClass('text-status-archived');
  });
});

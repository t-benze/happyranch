import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from './Dialog';

describe('Dialog', () => {
  it('keeps content constrained to the viewport and scrollable', () => {
    render(
      <Dialog open onOpenChange={() => {}}>
        <DialogContent>
          <DialogTitle>Run job</DialogTitle>
          <DialogDescription>Approve and run this job.</DialogDescription>
          <div>Long body</div>
        </DialogContent>
      </Dialog>,
    );

    expect(screen.getByRole('dialog')).toHaveClass(
      'max-h-[calc(100dvh-2rem)]',
      'overflow-x-hidden',
      'overflow-y-auto',
    );
  });

  it('defaults the close control to the legacy English label (W2a R2)', () => {
    render(
      <Dialog open onOpenChange={() => {}}>
        <DialogContent>
          <DialogTitle>Run job</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    // Omitted-prop callers keep their exact prior accessible name.
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('honors a localized closeLabel prop (W2a R2)', () => {
    render(
      <Dialog open onOpenChange={() => {}}>
        <DialogContent closeLabel="关闭">
          <DialogTitle>新建组织</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });
});

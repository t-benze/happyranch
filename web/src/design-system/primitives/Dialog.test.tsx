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
});

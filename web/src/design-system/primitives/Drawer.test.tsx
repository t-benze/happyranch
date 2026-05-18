import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Drawer, DrawerContent, DrawerTitle } from './Drawer';

describe('Drawer', () => {
  it('renders content when open', () => {
    render(
      <Drawer open onOpenChange={() => {}}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
          <p>body</p>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.getByText('Detail')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('does not render content when closed', () => {
    render(
      <Drawer open={false} onOpenChange={() => {}}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.queryByText('Detail')).toBeNull();
  });

  it('calls onOpenChange on escape', () => {
    const onOpenChange = vi.fn();
    render(
      <Drawer open onOpenChange={onOpenChange}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
        </DrawerContent>
      </Drawer>,
    );
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

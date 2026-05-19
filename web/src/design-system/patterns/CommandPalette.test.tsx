import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { CommandPalette, type CommandPaletteSection } from './CommandPalette';

const SECTIONS: CommandPaletteSection[] = [
  {
    label: 'Threads',
    items: [
      { key: 't1', primary: 'THR-0001 · Hong Kong visa guide', href: '/t/1' },
      { key: 't2', primary: 'THR-0002 · Macau ferry timetable', href: '/t/2' },
    ],
  },
  {
    label: 'Tasks',
    items: [
      { key: 'tk1', primary: 'TASK-9 · refresh hotels', href: '/k/9' },
    ],
  },
];

describe('CommandPalette', () => {
  it('renders nothing when closed', () => {
    render(
      <CommandPalette
        open={false}
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByPlaceholderText(/Search threads/i)).toBeNull();
  });

  it('renders section headers and items when open', () => {
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('Threads')).toBeInTheDocument();
    expect(screen.getByText('Tasks')).toBeInTheDocument();
    expect(screen.getByText(/Hong Kong visa guide/)).toBeInTheDocument();
    expect(screen.getByText(/refresh hotels/)).toBeInTheDocument();
  });

  it('filters via substring match across primary and secondary', () => {
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={() => {}}
      />,
    );
    const input = screen.getByPlaceholderText(/Search threads/i);
    fireEvent.change(input, { target: { value: 'macau' } });
    expect(screen.getByText(/Macau ferry/)).toBeInTheDocument();
    expect(screen.queryByText(/Hong Kong visa/)).toBeNull();
    expect(screen.queryByText(/refresh hotels/)).toBeNull();
  });

  it('hides empty sections', () => {
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={() => {}}
      />,
    );
    const input = screen.getByPlaceholderText(/Search threads/i);
    fireEvent.change(input, { target: { value: 'macau' } });
    expect(screen.queryByText('Tasks')).toBeNull();
  });

  it('calls onSelect when Enter is pressed on the active row', () => {
    const onSelect = vi.fn();
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={onSelect}
      />,
    );
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('/t/1', expect.objectContaining({ key: 't1' }));
  });

  it('moves selection with ArrowDown', () => {
    const onSelect = vi.fn();
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={onSelect}
      />,
    );
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'ArrowDown' });
    fireEvent.keyDown(dialog, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('/t/2', expect.objectContaining({ key: 't2' }));
  });

  it('clicking an option calls onSelect with its href', () => {
    const onSelect = vi.fn();
    render(
      <CommandPalette
        open
        onClose={() => {}}
        sections={SECTIONS}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText(/refresh hotels/));
    expect(onSelect).toHaveBeenCalledWith('/k/9', expect.objectContaining({ key: 'tk1' }));
  });
});

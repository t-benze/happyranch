import { useState } from 'react';
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

  it('preserves typed query and filtered selection across a localized-string swap (case B)', () => {
    function Harness(): JSX.Element {
      const [zh, setZh] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setZh(true)}>
            to-zh
          </button>
          <CommandPalette
            open
            onClose={() => {}}
            sections={SECTIONS}
            onSelect={() => {}}
            {...(zh
              ? {
                  title: '命令面板',
                  description: '输入以筛选。',
                  searchPlaceholder: '搜索会话、任务、智能体、组织、知识库…',
                  searchLabel: '命令面板搜索',
                  resultsLabel: '结果',
                }
              : {})}
          />
        </>
      );
    }
    render(<Harness />);
    const input = screen.getByPlaceholderText(/Search threads/i);
    fireEvent.change(input, { target: { value: 'macau' } });
    expect(input).toHaveValue('macau');
    expect(screen.getByText(/Macau ferry/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('to-zh'));
    const zhInput = screen.getByPlaceholderText('搜索会话、任务、智能体、组织、知识库…');
    // The typed query and the filtered result survive the copy swap (no remount).
    expect(zhInput).toHaveValue('macau');
    expect(screen.getByText(/Macau ferry/)).toBeInTheDocument();
    expect(screen.queryByText(/Hong Kong visa/)).toBeNull();
  });

  it('keeps the legacy English close control when no localized label is passed (W2a R2)', () => {
    render(
      <CommandPalette open onClose={() => {}} sections={SECTIONS} onSelect={() => {}} />,
    );
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('localizes the close control from an explicit closeLabel (W2a R2)', () => {
    render(
      <CommandPalette
        open
        onClose={() => {}}
        onSelect={() => {}}
        sections={[]}
        title="命令面板"
        closeLabel="关闭"
      />,
    );
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });

  it('accepts a dedicated closeAriaLabel distinct from the footer text (W2a R2)', () => {
    render(
      <CommandPalette
        open
        onClose={() => {}}
        onSelect={() => {}}
        sections={[]}
        closeLabel="close"
        closeAriaLabel="关闭"
      />,
    );
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
    // The footer keeps the separately supplied text.
    expect(screen.getByRole('dialog').textContent).toContain('close');
  });

  it('lets the actual X close control activate with Enter without selecting (W2a R2)', () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(<CommandPalette open onClose={onClose} sections={SECTIONS} onSelect={onSelect} />);
    const close = screen.getByRole('button', { name: 'Close' });
    close.focus();
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(close, { key: 'Enter' });
    // The container must neither cancel the native activation nor select a row.
    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Real browsers fire the button's native click after Enter keydown; that
    // reaches the Radix Close control and closes exactly once.
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('lets the localized X close control activate with Enter on an empty result set (W2a R2)', () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <CommandPalette
        open
        onClose={onClose}
        sections={[]}
        onSelect={onSelect}
        closeLabel="关闭"
      />,
    );
    const close = screen.getByRole('button', { name: '关闭' });
    close.focus();
    fireEvent.keyDown(close, { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not intercept Enter while a result option row is focused (W2a R2)', () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(<CommandPalette open onClose={onClose} sections={SECTIONS} onSelect={onSelect} />);
    const option = screen.getByRole('option', { name: /Macau ferry/ });
    option.focus();
    fireEvent.keyDown(option, { key: 'Enter' });
    // Deferred to the row's own native activation; no container selection/close.
    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(option);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('/t/2', expect.objectContaining({ key: 't2' }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('still selects the active row on Enter from the search input (W2a R2)', () => {
    const onSelect = vi.fn();
    render(<CommandPalette open onClose={() => {}} sections={SECTIONS} onSelect={onSelect} />);
    const input = screen.getByPlaceholderText(/Search threads/i);
    input.focus();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('/t/1', expect.objectContaining({ key: 't1' }));
  });

  it('still selects a nondefault row on ArrowDown + Enter from the search input (W2a R2)', () => {
    const onSelect = vi.fn();
    render(<CommandPalette open onClose={() => {}} sections={SECTIONS} onSelect={onSelect} />);
    const input = screen.getByPlaceholderText(/Search threads/i);
    input.focus();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('/t/2', expect.objectContaining({ key: 't2' }));
  });
});

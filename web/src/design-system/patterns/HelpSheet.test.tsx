import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { HelpSheet, type ShortcutSection } from './HelpSheet';

const SECTIONS: ShortcutSection[] = [
  {
    label: 'Global',
    shortcuts: [
      { keys: ['?'], description: 'Show this help' },
      { keys: ['Cmd', 'K'], description: 'Open command palette' },
    ],
  },
  {
    label: 'Threads',
    shortcuts: [{ keys: ['N'], description: 'New thread' }],
  },
];

describe('HelpSheet', () => {
  it('renders the flat list when `shortcuts` is passed', () => {
    render(
      <HelpSheet
        open
        onClose={() => {}}
        shortcuts={[{ keys: ['N'], description: 'New thread' }]}
      />,
    );
    expect(screen.getByText('New thread')).toBeInTheDocument();
  });

  it('renders tabs when `sections` is passed', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    expect(screen.getByRole('tab', { name: 'Global' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Threads' })).toBeInTheDocument();
  });

  it('defaults to the first section', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    expect(screen.getByText('Show this help')).toBeInTheDocument();
    expect(screen.queryByText('New thread')).toBeNull();
  });

  it('honors `defaultTab`', () => {
    render(
      <HelpSheet open onClose={() => {}} sections={SECTIONS} defaultTab="Threads" />,
    );
    expect(screen.getByText('New thread')).toBeInTheDocument();
    expect(screen.queryByText('Show this help')).toBeNull();
  });

  it('switches tab on click', async () => {
    const user = userEvent.setup();
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    await user.click(screen.getByRole('tab', { name: 'Threads' }));
    expect(screen.getByText('New thread')).toBeInTheDocument();
  });

  it('skips empty sections when in tabbed mode', () => {
    render(
      <HelpSheet
        open
        onClose={() => {}}
        sections={[
          { label: 'Global', shortcuts: SECTIONS[0].shortcuts },
          { label: 'Empty', shortcuts: [] },
        ]}
      />,
    );
    expect(screen.queryByRole('tab', { name: 'Empty' })).toBeNull();
  });

  it('keeps the selected tab when localized labels change but stable ids stay (W2a)', () => {
    function Harness(): JSX.Element {
      const [zh, setZh] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setZh(true)}>
            to-zh
          </button>
          <HelpSheet
            open
            onClose={() => {}}
            defaultTabId="threads"
            title={zh ? '键盘快捷键' : 'Keyboard shortcuts'}
            emptyLabel={zh ? '未定义快捷键。' : 'No shortcuts defined.'}
            sections={[
              {
                id: 'global',
                label: zh ? '全局' : 'Global',
                shortcuts: [{ keys: ['?'], description: zh ? '显示此帮助' : 'Show this help' }],
              },
              {
                id: 'threads',
                label: zh ? '会话' : 'Threads',
                shortcuts: [{ keys: ['N'], description: zh ? '新建会话' : 'New thread' }],
              },
            ]}
          />
        </>
      );
    }
    render(<Harness />);
    expect(screen.getByRole('tab', { name: 'Threads' })).toHaveAttribute('data-state', 'active');
    fireEvent.click(screen.getByText('to-zh'));
    // Stable id keeps the same tab active after the label is localized.
    expect(screen.getByRole('tab', { name: '会话' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByRole('tab', { name: '全局' })).toHaveAttribute('data-state', 'inactive');
    expect(screen.getByText('新建会话')).toBeInTheDocument();
    expect(screen.getByText('键盘快捷键')).toBeInTheDocument();
  });

  it('still honors the legacy label-based `defaultTab`', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} defaultTab="Threads" />);
    expect(screen.getByRole('tab', { name: 'Threads' })).toHaveAttribute('data-state', 'active');
  });

  it('keeps the legacy English close control when no closeLabel is passed (W2a R2)', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} />);
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('localizes the close control from closeLabel (W2a R2)', () => {
    render(<HelpSheet open onClose={() => {}} sections={SECTIONS} closeLabel="关闭" />);
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });
});

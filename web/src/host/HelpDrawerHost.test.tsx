import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { HelpDrawerHost } from './HelpDrawerHost';
import { I18nProvider } from '@/hooks/i18n';
import { LocaleTestSwitch, savedLocaleAdapter } from '@/test/render';
import type { Locale } from '@/lib/i18n';

function setup(initialPath = '/orgs/demo/threads', locale: Locale = 'en') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <I18nProvider adapter={savedLocaleAdapter(locale)} mode="preview">
        <HelpDrawerHost />
        <LocaleTestSwitch to="zh-CN" />
        <LocaleTestSwitch to="en" />
      </I18nProvider>
    </MemoryRouter>,
  );
}

function openHelp() {
  act(() => {
    fireEvent.keyDown(window, { key: '?' });
  });
}

describe('HelpDrawerHost', () => {
  it('opens the help dialog on `?`', () => {
    setup();
    expect(screen.queryByRole('dialog')).toBeNull();
    openHelp();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument();
  });

  it('defaults to the Threads tab on a /threads route', () => {
    setup('/orgs/demo/threads/THR-1');
    openHelp();
    const threadsTab = screen.getByRole('tab', { name: 'Threads' });
    expect(threadsTab).toHaveAttribute('data-state', 'active');
  });

  it('defaults to the Tasks tab on a /tasks route', () => {
    setup('/orgs/demo/tasks');
    openHelp();
    const tasksTab = screen.getByRole('tab', { name: 'Tasks' });
    expect(tasksTab).toHaveAttribute('data-state', 'active');
  });

  it('falls back to Global on an unknown route', () => {
    setup('/orgs/demo/');
    openHelp();
    const globalTab = screen.getByRole('tab', { name: 'Global' });
    expect(globalTab).toHaveAttribute('data-state', 'active');
  });

  it('is suppressed when focus is in an input', () => {
    setup();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: '?', bubbles: true });
    expect(screen.queryByRole('dialog')).toBeNull();
    document.body.removeChild(input);
  });

  it('renders zh-CN help copy and translated shortcut descriptions', () => {
    setup('/orgs/demo/threads', 'zh-CN');
    openHelp();
    expect(screen.getByText('键盘快捷键')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '会话' })).toHaveAttribute('data-state', 'active');
    // Real zh-CN catalog text for a thread shortcut description.
    expect(screen.getByText('新建会话')).toBeInTheDocument();
    // Key sequences are unchanged.
    expect(screen.getByText('N')).toBeInTheDocument();
  });

  it('keeps the selected non-default tab and open state across a locale switch (case B)', async () => {
    const user = userEvent.setup();
    setup('/orgs/demo/threads');
    openHelp();

    // Move to a non-default tab.
    await user.click(screen.getByRole('tab', { name: 'Tasks' }));
    expect(screen.getByRole('tab', { name: 'Tasks' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByText('Close the open task drawer or dialog')).toBeInTheDocument();

    // Switch locale through the test-only provider control. Radix Dialog sets
    // pointer-events:none on outside content while modal, so dispatch directly.
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    const zhTasksTab = screen.getByRole('tab', { name: '任务' });
    expect(zhTasksTab).toHaveAttribute('data-state', 'active');
    expect(screen.getByText('关闭打开的任务抽屉或对话框')).toBeInTheDocument();
    // Default tab did not reset to the route's Threads tab.
    expect(screen.getByRole('tab', { name: '会话' })).toHaveAttribute('data-state', 'inactive');
  });
});

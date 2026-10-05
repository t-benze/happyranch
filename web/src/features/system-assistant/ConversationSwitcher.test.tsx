/**
 * Tests for ConversationSwitcher — the in-dock conversation list (THR-056
 * STEP-B). Covers newest-first ordering, active-state reflection, and the
 * new / switch / rename / delete handler wiring, including the inline rename
 * editor and the inline delete confirm.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { I18nProvider } from '@/hooks/i18n';
import { savedLocaleAdapter } from '@/test/render';
import type { ConversationSummary } from '@/hooks/assistant';
import {
  ConversationSwitcher,
  sortConversationsNewestFirst,
} from './ConversationSwitcher';

function conv(over: Partial<ConversationSummary>): ConversationSummary {
  return {
    id: 'id',
    title: 'A conversation',
    created_at: '2026-07-01T00:00:00Z',
    active: false,
    ...over,
  };
}

function renderSwitcher(over: Partial<React.ComponentProps<typeof ConversationSwitcher>> = {}) {
  const props = {
    conversations: [] as ConversationSummary[],
    loading: false,
    error: null as string | null,
    busy: false,
    onNew: vi.fn(),
    onSwitch: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  render(<ConversationSwitcher {...props} />, { wrapper: I18nProvider });
  return props;
}

describe('sortConversationsNewestFirst', () => {
  test('orders by created_at descending (newest first)', () => {
    const list = [
      conv({ id: 'old', created_at: '2026-07-01T00:00:00Z' }),
      conv({ id: 'new', created_at: '2026-07-04T00:00:00Z' }),
      conv({ id: 'mid', created_at: '2026-07-02T00:00:00Z' }),
    ];
    expect(sortConversationsNewestFirst(list).map((c) => c.id)).toEqual([
      'new',
      'mid',
      'old',
    ]);
  });

  test('sorts null created_at last and is stable across ties', () => {
    const list = [
      conv({ id: 'null-a', created_at: null }),
      conv({ id: 'dated', created_at: '2026-07-02T00:00:00Z' }),
      conv({ id: 'null-b', created_at: null }),
    ];
    expect(sortConversationsNewestFirst(list).map((c) => c.id)).toEqual([
      'dated',
      'null-a',
      'null-b',
    ]);
  });

  test('does not mutate the input array', () => {
    const list = [
      conv({ id: 'a', created_at: '2026-07-01T00:00:00Z' }),
      conv({ id: 'b', created_at: '2026-07-04T00:00:00Z' }),
    ];
    sortConversationsNewestFirst(list);
    expect(list.map((c) => c.id)).toEqual(['a', 'b']);
  });
});

describe('ConversationSwitcher', () => {
  test('renders conversations newest-first and marks the active one', () => {
    renderSwitcher({
      conversations: [
        conv({ id: 'old', title: 'Older', created_at: '2026-07-01T00:00:00Z' }),
        conv({ id: 'new', title: 'Newer', created_at: '2026-07-04T00:00:00Z', active: true }),
      ],
    });
    const rows = screen.getAllByRole('listitem');
    // Newest first: 'Newer' precedes 'Older'.
    expect(within(rows[0]).getByText('Newer')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Older')).toBeInTheDocument();
    // Active row's switch button carries aria-current.
    expect(screen.getByRole('button', { name: 'Newer' })).toHaveAttribute(
      'aria-current',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Older' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  test('loading and empty states render distinct copy', () => {
    const { rerender } = renderReturning({ loading: true });
    expect(screen.getByText('Loading conversations…')).toBeInTheDocument();
    rerender(<ConversationSwitcher {...baseProps({ loading: false, conversations: [] })} />);
    expect(screen.getByText('No conversations yet.')).toBeInTheDocument();
  });

  test('error state renders an alert', () => {
    renderSwitcher({ error: 'Could not load conversations.' });
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load conversations.');
  });

  test('"New conversation" fires onNew', () => {
    const props = renderSwitcher();
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });

  test('clicking a row title fires onSwitch with its id', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c9', title: 'Pick me' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Pick me' }));
    expect(props.onSwitch).toHaveBeenCalledWith('c9');
  });

  test('inline rename commits the trimmed title on Enter', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c1', title: 'Before' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Rename Before' }));
    const input = screen.getByLabelText('Conversation title');
    fireEvent.change(input, { target: { value: '  After  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onRename).toHaveBeenCalledWith('c1', 'After');
  });

  test('rename is a no-op when the title is unchanged', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c1', title: 'Same' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Rename Same' }));
    fireEvent.keyDown(screen.getByLabelText('Conversation title'), { key: 'Enter' });
    expect(props.onRename).not.toHaveBeenCalled();
  });

  test('Escape cancels the rename editor without calling onRename', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c1', title: 'Keep' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Rename Keep' }));
    const input = screen.getByLabelText('Conversation title');
    fireEvent.change(input, { target: { value: 'Discarded' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Keep' })).toBeInTheDocument();
  });

  test('delete asks for inline confirmation before firing onDelete', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c1', title: 'Doomed' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Doomed' }));
    // Bare trash click does not delete.
    expect(props.onDelete).not.toHaveBeenCalled();
    // Confirm.
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(props.onDelete).toHaveBeenCalledWith('c1');
  });

  test('delete confirm can be cancelled', () => {
    const props = renderSwitcher({
      conversations: [conv({ id: 'c1', title: 'Spared' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Delete Spared' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Spared' })).toBeInTheDocument();
  });

  test('close button fires onClose', () => {
    const props = renderSwitcher();
    fireEvent.click(screen.getByRole('button', { name: 'Close conversations' }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });
});

// --- helpers for the rerender-based loading/empty test ---
function baseProps(
  over: Partial<React.ComponentProps<typeof ConversationSwitcher>> = {},
): React.ComponentProps<typeof ConversationSwitcher> {
  return {
    conversations: [],
    loading: false,
    error: null,
    busy: false,
    onNew: vi.fn(),
    onSwitch: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
}

function renderReturning(
  over: Partial<React.ComponentProps<typeof ConversationSwitcher>> = {},
) {
  return render(<ConversationSwitcher {...baseProps(over)} />, { wrapper: I18nProvider });
}


describe('Conversation switcher mounted localization', () => {
  test('Chinese loading, error, empty and busy controls localize while raw errors remain exact', () => {
    const props = baseProps({ loading: true, busy: true });
    const wrapper = ({ children }: { children: React.ReactNode }) => <I18nProvider adapter={savedLocaleAdapter('zh-CN')}>{children}</I18nProvider>;
    const { rerender } = render(<ConversationSwitcher {...props} />, { wrapper });
    expect(screen.getByRole('region', { name: '会话' })).toBeInTheDocument();
    expect(screen.getByText('正在加载会话…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建会话' })).toBeDisabled();
    rerender(<ConversationSwitcher {...props} loading={false} />);
    expect(screen.getByText('暂无会话。')).toBeInTheDocument();
    rerender(<ConversationSwitcher {...props} loading={false} error='Could not load conversations.' />);
    expect(screen.getByRole('alert').textContent).toBe('Could not load conversations.');
    fireEvent.click(screen.getByRole('button', { name: '关闭会话列表' }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onNew).not.toHaveBeenCalled();
    rerender(<ConversationSwitcher {...props} loading={false} busy={false} conversations={[conv({ id: 'raw/id', title: 'Raw title' })]} />);
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(props.onNew).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Raw title' }));
    expect(props.onSwitch).toHaveBeenCalledWith('raw/id');
    fireEvent.click(screen.getByRole('button', { name: '重命名 Raw title' }));
    fireEvent.change(screen.getByRole('textbox', { name: '会话标题' }), { target: { value: 'cancelled' } });
    fireEvent.click(screen.getByRole('button', { name: '取消重命名' }));
    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Raw title' })).toBeInTheDocument();
  });

  test('locale switching preserves rename selection and delete confirmation with verbatim callback arguments', () => {
    const rawTitle = 'Raw “会话” / Title';
    const props = baseProps({ conversations: [conv({ id: 'raw/id', title: rawTitle, active: true })] });
    const { rerender } = render(<ConversationSwitcher {...props} />, { wrapper: I18nProvider });
    fireEvent.click(screen.getByRole('button', { name: `Rename ${rawTitle}` }));
    const input = screen.getByRole('textbox', { name: 'Conversation title' }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '  Raw /新题  ' } });
    input.focus(); input.setSelectionRange(2, 9);
    fireEvent(window, new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: 'zh-CN' }));
    expect(screen.getByRole('textbox', { name: '会话标题' })).toBe(input);
    expect(input).toHaveValue('  Raw /新题  ');
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([2, 9]);
    rerender(<ConversationSwitcher {...props} busy />);
    expect(screen.getByRole('button', { name: '保存标题' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消重命名' })).toBeEnabled();
    rerender(<ConversationSwitcher {...props} />);
    fireEvent(window, new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: 'en' }));
    expect(screen.getByRole('textbox', { name: 'Conversation title' })).toBe(input);
    expect(document.activeElement).toBe(input);
    fireEvent.click(screen.getByRole('button', { name: 'Save title' }));
    expect(props.onRename).toHaveBeenCalledTimes(1);
    expect(props.onRename).toHaveBeenCalledWith('raw/id', 'Raw /新题');
    fireEvent.click(screen.getByRole('button', { name: `Delete ${rawTitle}` }));
    const confirmation = screen.getByText(`Delete “${rawTitle}”?`);
    fireEvent(window, new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: 'zh-CN' }));
    expect(screen.getByText(`删除“${rawTitle}”？`)).toBe(confirmation);
    rerender(<ConversationSwitcher {...props} busy />);
    expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeEnabled();
    expect(props.onDelete).not.toHaveBeenCalled();
    rerender(<ConversationSwitcher {...props} />);
    fireEvent(window, new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: 'en' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(props.onDelete).toHaveBeenCalledTimes(1);
    expect(props.onDelete).toHaveBeenCalledWith('raw/id');
    expect(props.onSwitch).not.toHaveBeenCalled();
    expect(props.onNew).not.toHaveBeenCalled();
  });
});

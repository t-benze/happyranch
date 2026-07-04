/**
 * Tests for ConversationSwitcher — the in-dock conversation list (THR-056
 * STEP-B). Covers newest-first ordering, active-state reflection, and the
 * new / switch / rename / delete handler wiring, including the inline rename
 * editor and the inline delete confirm.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
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
  render(<ConversationSwitcher {...props} />);
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
  return render(<ConversationSwitcher {...baseProps(over)} />);
}

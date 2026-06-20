import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTheme } from './theme';

describe('useTheme', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to light', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
  });

  it('reads persisted value', () => {
    localStorage.setItem('happyranch.theme', 'light');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
  });

  it('persists and applies attribute on setTheme', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('light'));
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem('happyranch.theme')).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('ignores invalid persisted value', () => {
    localStorage.setItem('happyranch.theme', 'sepia');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
  });

  it('mirrors storage events from other tabs', () => {
    const { result } = renderHook(() => useTheme());
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: 'happyranch.theme',
          newValue: 'light',
        }),
      );
    });
    expect(result.current.theme).toBe('light');
  });

  it('persists dark mode across remount (TASK-605 guard)', () => {
    // 1. Set dark and verify it sticks in the hook
    const { result: first, unmount } = renderHook(() => useTheme());
    act(() => first.current.setTheme('dark'));
    expect(first.current.theme).toBe('dark');
    expect(localStorage.getItem('happyranch.theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    // 2. Unmount the hook
    unmount();

    // 3. Remount — readInitial() should find "dark" in localStorage
    const { result: second } = renderHook(() => useTheme());
    expect(second.current.theme).toBe('dark');
    expect(localStorage.getItem('happyranch.theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});

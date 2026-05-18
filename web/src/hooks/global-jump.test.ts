import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useGlobalJump } from './global-jump';

function fire(key: string) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

describe('useGlobalJump', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('fires on g+t chord', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    fire('t');
    expect(onJump).toHaveBeenCalledTimes(1);
  });

  it('does not fire when buffer expires', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    vi.advanceTimersByTime(1100);
    fire('t');
    expect(onJump).not.toHaveBeenCalled();
  });

  it('does not fire when focus is in an input', () => {
    const onJump = vi.fn();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useGlobalJump('t', onJump));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 't', bubbles: true }));
    expect(onJump).not.toHaveBeenCalled();
    document.body.removeChild(input);
  });

  it('does not fire on the wrong second letter', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    fire('k');
    expect(onJump).not.toHaveBeenCalled();
  });
});

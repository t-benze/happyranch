import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import {
  _resetGlobalJumpForTests,
  isGPrefixArmed,
  useGlobalJump,
} from './global-jump';

function fire(key: string) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

describe('useGlobalJump', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _resetGlobalJumpForTests();
  });
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

  it('fires the g+g chord without re-arming', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('g', onJump));
    fire('g');
    fire('g');
    expect(onJump).toHaveBeenCalledTimes(1);
    expect(isGPrefixArmed()).toBe(false);
  });

  it('dispatches to the correct letter when multiple letters are registered', () => {
    const onG = vi.fn();
    const onT = vi.fn();
    const onI = vi.fn();
    renderHook(() => useGlobalJump('g', onG));
    renderHook(() => useGlobalJump('t', onT));
    renderHook(() => useGlobalJump('i', onI));

    fire('g');
    fire('t');
    expect(onT).toHaveBeenCalledTimes(1);
    expect(onG).not.toHaveBeenCalled();
    expect(onI).not.toHaveBeenCalled();

    fire('g');
    fire('g');
    expect(onG).toHaveBeenCalledTimes(1);

    fire('g');
    fire('i');
    expect(onI).toHaveBeenCalledTimes(1);
  });

  it('exposes the armed state via isGPrefixArmed()', () => {
    renderHook(() => useGlobalJump('t', vi.fn()));
    expect(isGPrefixArmed()).toBe(false);
    fire('g');
    expect(isGPrefixArmed()).toBe(true);
    vi.advanceTimersByTime(1100);
    expect(isGPrefixArmed()).toBe(false);
  });

  it('disarms after the chord completes (so a stray next-key does not jump)', () => {
    const onT = vi.fn();
    renderHook(() => useGlobalJump('t', onT));
    fire('g');
    fire('t');
    expect(onT).toHaveBeenCalledTimes(1);
    expect(isGPrefixArmed()).toBe(false);
    fire('t');
    expect(onT).toHaveBeenCalledTimes(1);
  });
});

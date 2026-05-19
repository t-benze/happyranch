import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useCommandPaletteHotkey } from './command-palette';

function fireCmdK(opts: { meta?: boolean; ctrl?: boolean; target?: EventTarget } = {}) {
  const event = new KeyboardEvent('keydown', {
    key: 'k',
    metaKey: opts.meta ?? false,
    ctrlKey: opts.ctrl ?? false,
    bubbles: true,
    cancelable: true,
  });
  (opts.target ?? window).dispatchEvent(event);
  return event;
}

describe('useCommandPaletteHotkey', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('toggles on Cmd-K', () => {
    const onToggle = vi.fn();
    renderHook(() => useCommandPaletteHotkey(onToggle));
    fireCmdK({ meta: true });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('toggles on Ctrl-K', () => {
    const onToggle = vi.fn();
    renderHook(() => useCommandPaletteHotkey(onToggle));
    fireCmdK({ ctrl: true });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('does nothing on plain `k`', () => {
    const onToggle = vi.fn();
    renderHook(() => useCommandPaletteHotkey(onToggle));
    fireCmdK({});
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('is suppressed inside an input', () => {
    const onToggle = vi.fn();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useCommandPaletteHotkey(onToggle));
    fireCmdK({ meta: true, target: input });
    expect(onToggle).not.toHaveBeenCalled();
  });
});

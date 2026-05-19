/**
 * useCommandPaletteHotkey — global Cmd-K / Ctrl-K listener.
 *
 * Per spec `2026-05-19-web-polish-design.md` §6.1. Suppresses the hotkey
 * when focus is inside an `<input>`, `<textarea>`, or `[contenteditable]`
 * so typing the literal `k` in a search box doesn't slam the palette
 * open. Calls `onToggle` so the host can flip its open state (open if
 * closed, close if open).
 */
import { useEffect } from 'react';

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useCommandPaletteHotkey(onToggle: () => void): void {
  useEffect(() => {
    const handler = (ev: KeyboardEvent) => {
      const isCmdK =
        (ev.metaKey || ev.ctrlKey) && (ev.key === 'k' || ev.key === 'K');
      if (!isCmdK) return;
      if (isInEditable(ev.target)) return;
      ev.preventDefault();
      onToggle();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onToggle]);
}

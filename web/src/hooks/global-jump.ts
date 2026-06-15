/**
 * Global `g <letter>` jump-key chord.
 *
 * One listener is installed at module load (the first time `useGlobalJump` is
 * mounted) and dispatches the second key to a shared handler map. The single
 * source of truth for "is the `g` prefix currently armed?" lives here too, so
 * page-level single-key handlers (`ThreadsPage`'s `i`, `TasksPage`'s `d`,
 * etc.) can skip themselves while a chord is in flight by calling
 * `isGPrefixArmed()`.
 *
 * Re-entrancy hazard the previous implementation tripped on: when each
 * `useGlobalJump(letter, cb)` registered its own `keydown` listener, every
 * instance re-armed the prefix on the first `g` press, and the chord case
 * `g g` was unreachable because the `if (ev.key === 'g') return;` branch ran
 * before any letter-match check. Centralizing the state in one listener
 * removes both problems.
 */
import { useEffect } from 'react';

const BUFFER_MS = 1000;

let armedAt: number | null = null;
let listenerInstalled = false;
const handlers = new Map<string, () => void>();

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

function disarm(): void {
  armedAt = null;
}

function fire(letter: string, ev: KeyboardEvent): boolean {
  const cb = handlers.get(letter);
  if (!cb) return false;
  disarm();
  ev.preventDefault();
  cb();
  return true;
}

function handle(ev: KeyboardEvent): void {
  if (isInEditable(ev.target)) return;
  const now = Date.now();
  const armed = armedAt !== null && now - armedAt <= BUFFER_MS;

  if (ev.key === 'g') {
    // Special-case `g g`: if the prefix is already armed, the second `g` is
    // the completing keypress, not a fresh re-arm. Otherwise this branch
    // would re-arm and `g g` could never fire.
    if (armed && fire('g', ev)) return;
    armedAt = now;
    return;
  }

  if (!armed) {
    disarm();
    return;
  }
  if (!fire(ev.key, ev)) {
    // Wrong second key — discard the prefix so an unrelated keypress doesn't
    // get hijacked later.
    disarm();
  }
}

function installListener(): void {
  if (listenerInstalled) return;
  listenerInstalled = true;
  if (typeof window === 'undefined') return;
  window.addEventListener('keydown', handle);
}

export function useGlobalJump(letter: string, onJump: () => void): void {
  useEffect(() => {
    installListener();
    handlers.set(letter, onJump);
    return () => {
      // Only delete this letter's binding if no other consumer replaced it.
      if (handlers.get(letter) === onJump) handlers.delete(letter);
    };
  }, [letter, onJump]);
}

/**
 * True when the `g` prefix is currently armed (i.e., the founder has pressed
 * `g` within the chord buffer). Page-level single-key handlers should
 * consult this before firing actions like Threads's `i` (Invite) or Tasks's
 * `d` (Dispatch) — otherwise `g i` / `g d` both jump AND open the dialog.
 */
export function isGPrefixArmed(): boolean {
  if (armedAt === null) return false;
  return Date.now() - armedAt <= BUFFER_MS;
}

/** Test-only escape hatch: reset module-level state between specs. */
export function _resetGlobalJumpForTests(): void {
  armedAt = null;
  handlers.clear();
}

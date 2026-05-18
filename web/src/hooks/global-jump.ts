import { useEffect, useRef } from 'react';

const BUFFER_MS = 1000;

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useGlobalJump(letter: string, onJump: () => void): void {
  const armedAt = useRef<number | null>(null);

  useEffect(() => {
    const handler = (ev: KeyboardEvent) => {
      if (isInEditable(ev.target)) return;
      const now = Date.now();
      if (ev.key === 'g') {
        armedAt.current = now;
        return;
      }
      if (ev.key === letter && armedAt.current !== null) {
        if (now - armedAt.current <= BUFFER_MS) {
          armedAt.current = null;
          onJump();
        } else {
          armedAt.current = null;
        }
      } else {
        armedAt.current = null;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [letter, onJump]);
}

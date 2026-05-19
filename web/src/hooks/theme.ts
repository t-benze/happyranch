/**
 * useTheme — light/dark mode persistence + `<html data-theme>` swap.
 *
 * Per spec `2026-05-19-web-polish-design.md` §3. Mirrors useDensity:
 * localStorage-backed, dark by default. On change, writes the attribute
 * synchronously so the CSS variable override under
 * `:root[data-theme="light"]` flips immediately.
 *
 * A `storage` listener keeps two open tabs in sync without a refresh.
 */
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';
const KEY = 'grassland.theme';

function readInitial(): Theme {
  if (typeof window === 'undefined') return 'dark';
  const v = window.localStorage.getItem(KEY);
  return v === 'light' ? 'light' : 'dark';
}

function applyAttribute(theme: Theme): void {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', theme);
}

export function useTheme(): {
  theme: Theme;
  setTheme: (t: Theme) => void;
} {
  const [theme, setThemeState] = useState<Theme>(readInitial);

  useEffect(() => {
    applyAttribute(theme);
  }, [theme]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onStorage = (ev: StorageEvent) => {
      if (ev.key !== KEY) return;
      const next = ev.newValue === 'light' ? 'light' : 'dark';
      setThemeState(next);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    if (typeof window !== 'undefined') window.localStorage.setItem(KEY, t);
    applyAttribute(t);
  }, []);

  return { theme, setTheme };
}

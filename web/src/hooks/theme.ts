/**
 * useTheme — light/dark mode persistence + `<html data-theme>` swap.
 *
 * Direction-A "Pasture" light-first. localStorage-backed, LIGHT by default.
 * On change, writes the attribute synchronously so the CSS variable override
 * under `:root[data-theme="dark"]` flips immediately.
 *
 * A `storage` listener keeps two open tabs in sync without a refresh.
 */
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';
const KEY = 'happyranch.theme';

function readInitial(): Theme {
  if (typeof window === 'undefined') return 'light';
  const v = window.localStorage.getItem(KEY);
  return v === 'dark' ? 'dark' : 'light';
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
      const next = ev.newValue === 'dark' ? 'dark' : 'light';
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

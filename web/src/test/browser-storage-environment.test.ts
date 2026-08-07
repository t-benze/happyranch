import { describe, expect, test } from 'vitest';

describe('Vitest browser storage environment', () => {
  test('provides origin-backed storage through window and unqualified globals', () => {
    expect(window.localStorage).toBe(globalThis.localStorage);
    expect(window.sessionStorage).toBe(globalThis.sessionStorage);

    window.localStorage.setItem('window-local-key', 'window-local-value');
    globalThis.localStorage.setItem('global-local-key', 'global-local-value');
    window.sessionStorage.setItem('window-session-key', 'window-session-value');
    globalThis.sessionStorage.setItem('global-session-key', 'global-session-value');

    expect(globalThis.localStorage.getItem('window-local-key')).toBe('window-local-value');
    expect(window.localStorage.getItem('global-local-key')).toBe('global-local-value');
    expect(globalThis.sessionStorage.getItem('window-session-key')).toBe(
      'window-session-value',
    );
    expect(window.sessionStorage.getItem('global-session-key')).toBe(
      'global-session-value',
    );

    globalThis.localStorage.clear();
    globalThis.sessionStorage.clear();

    expect(window.localStorage.getItem('window-local-key')).toBeNull();
    expect(window.sessionStorage.getItem('window-session-key')).toBeNull();
  });
});

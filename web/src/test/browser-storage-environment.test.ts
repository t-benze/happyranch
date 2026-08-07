import { describe, expect, test } from 'vitest';

describe('Vitest browser storage environment', () => {
  test('provides origin-backed storage through window and unqualified globals', () => {
    expect(window.localStorage).toBe(globalThis.localStorage);
    expect(window.sessionStorage).toBe(globalThis.sessionStorage);

    localStorage.setItem('local-key', 'local-value');
    sessionStorage.setItem('session-key', 'session-value');

    expect(window.localStorage.getItem('local-key')).toBe('local-value');
    expect(window.sessionStorage.getItem('session-key')).toBe('session-value');

    window.localStorage.clear();
    window.sessionStorage.clear();

    expect(localStorage.getItem('local-key')).toBeNull();
    expect(sessionStorage.getItem('session-key')).toBeNull();
  });
});

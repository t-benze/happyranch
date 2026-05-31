import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { server } from '../test/server';
import { __resetTokenCacheForTests, getToken } from './auth';

describe('getToken', () => {
  beforeEach(() => {
    __resetTokenCacheForTests();
  });
  afterEach(() => {
    __resetTokenCacheForTests();
  });

  test('fetches token from /api/v1/auth/bootstrap on cold start', async () => {
    let calls = 0;
    server.use(
      http.get('/api/v1/auth/bootstrap', () => {
        calls += 1;
        return HttpResponse.json({ token: 'abc123' });
      }),
    );
    const t = await getToken();
    expect(t).toBe('abc123');
    expect(calls).toBe(1);
  });

  test('caches the token in sessionStorage across calls', async () => {
    let calls = 0;
    server.use(
      http.get('/api/v1/auth/bootstrap', () => {
        calls += 1;
        return HttpResponse.json({ token: 'abc123' });
      }),
    );
    await getToken();
    await getToken();
    await getToken();
    expect(calls).toBe(1);
    expect(sessionStorage.getItem('happyranch.token')).toBe('abc123');
  });

  test('reads from sessionStorage if pre-populated', async () => {
    sessionStorage.setItem('happyranch.token', 'pre-existing');
    server.use(
      http.get('/api/v1/auth/bootstrap', () => {
        throw new Error('should not be called');
      }),
    );
    const t = await getToken();
    expect(t).toBe('pre-existing');
  });

  test('throws when bootstrap returns 403', async () => {
    server.use(
      http.get('/api/v1/auth/bootstrap', () =>
        HttpResponse.json({ detail: { code: 'not_localhost' } }, { status: 403 }),
      ),
    );
    await expect(getToken()).rejects.toThrow(/bootstrap/i);
  });
});

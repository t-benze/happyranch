import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import { __resetTokenCacheForTests } from '../auth';
import { ApiError, request } from './client';

function seedToken(token = 'tok') {
  sessionStorage.setItem('happyranch.token', token);
}

describe('request', () => {
  test('returns parsed JSON on 2xx', async () => {
    seedToken();
    server.use(
      http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: ['alpha'] })),
    );
    const data = await request<{ orgs: string[] }>('/orgs');
    expect(data).toEqual({ orgs: ['alpha'] });
  });

  test('attaches Authorization header from sessionStorage', async () => {
    seedToken('my-tok');
    let seen: string | null = null;
    server.use(
      http.get('/api/v1/orgs', ({ request: req }) => {
        seen = req.headers.get('authorization');
        return HttpResponse.json({ ok: true });
      }),
    );
    await request('/orgs');
    expect(seen).toBe('Bearer my-tok');
  });

  test('bootstraps token when sessionStorage is empty', async () => {
    __resetTokenCacheForTests();
    server.use(
      http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'fresh' })),
      http.get('/api/v1/orgs', ({ request: req }) => {
        if (req.headers.get('authorization') !== 'Bearer fresh') {
          return HttpResponse.json({ detail: 'bad' }, { status: 401 });
        }
        return HttpResponse.json({ orgs: [] });
      }),
    );
    const data = await request<{ orgs: string[] }>('/orgs');
    expect(data).toEqual({ orgs: [] });
  });

  test('throws ApiError with structured detail.code on 4xx', async () => {
    seedToken();
    server.use(
      http.post('/api/v1/orgs/alpha/threads', () =>
        HttpResponse.json(
          { detail: { code: 'empty_subject' } },
          { status: 422 },
        ),
      ),
    );
    await expect(
      request('/orgs/alpha/threads', { method: 'POST', body: {} }),
    ).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      code: 'empty_subject',
    });
  });

  test('ApiError with string detail keeps the raw payload', async () => {
    seedToken();
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ detail: 'server exploded' }, { status: 500 }),
      ),
    );
    try {
      await request('/orgs');
      throw new Error('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect(err.status).toBe(500);
      expect(err.code).toBeNull();
      expect(err.detail).toBe('server exploded');
    }
  });

  test('re-bootstraps once on 401', async () => {
    sessionStorage.setItem('happyranch.token', 'stale');
    let bootstrapped = 0;
    let attempts = 0;
    server.use(
      http.get('/api/v1/auth/bootstrap', () => {
        bootstrapped += 1;
        return HttpResponse.json({ token: 'fresh' });
      }),
      http.get('/api/v1/orgs', ({ request: req }) => {
        attempts += 1;
        const tok = req.headers.get('authorization');
        if (tok === 'Bearer fresh') return HttpResponse.json({ orgs: ['ok'] });
        return HttpResponse.json({ detail: 'bad' }, { status: 401 });
      }),
    );
    const data = await request<{ orgs: string[] }>('/orgs');
    expect(data).toEqual({ orgs: ['ok'] });
    expect(bootstrapped).toBe(1);
    expect(attempts).toBe(2);
  });

  test('serializes body to JSON for non-GET requests', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post('/api/v1/orgs/alpha/threads', async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001' }, { status: 201 });
      }),
    );
    const data = await request('/orgs/alpha/threads', {
      method: 'POST',
      body: { subject: 'hi', recipients: ['a'] },
    });
    expect(data).toEqual({ thread_id: 'THR-001' });
    expect(received).toEqual({ subject: 'hi', recipients: ['a'] });
  });

  test('encodes query params', async () => {
    seedToken();
    let queryURL: string | null = null;
    server.use(
      http.get('/api/v1/orgs/alpha/threads', ({ request: req }) => {
        queryURL = req.url;
        return HttpResponse.json({ threads: [] });
      }),
    );
    await request('/orgs/alpha/threads', { params: { status: 'open', limit: 50 } });
    expect(queryURL).toMatch(/status=open/);
    expect(queryURL).toMatch(/limit=50/);
  });
});

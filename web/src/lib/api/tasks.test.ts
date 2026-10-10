import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import { getTaskPauseOverview, pauseTask, resumeTask } from './tasks';

describe('task Pause transport', () => {
  test.each([
    ['pause', pauseTask],
    ['resume', resumeTask],
  ] as const)('%s posts the exact generation once to the selected org and task', async (action, control) => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const calls: unknown[] = [];
    server.use(http.post(`/api/v1/orgs/alpha/tasks/TASK-42/${action}`, async ({ request }) => {
      calls.push(await request.json());
      expect(request.headers.get('authorization')).toBe('Bearer tok');
      return HttpResponse.json({ changed: false, pause: { generation: 7 } });
    }));
    expect(await control('alpha', 'TASK-42', 7)).toEqual({ changed: false, pause: { generation: 7 } });
    expect(calls).toEqual([{ expected_generation: 7 }]);
  });

  test.each([
    ['pause', pauseTask],
    ['resume', resumeTask],
  ] as const)('%s preserves a conflict without rereading or toggling again', async (action, control) => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const requests: string[] = [];
    server.use(http.all('/api/v1/orgs/alpha/tasks/TASK-42/*', ({ request }) => {
      requests.push(`${request.method} ${new URL(request.url).pathname}`);
      return HttpResponse.json({ detail: { code: 'control_generation_conflict', generation: 9 } }, { status: 409 });
    }));
    await expect(control('alpha', 'TASK-42', 7)).rejects.toMatchObject({
      status: 409, code: 'control_generation_conflict',
    });
    expect(requests).toEqual([`POST /api/v1/orgs/alpha/tasks/TASK-42/${action}`]);
  });

  test.each([NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects an unrepresentable generation (%s) before transport', async (generation) => {
      sessionStorage.setItem('happyranch.token', 'tok');
      let requests = 0;
      server.use(http.all('/api/v1/orgs/alpha/tasks/TASK-42/*', () => {
        requests += 1;
        return HttpResponse.json({ changed: true });
      }));
      await expect(pauseTask('alpha', 'TASK-42', generation)).rejects.toBeInstanceOf(RangeError);
      await expect(resumeTask('alpha', 'TASK-42', generation)).rejects.toBeInstanceOf(RangeError);
      expect(requests).toBe(0);
    },
  );

  test('overview keeps the selected org, opaque cursor, null counts and incomplete evidence', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let query: URLSearchParams | undefined;
    const page = { org_slug: 'alpha', evidence_complete: false, counts: null, next_cursor: 'TASK-50',
      unheld_runnable: [{ lifecycle_status: 'pending', effective_hold: false }], pausing: [], paused: [],
      terminal_drain: [], unavailable_roots: [] };
    server.use(http.get('/api/v1/orgs/alpha/tasks/pause-overview', ({ request }) => {
      query = new URL(request.url).searchParams;
      return HttpResponse.json(page);
    }));
    expect(await getTaskPauseOverview('alpha', { limit: 20, before: 'TASK-20/+ opaque' })).toEqual(page);
    expect(Object.fromEntries(query!)).toEqual({ limit: '20', before: 'TASK-20/+ opaque' });
  });
});

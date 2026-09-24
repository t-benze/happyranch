import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { I18nTestBoundary, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { TaskListRow } from './TaskListRow';
import * as api from '@/lib/api';
import { __resetTokenCacheForTests } from '@/lib/auth';
import { useResolveEscalation } from '@/hooks/tasks';
import type { SSEOptions } from '@/lib/api';
import type { ActiveChainResponse, JobRecord, TaskEvent, TaskRecord } from '@/lib/api/types';

beforeEach(() => {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
  );
});

const SLUG = 'hk-macau-tourism';

afterEach(() => {
  vi.restoreAllMocks();
});

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

/** A root task fixture with severity_rollup (roots endpoint field). */
function rootTask(overrides?: Partial<TaskRecord> & Record<string, unknown>): TaskRecord {
  return {
    task_id: 'TASK-0091',
    team: 'content',
    brief: 'Draft Hong Kong visa guide v2',
    status: 'completed',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'completed',
    ...overrides,
  } as TaskRecord;
}

const TASK = rootTask({ status: 'in_progress', severity_rollup: 'in_progress' });

const JOB: JobRecord = {
  id: 'JOB-0001',
  task_id: 'TASK-0091',
  agent_name: 'content_writer',
  title: 'Generate sitemap',
  rationale: 'SEO improvement.',
  script_text: 'python3 gen_sitemap.py',
  interpreter: 'bash',
  cwd_hint: null,
  status: 'completed',
  exit_code: 0,
  stdout_head: null,
  stderr_head: null,
  stdout_path: null,
  stderr_path: null,
  duration_ms: 800,
  started_at: '2026-05-18T10:02:00Z',
  finished_at: '2026-05-18T10:02:01Z',
  reviewed_at: null,
  reviewed_by: null,
  reject_reason: null,
  cwd_resolved: null,
  max_runtime_seconds: 300,
  max_output_bytes: 52428800,
  review_required: false,
  persistent: false,
  reason: null,
  created_at: '2026-05-18T10:01:00Z',
};

describe('TasksPage — read path (roots endpoint)', () => {
  test('uses a non-exact count until every status=escalated page is exhausted', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const firstPage = Array.from({ length: 50 }, (_, index) => rootTask({
      task_id: `TASK-ESC-${index + 11}`,
      brief: `Escalation ${index + 11}`,
      status: 'escalated',
      severity_rollup: 'escalated',
    }));
    const finalPage = Array.from({ length: 10 }, (_, index) => rootTask({
      task_id: `TASK-ESC-${index + 1}`,
      brief: `Escalation ${index + 1}`,
      status: 'escalated',
      severity_rollup: 'escalated',
    }));
    const attentionRequests: Record<string, string>[] = [];
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = Object.fromEntries(new URL(request.url).searchParams);
      if (params.status !== 'escalated') return HttpResponse.json({ tasks: [], next_cursor: null });
      attentionRequests.push(params);
      return HttpResponse.json(params.before
        ? { tasks: finalPage, next_cursor: null }
        : { tasks: firstPage, next_cursor: 'TASK-ESC-11' });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByText('50+ waiting on you')).toBeInTheDocument();
    expect(screen.queryByText('50 waiting on you')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Load more waiting-on-you tasks' }));
    expect(await screen.findByText('60 waiting on you')).toBeInTheDocument();
    expect(attentionRequests).toEqual([
      { status: 'escalated', limit: '50' },
      { status: 'escalated', limit: '50', before: 'TASK-ESC-11' },
    ]);
  });

  test('deduplicates an escalated root that occurs in both traversals', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const shared = rootTask({
      task_id: 'TASK-SHARED-ESC', brief: 'Shared escalation', status: 'escalated', severity_rollup: 'escalated',
    });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) =>
      HttpResponse.json(new URL(request.url).searchParams.get('status') === 'escalated'
        ? { tasks: [shared], next_cursor: null }
        : { tasks: [shared, rootTask({ task_id: 'TASK-ORDINARY', brief: 'Ordinary root' })], next_cursor: null }),
    ));

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByText('Shared escalation')).toBeInTheDocument();
    expect(screen.getAllByText('Shared escalation')).toHaveLength(1);
    expect(screen.getByText('Ordinary root')).toBeInTheDocument();
  });

  test('keeps ordinary results usable when the attention traversal fails and retries independently', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let attentionAttempts = 0;
    const escalated = rootTask({
      task_id: 'TASK-RECOVERED-ESC', brief: 'Recovered escalation', status: 'escalated', severity_rollup: 'escalated',
    });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      if (new URL(request.url).searchParams.get('status') !== 'escalated') {
        return HttpResponse.json({ tasks: [rootTask({ task_id: 'TASK-ORDINARY', brief: 'Ordinary remains visible' })], next_cursor: null });
      }
      attentionAttempts += 1;
      return attentionAttempts === 1
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ tasks: [escalated], next_cursor: null });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByText('Ordinary remains visible')).toBeInTheDocument();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load waiting-on-you tasks');
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Recovered escalation')).toBeInTheDocument();
    expect(attentionAttempts).toBe(2);
  });

  test('C08 resolves through the mounted provider then refetches both streams into the final waiting rows', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let resolved = false;
    const oldEscalation = rootTask({
      task_id: 'TASK-RESOLVED-ESC', brief: 'Escalation now resolved', status: 'escalated', severity_rollup: 'escalated',
    });
    const promotedRoot = rootTask({
      task_id: 'TASK-PROMOTED-ESC', brief: 'New escalation after refetch', status: 'pending', severity_rollup: 'pending',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
        const attention = new URL(request.url).searchParams.get('status') === 'escalated';
        if (!resolved) return HttpResponse.json({ tasks: attention ? [oldEscalation] : [oldEscalation, promotedRoot], next_cursor: null });
        return HttpResponse.json({
          tasks: attention
            ? [{ ...promotedRoot, status: 'escalated', severity_rollup: 'escalated' }]
            : [{ ...oldEscalation, status: 'resolved', severity_rollup: 'resolved' }, { ...promotedRoot, status: 'escalated', severity_rollup: 'escalated' }],
          next_cursor: null,
        });
      }),
      http.post(`/api/v1/orgs/${SLUG}/tasks/TASK-RESOLVED-ESC/resolve-escalation`, () => {
        resolved = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    function ResolveButton() {
      const resolve = useResolveEscalation('TASK-RESOLVED-ESC');
      return <button onClick={() => void resolve.mutateAsync({ decision: 'continue', rationale: 'Founder resolution' })}>Resolve waiting task</button>;
    }
    const queryClient = makeQueryClient();
    render(<MemoryRouter initialEntries={[`/orgs/${SLUG}/tasks`]}><AppProvider client={queryClient}>
      <Routes><Route path="/orgs/:slug/tasks" element={<ResolveButton />} /></Routes><I18nTestBoundary><AppRoutes /></I18nTestBoundary>
    </AppProvider></MemoryRouter>);
    await screen.findByText('Escalation now resolved');
    expect(screen.getAllByText('Escalation now resolved')).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Resolve waiting task' }));
    await screen.findByText('New escalation after refetch');
    const waiting = screen.getByRole('heading', { name: 'Waiting on you' }).closest('.tasks-group') as HTMLElement;
    expect(within(waiting).queryByText('Escalation now resolved')).not.toBeInTheDocument();
    expect(within(waiting).getByText('New escalation after refetch')).toBeInTheDocument();
    expect(screen.getAllByText('New escalation after refetch')).toHaveLength(1);
    expect(screen.getByText('1 waiting on you')).toBeInTheDocument();
    await queryClient.cancelQueries();
    queryClient.clear();
  });

  test('does not render a late attention response from a prior org', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let releaseOld!: () => void;
    const oldAttention = new Promise<void>((resolve) => { releaseOld = resolve; });
    const oldEscalation = rootTask({
      task_id: 'TASK-ORG-A-ESC', brief: 'Old org escalation', status: 'escalated', severity_rollup: 'escalated',
    });
    const newEscalation = rootTask({
      task_id: 'TASK-ORG-B-ESC', brief: 'Current org escalation', status: 'escalated', severity_rollup: 'escalated',
    });
    server.use(http.get('/api/v1/orgs/:slug/tasks/roots', async ({ request, params }) => {
      const status = new URL(request.url).searchParams.get('status');
      if (status !== 'escalated') return HttpResponse.json({ tasks: [], next_cursor: null });
      if (params.slug === 'org-a') {
        await oldAttention;
        return HttpResponse.json({ tasks: [oldEscalation], next_cursor: null });
      }
      return HttpResponse.json({ tasks: [newEscalation], next_cursor: null });
    }));
    const queryClient = makeQueryClient();
    render(
      <MemoryRouter initialEntries={['/orgs/org-a/tasks']}>
        <AppProvider client={queryClient}>
          <Link to="/orgs/org-b/tasks">Go org b</Link><I18nTestBoundary><AppRoutes /></I18nTestBoundary>
        </AppProvider>
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole('link', { name: 'Go org b' }));
    expect(await screen.findByText('Current org escalation')).toBeInTheDocument();
    releaseOld();
    await waitFor(() => expect(screen.queryByText('Old org escalation')).not.toBeInTheDocument());
    expect(screen.getByText('Current org escalation')).toBeInTheDocument();
    await queryClient.cancelQueries();
    queryClient.clear();
  });

  test('settles both old-org streams before keeping only the filtered current-org rows', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let releaseOldOrdinary!: () => void;
    let releaseOldAttention!: () => void;
    const oldOrdinary = new Promise<void>((resolve) => { releaseOldOrdinary = resolve; });
    const oldAttention = new Promise<void>((resolve) => { releaseOldAttention = resolve; });
    const requests: { slug: string; params: Record<string, string>; settled: boolean }[] = [];
    const oldRoot = rootTask({ task_id: 'TASK-ORG-A', brief: 'Old org ordinary root' });
    const oldEscalation = rootTask({ task_id: 'TASK-ORG-A-ESC', brief: 'Old org escalation', status: 'escalated', severity_rollup: 'escalated' });
    const currentRoot = rootTask({ task_id: 'TASK-ORG-B', brief: 'Current org ordinary root' });
    const currentFiltered = rootTask({ task_id: 'TASK-ORG-B-COMPLETE', brief: 'Current org filtered root', status: 'completed', severity_rollup: 'completed' });
    const currentEscalation = rootTask({ task_id: 'TASK-ORG-B-ESC', brief: 'Current org escalation', status: 'escalated', severity_rollup: 'escalated' });
    server.use(http.get('/api/v1/orgs/:slug/tasks/roots', async ({ request, params }) => {
      const url = new URL(request.url);
      const status = url.searchParams.get('status');
      const receipt = { slug: String(params.slug), params: Object.fromEntries(url.searchParams), settled: false };
      requests.push(receipt);
      if (params.slug === 'org-a') await (status === 'escalated' ? oldAttention : oldOrdinary);
      receipt.settled = true;
      if (params.slug === 'org-a') return HttpResponse.json({ tasks: status === 'escalated' ? [oldEscalation] : [oldRoot], next_cursor: null });
      if (status === 'escalated') return HttpResponse.json({ tasks: [currentEscalation], next_cursor: null });
      return HttpResponse.json({ tasks: status === 'completed' ? [currentFiltered] : [currentRoot], next_cursor: null });
    }));
    const interceptedFetch = globalThis.fetch;
    let releaseBodies!: () => void;
    const bodyGate = new Promise<void>((resolve) => { releaseBodies = resolve; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (...args) => {
      const response = await interceptedFetch(...args);
      if (String(args[0]).includes('/orgs/org-a/tasks/roots')) {
        const originalText = response.text.bind(response);
        response.text = async () => { await bodyGate; return originalText(); };
      }
      return response;
    });
    const queryClient = makeQueryClient();
    render(<MemoryRouter initialEntries={['/orgs/org-a/tasks']}><AppProvider client={queryClient}>
      <Link to="/orgs/org-b/tasks">Go org b</Link><I18nTestBoundary><AppRoutes /></I18nTestBoundary>
    </AppProvider></MemoryRouter>);

    await waitFor(() => expect(requests.filter((request) => request.slug === 'org-a')).toHaveLength(2));
    expect(requests.filter((request) => request.slug === 'org-a').map((request) => request.params))
      .toEqual(expect.arrayContaining([{ limit: '50' }, { status: 'escalated', limit: '50' }]));
    await userEvent.click(screen.getByRole('link', { name: 'Go org b' }));
    expect(await screen.findByText('Current org ordinary root')).toBeInTheDocument();
    expect(await screen.findByText('Current org escalation')).toBeInTheDocument();
    await waitFor(() => expect(requests.filter((request) => request.slug === 'org-b')).toHaveLength(2));
    expect(requests.filter((request) => request.slug === 'org-b').map((request) => request.params))
      .toEqual(expect.arrayContaining([{ limit: '50' }, { status: 'escalated', limit: '50' }]));

    releaseOldOrdinary();
    releaseOldAttention();
    await waitFor(() => expect(requests.filter((request) => request.slug === 'org-a').every((request) => request.settled)).toBe(true));
    releaseBodies();
    const oldOrdinaryKey = ['tasks-roots-infinite', 'org-a', undefined] as const;
    const oldAttentionKey = ['tasks-roots-infinite', 'org-a', { status: 'escalated' }] as const;
    const currentOrdinaryKey = ['tasks-roots-infinite', 'org-b', undefined] as const;
    const currentAttentionKey = ['tasks-roots-infinite', 'org-b', { status: 'escalated' }] as const;
    await waitFor(() => {
      for (const key of [oldOrdinaryKey, oldAttentionKey, currentOrdinaryKey, currentAttentionKey]) {
        expect(queryClient.getQueryState(key)).toMatchObject({ status: 'success', fetchStatus: 'idle' });
      }
    });
    expect(queryClient.getQueryData(oldOrdinaryKey)).toEqual({ pages: [{ tasks: [oldRoot], next_cursor: null }], pageParams: [undefined] });
    expect(queryClient.getQueryData(oldAttentionKey)).toEqual({ pages: [{ tasks: [oldEscalation], next_cursor: null }], pageParams: [undefined] });
    expect(queryClient.getQueryData(currentOrdinaryKey)).toEqual({ pages: [{ tasks: [currentRoot], next_cursor: null }], pageParams: [undefined] });
    expect(queryClient.getQueryData(currentAttentionKey)).toEqual({ pages: [{ tasks: [currentEscalation], next_cursor: null }], pageParams: [undefined] });
    expect(screen.queryByText('Old org ordinary root')).not.toBeInTheDocument();
    expect(screen.queryByText('Old org escalation')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
    await userEvent.selectOptions(screen.getByLabelText('Task status'), 'completed');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByText('Current org filtered root')).toBeInTheDocument();
    expect(screen.getByText('Current org escalation')).toBeInTheDocument();
    expect(screen.queryByText('Current org ordinary root')).not.toBeInTheDocument();
    expect(requests.filter((request) => request.slug === 'org-b').map((request) => request.params))
      .toContainEqual({ status: 'completed', limit: '50' });
    const currentFilteredKey = ['tasks-roots-infinite', 'org-b', { status: 'completed' }] as const;
    await waitFor(() => expect(queryClient.getQueryState(currentFilteredKey)).toMatchObject({ status: 'success', fetchStatus: 'idle' }));
    expect(queryClient.getQueryData(currentFilteredKey)).toEqual({ pages: [{ tasks: [currentFiltered], next_cursor: null }], pageParams: [undefined] });
    expect(screen.queryByText('Old org ordinary root')).not.toBeInTheDocument();
    expect(screen.queryByText('Old org escalation')).not.toBeInTheDocument();
    expect(screen.getByText('Current org filtered root')).toBeInTheDocument();
    expect(screen.getByText('Current org escalation')).toBeInTheDocument();
    await queryClient.cancelQueries();
    queryClient.clear();
  });

  test('keeps an older attention row through ordinary page two and deduplicates later-page overlap', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let intersect: IntersectionObserverCallback | undefined;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) { intersect = callback; }
      observe() {}
      disconnect() {}
      unobserve() {}
      takeRecords() { return []; }
      root = null;
      rootMargin = '';
      thresholds = [];
    });
    const oldEscalation = rootTask({ task_id: 'TASK-OLD-ESC', brief: 'Older escalation', status: 'escalated', severity_rollup: 'escalated' });
    const laterEscalation = rootTask({ task_id: 'TASK-LATER-ESC', brief: 'Later escalation', status: 'escalated', severity_rollup: 'escalated' });
    const firstOrdinary = rootTask({ task_id: 'TASK-ORD-ONE', brief: 'First ordinary root' });
    const laterOrdinary = rootTask({ task_id: 'TASK-ORD-TWO', brief: 'Second ordinary root' });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('status') === 'escalated') {
        return HttpResponse.json(params.has('before')
          ? { tasks: [oldEscalation, laterEscalation], next_cursor: null }
          : { tasks: [oldEscalation], next_cursor: 'attention-2' });
      }
      return HttpResponse.json(params.has('before')
        ? { tasks: [oldEscalation, laterOrdinary], next_cursor: null }
        : { tasks: [firstOrdinary], next_cursor: 'ordinary-2' });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);
    expect(await screen.findByText('Older escalation')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Load more waiting-on-you tasks' }));
    expect(await screen.findByText('Later escalation')).toBeInTheDocument();
    await act(async () => intersect?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(await screen.findByText('Second ordinary root')).toBeInTheDocument();
    expect(screen.getAllByText('Older escalation')).toHaveLength(1);
    expect(screen.getAllByText('Later escalation')).toHaveLength(1);
    expect(screen.getAllByText('First ordinary root')).toHaveLength(1);
    expect(screen.getAllByText('Second ordinary root')).toHaveLength(1);
  });

  test('shows an older escalated root from its independent status query without claiming a partial exact count', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const requests: Record<string, string>[] = [];
    const ordinary = rootTask({ task_id: 'TASK-ORD', brief: 'Newest ordinary root' });
    const escalated = rootTask({
      task_id: 'TASK-ESC-OLD',
      brief: 'Older founder decision',
      status: 'escalated',
      severity_rollup: 'escalated',
    });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = Object.fromEntries(new URL(request.url).searchParams);
      requests.push(params);
      return HttpResponse.json(params.status === 'escalated'
        ? { tasks: [escalated], next_cursor: 'TASK-ESC-OLD' }
        : { tasks: [ordinary], next_cursor: null });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByRole('heading', { name: 'Waiting on you' })).toBeInTheDocument();
    expect(screen.getByText('Older founder decision')).toBeInTheDocument();
    expect(screen.getByText('50+ waiting on you')).toBeInTheDocument();
    expect(screen.queryByText('1 WAITING ON YOU')).not.toBeInTheDocument();
    expect(requests).toEqual([
      { limit: '50' },
      { status: 'escalated', limit: '50' },
    ]);
  });

  test('keeps Waiting on you visible when the ordinary roots traversal is empty', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const escalated = rootTask({
      task_id: 'TASK-ESC-ONLY',
      brief: 'Founder decision without ordinary roots',
      status: 'escalated',
      severity_rollup: 'escalated',
    });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      return HttpResponse.json(status === 'escalated'
        ? { tasks: [escalated], next_cursor: null }
        : { tasks: [], next_cursor: null });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);

    const heading = await screen.findByRole('heading', { name: 'Waiting on you' });
    expect(screen.getByText('Founder decision without ordinary roots')).toBeInTheDocument();
    expect(screen.getByText('1 waiting on you')).toBeInTheDocument();
    // The escalated group is rendered inside the shared list shell, after the
    // column header, even when the ordinary traversal returns zero rows.
    const list = screen.getByTestId('tasks-responsive-list');
    expect(within(list).getByRole('heading', { name: 'Waiting on you' })).toBe(heading);
    expect(list.querySelector('.tasks-column-header')).not.toBeNull();
    expect(list.querySelector('[aria-labelledby="waiting-on-you-heading"] li')).not.toBeNull();
    // 'No tasks' would contradict the visible escalated row.
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    // Responsive coverage now flows through the shared list shell.
    expect(screen.getByTestId('tasks-responsive-styles')).toHaveTextContent('@media (max-width: 767px)');
    expect(screen.getByTestId('tasks-responsive-styles')).not.toHaveTextContent('data-waiting-on-you-responsive-list');
  });

  test('keeps initial loading distinct from empty', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ tasks: [] });
      }),
    );

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
  });

  test('renders an initial request failure with a keyboard-actionable Retry, never empty', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let requests = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () => {
        requests += 1;
        return requests === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({ tasks: [TASK], next_cursor: null });
      }),
    );
    const user = userEvent.setup();

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByText('Could not load tasks')).toBeInTheDocument();
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    const retry = screen.getByRole('button', { name: 'Retry' });
    retry.focus();
    expect(retry).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(await screen.findByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
    expect(requests).toBe(3);
  });

  test('reserves the empty state for a successful zero-row response', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [], next_cursor: null }),
      ),
    );

    mountAt(`/orgs/${SLUG}/tasks`);

    expect(await screen.findByText('No tasks')).toBeInTheDocument();
    expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
  });

  test.each(['unfiltered', 'filtered'] as const)(
    'C09 %s retains two cached pages through invalidation500, keyboard Retry500, then Retry200',
    async (context) => {
      __resetTokenCacheForTests();
      sessionStorage.clear();
      sessionStorage.setItem('happyranch.token', 'synthetic-c09');
      const queryClient = makeQueryClient();
      const params = context === 'filtered'
        ? { status: 'in_progress', assigned_agent: 'agent-c09' } : undefined;
      const key = ['tasks-roots-infinite', SLUG, params];
      const first = rootTask({ ...TASK, assigned_agent: 'agent-c09' });
      const second = rootTask({ ...first, task_id: 'TASK-0092', brief: 'Cached second page' });
      const cached = {
        pages: [
          { tasks: [first], next_cursor: 'page-2' },
          { tasks: [second], next_cursor: null },
        ],
        pageParams: [undefined, 'page-2'],
      };
      // Seed both keys so selecting the filtered context causes no setup HTTP.
      queryClient.setQueryData(['tasks-roots-infinite', SLUG, undefined], cached);
      queryClient.setQueryData(key, cached);
      let shouldFail = true;
      const ledger: { pathname: string; params: Record<string, string>; bearer: string | null }[] = [];
      server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('status') === 'escalated') {
          return HttpResponse.json({ tasks: [], next_cursor: null });
        }
        ledger.push({ pathname: url.pathname, params: Object.fromEntries(url.searchParams),
          bearer: request.headers.get('authorization') });
        if (shouldFail) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(url.searchParams.has('before')
          ? { tasks: [{ ...second, brief: 'Recovered second page' }], next_cursor: null }
          : { tasks: [first], next_cursor: 'page-2' });
      }));
      function Location() {
        const location = useLocation();
        return <output aria-label="C09 current URL">{location.pathname}</output>;
      }
      const mounted = render(
        <MemoryRouter initialEntries={[`/orgs/${SLUG}/tasks`]}>
          <AppProvider client={queryClient}><Location /><I18nTestBoundary><AppRoutes /></I18nTestBoundary></AppProvider>
        </MemoryRouter>,
      );
      const user = userEvent.setup();
      const requestAt = (before?: string) => ({ pathname: `/api/v1/orgs/${SLUG}/tasks/roots`,
        params: { ...params, limit: '50', ...(before ? { before } : {}) },
        bearer: 'Bearer synthetic-c09' });
      function inventory(secondBrief: string) {
        const rows = within(screen.getByTestId('tasks-responsive-list')).getAllByRole('listitem');
        expect(rows.map((row) => within(row).getByRole('link').getAttribute('href')).sort())
          .toEqual([`/orgs/${SLUG}/tasks/TASK-0091`, `/orgs/${SLUG}/tasks/TASK-0092`]);
        expect(screen.getByText(first.brief)).toBeInTheDocument();
        expect(screen.getByText(secondBrief)).toBeInTheDocument();
        expect(screen.getByLabelText('C09 current URL').textContent).toBe(`/orgs/${SLUG}/tasks`);
        if (params) {
          expect(screen.getByText(/Applied filters:/).textContent)
            .toBe('Applied filters: status = in_progress assigned agent = agent-c09');
        } else expect(screen.queryByText(/Applied filters:/)).not.toBeInTheDocument();
      }
      async function failed(attempts: number) {
        await waitFor(() => {
          expect(queryClient.getQueryState(key)).toMatchObject({ status: 'error', fetchStatus: 'idle' });
          expect(within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' })).toBeEnabled();
        });
        expect(screen.getByRole('alert')).toHaveTextContent('Tasks may be out of date');
        inventory('Cached second page');
        expect(queryClient.getQueryData(key)).toEqual(cached);
        for (const text of ['No tasks', 'End of list', 'Could not load tasks', 'Recovered second page']) {
          expect(screen.queryByText(text)).not.toBeInTheDocument();
        }
        expect(ledger).toEqual(Array.from({ length: attempts }, () => requestAt()));
      }
      async function keyboardRetry() {
        const retry = within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' });
        expect(retry).toBeEnabled(); retry.focus(); expect(retry).toHaveFocus();
        await user.keyboard('{Enter}');
      }
      try {
        await screen.findByText('Cached second page');
        if (params) {
          await user.click(screen.getByRole('button', { name: 'Filter' }));
          await user.selectOptions(screen.getByLabelText('Task status'), params.status);
          await user.type(screen.getByLabelText('Assigned agent (exact name)'), params.assigned_agent);
          await user.click(screen.getByRole('button', { name: 'Apply' }));
        }
        inventory('Cached second page');
        expect(ledger).toEqual([]);
        await act(() => queryClient.invalidateQueries({ queryKey: key, exact: true }));
        await failed(1);
        await keyboardRetry();
        await failed(2);
        shouldFail = false;
        await keyboardRetry();
        await screen.findByText('Recovered second page');
        await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
        inventory('Recovered second page');
        expect(screen.queryByText('Cached second page')).not.toBeInTheDocument();
        expect(screen.queryByText('Tasks may be out of date')).not.toBeInTheDocument();
        expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
        expect(screen.getByText('End of list')).toBeInTheDocument();
        expect(queryClient.getQueryState(key)).toMatchObject({ status: 'success', fetchStatus: 'idle' });
        expect(queryClient.getQueryData(key)).toEqual({ ...cached, pages: [cached.pages[0],
          { tasks: [{ ...second, brief: 'Recovered second page' }], next_cursor: null }] });
        expect(ledger).toEqual([requestAt(), requestAt(), requestAt(), requestAt('page-2')]);
        if (params) expect(queryClient.getQueryData(['tasks-roots-infinite', SLUG, undefined])).toEqual(cached);
      } finally {
        mounted.unmount();
        await queryClient.cancelQueries(); queryClient.clear();
        __resetTokenCacheForTests(); sessionStorage.clear();
      }
    },
  );

  test('retains the first page when fetching the next page fails and retries that page only', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let intersect: IntersectionObserverCallback | undefined;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) { intersect = callback; }
      observe() {}
      disconnect() {}
      unobserve() {}
      takeRecords() { return []; }
      root = null;
      rootMargin = '';
      thresholds = [];
    });
    const requestedBefore: string[] = [];
    let nextAttempts = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
        const before = new URL(request.url).searchParams.get('before') ?? 'first';
        if (new URL(request.url).searchParams.get('status') === 'escalated') {
          return HttpResponse.json({ tasks: [], next_cursor: null });
        }
        requestedBefore.push(before);
        if (before === 'first') {
          return HttpResponse.json({ tasks: [TASK], next_cursor: 'page-2' });
        }
        nextAttempts += 1;
        return nextAttempts === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({
              tasks: [rootTask({ task_id: 'TASK-0092', brief: 'Recovered next page' })],
              next_cursor: null,
            });
      }),
    );

    mountAt(`/orgs/${SLUG}/tasks`);
    expect(await screen.findByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
    await act(async () => intersect?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));

    expect(await screen.findByText('Could not load more tasks')).toBeInTheDocument();
    expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
    expect(screen.queryByText('End of list')).not.toBeInTheDocument();
    expect(screen.queryByText('Loading more…')).not.toBeInTheDocument();

    const retry = screen.getByRole('button', { name: 'Retry loading more tasks' });
    retry.focus();
    expect(retry).toHaveFocus();
    await userEvent.keyboard('{Enter}');

    expect(await screen.findByText('Recovered next page')).toBeInTheDocument();
    expect(screen.queryByText('Could not load more tasks')).not.toBeInTheDocument();
    expect(await screen.findByText('End of list')).toBeInTheDocument();
    expect(requestedBefore).toEqual(['first', 'page-2', 'page-2']);
  });

  test('provides a bounded mobile layout while retaining the desktop table', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK], next_cursor: null }),
      ),
    );

    mountAt(`/orgs/${SLUG}/tasks`);
    expect(await screen.findByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
    expect(screen.getByTestId('tasks-responsive-list')).toHaveAttribute('data-tasks-responsive-list');
    expect(screen.getByTestId('tasks-page-header')).toHaveClass('flex-col', 'sm:flex-row');
    expect(screen.getByText('Draft Hong Kong visa guide v2')).toBeInTheDocument();
    expect(document.querySelector('style')?.textContent).toContain('@media (max-width: 767px)');
  });

  test('fetches from /tasks/roots and renders fixture tasks', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() =>
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument(),
    );
  });

  test('renders group-by selector tabs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'What the org is working on' }),
      ).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Status' })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Agent' })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Thread' })).toBeInTheDocument();
    });
  });

  test('groups tasks by status with group heading', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/In progress/)).toBeInTheDocument();
    });
  });

  // TASKS-04: group-by control is a segmented control (not plain text tabs).
  test('renders the group-by control as a bordered segmented control (TASKS-04)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    const tablist = await screen.findByRole('tablist', { name: 'Group by' });
    // Segmented = a grouped, bordered, rounded container — not plain text tabs.
    expect(tablist).toHaveClass('rounded-full');
    expect(tablist).toHaveClass('border');
    // The active segment ('Status', the default) carries the accent fill.
    expect(screen.getByRole('tab', { name: 'Status' })).toHaveClass(
      'data-[state=active]:bg-accent-soft',
    );
  });

  // TASKS-04: group headers carry a count badge + a colored status dot, both
  // pure client-side derivations of the already-loaded roots payload.
  test('group headers carry a count badge and a colored status dot (TASKS-04)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const a = rootTask({
      task_id: 'TASK-0400',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'First running root',
    });
    const b = rootTask({
      task_id: 'TASK-0401',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'Second running root',
    });
    const c = rootTask({
      task_id: 'TASK-0402',
      status: 'pending',
      severity_rollup: 'pending',
      brief: 'Awaiting pickup',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [a, b, c] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    const inProgress = await screen.findByRole('heading', {
      name: /In progress/,
    });
    // Count badge reflects the client-side group size (2 in_progress roots).
    expect(within(inProgress).getByText('2')).toBeInTheDocument();
    // Colored status dot uses the green 'open' token for in_progress.
    const dot = inProgress.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-info');
    // The pending group shows a count of 1.
    const pending = screen.getByRole('heading', { name: /Pending/ });
    expect(within(pending).getByText('1')).toBeInTheDocument();
  });

  test('renders severity_rollup as inline subtitle in the title column', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Root is pending but has an escalated child → severity_rollup = 'escalated'
    // (Path B: escalated is the worst rollup severity).
    const taskWithRollup = rootTask({
      task_id: 'TASK-0100',
      status: 'pending',
      severity_rollup: 'escalated',
      brief: 'Root task that has a stuck child',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [taskWithRollup] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      // STATUS column shows the primary status ('pending'), NOT the rollup.
      expect(screen.getByText('pending')).toBeInTheDocument();
      // The rollup renders inline in the TITLE column as "subtask escalated".
      expect(screen.getByText('subtask escalated')).toBeInTheDocument();
      expect(screen.getByText(/Root task that has a stuck child/)).toBeInTheDocument();
    });
  });

  // TASKS-05: root rows surface the worst-child rollup inline when a descendant
  // sits in a strictly-worse state than the root itself. Pure client-side
  // derivation of severity_rollup vs the root's own status; count-free (the
  // count-decorated design form "1 of 2 subtasks blocked" needs per-status
  // subtask counts that the roots payload does not carry — deferred).
  //
  // Also verifies the STATUS column carries only the compact primary status
  // (no block_kind qualifier) when the rollup matches.
  test('surfaces worst-child subtask rollup inline on root rows (TASKS-05)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Root is in_progress but a descendant is escalated → severity_rollup='escalated'.
    const worseChild = rootTask({
      task_id: 'TASK-0500',
      status: 'in_progress',
      severity_rollup: 'escalated',
      brief: 'Root in progress with a stuck child',
    });
    // Root with no worse descendant (rollup === own status) → no inline rollup.
    const noWorseChild = rootTask({
      task_id: 'TASK-0501',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'Root in progress all subtasks fine',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [worseChild, noWorseChild] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    // The worse-child root names the worst descendant status inline, colored
    // with the escalated token.
    const rollup = await screen.findByText('subtask escalated');
    expect(rollup).toHaveClass('text-attention-text');
    // The healthy root surfaces no inline rollup (no fabricated subtask state).
    expect(screen.queryByText('subtask in progress')).not.toBeInTheDocument();
    // STATUS column for the worse-child root shows compact primary 'in_progress'
    // (the task's own status, NOT the severity rollup).
    const worseRow = screen.getByText('TASK-0500').closest('a')!;
    expect(within(worseRow).getByText('in_progress')).toBeInTheDocument();
    // STATUS column for the no-worse-child root also shows compact 'in_progress'.
    const healthyRow = screen.getByText('TASK-0501').closest('a')!;
    expect(within(healthyRow).getByText('in_progress')).toBeInTheDocument();
  });

  test('stacks subtask rollup under title inside the title column', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const taskWithRollup = rootTask({
      task_id: 'TASK-0502',
      status: 'in_progress',
      severity_rollup: 'escalated',
      brief:
        'A long root title that needs truncation before it can collide with the task id column',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [taskWithRollup] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);

    const rollup = await screen.findByText('subtask escalated');
    const title = screen.getByText(/A long root title/);
    // The rollup lives inside a nested context div, which is a child of the
    // outer title column (the title headline's parent).
    const contextRow = rollup.parentElement!;
    const titleColumn = contextRow.parentElement!;

    expect(titleColumn).toBe(title.parentElement);
    expect(titleColumn).toHaveClass('min-w-0');
    expect(titleColumn?.parentElement).toHaveClass('tasks-grid');
    expect(titleColumn).toHaveClass('flex-col');
    expect(titleColumn).toHaveClass('items-start');
    expect(title).toHaveClass('w-full');
    expect(title).toHaveClass('min-w-0');
    expect(rollup).not.toHaveClass('shrink-0');
    expect(rollup).toHaveClass('max-w-full');
    expect(rollup).toHaveClass('overflow-hidden');
    // Context row clips inside the title column.
    expect(contextRow).toHaveClass('max-w-full');
    expect(contextRow).toHaveClass('overflow-hidden');
    expect(contextRow).toHaveClass('whitespace-nowrap');
  });

  test('groups by thread on dispatched_from_thread_id, with no-thread bucket', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const threaded = rootTask({
      task_id: 'TASK-0200',
      dispatched_from_thread_id: 'THR-0030',
      status: 'in_progress',
      severity_rollup: 'in_progress',
    });
    const unthreaded = rootTask({
      task_id: 'TASK-0201',
      team: 'engineering',
      status: 'pending',
      severity_rollup: 'pending',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [threaded, unthreaded] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    // Switch to the Thread group-by tab
    const user = userEvent.setup();
    const threadTab = await screen.findByRole('tab', { name: 'Thread' });
    await user.click(threadTab);
    await waitFor(() => {
      // THR-0030 appears as the group heading AND as the row's thread chip,
      // so multiple matches are expected; plus a "No thread" group heading.
      expect(screen.getAllByText('THR-0030').length).toBeGreaterThan(0);
      expect(screen.getByText('No thread')).toBeInTheDocument();
    });
  });

  test('renders supersede/revisit links from roots payload fields', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const superseder = rootTask({
      task_id: 'TASK-0300',
      revisit_of_task_id: 'TASK-0299',
      direct_revisits: ['TASK-0301'],
      status: 'completed',
      severity_rollup: 'completed',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [superseder] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/supersedes/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-0299/)).toBeInTheDocument();
      expect(screen.getByText(/superseded by/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-0301/)).toBeInTheDocument();
    });

    // Lineage links carry correct hrefs
    const supersedesLink = screen.getByRole('link', { name: /supersedes TASK-0299/ });
    expect(supersedesLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0299`);
    const supersededByLink = screen.getByRole('link', { name: /superseded by TASK-0301/ });
    expect(supersededByLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0301`);
  });

  test('renders 0 count when query resolves to empty (no loading placeholder)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      // Empty state, not a loading indicator
      expect(screen.getByText(/No tasks match/)).toBeInTheDocument();
    });
  });
});

// THR-221 seq448: the escalated 'Waiting on you' group must be an ordinary
// group (same wrapper/heading/rows-card) ranked FIRST inside the shared list
// shell — not a separate padded box above the column header. Its independent
// status=escalated traversal contract is unchanged.
describe('TasksPage — escalated group is the first ordinary-styled group (THR-221 seq448)', () => {
  function escalatedHandler(escalated: TaskRecord[], ordinary: TaskRecord[]) {
    return http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) =>
      HttpResponse.json(new URL(request.url).searchParams.get('status') === 'escalated'
        ? { tasks: escalated, next_cursor: null }
        : { tasks: ordinary, next_cursor: null }),
    );
  }

  test('renders the escalated group first inside the list shell with the ordinary group styling', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const escalated = rootTask({ task_id: 'TASK-ESC-A', brief: 'Founder decision A', status: 'escalated', severity_rollup: 'escalated' });
    const running = rootTask({ task_id: 'TASK-RUN-A', brief: 'Running root A', status: 'in_progress', severity_rollup: 'in_progress' });
    const failed = rootTask({ task_id: 'TASK-FAIL-A', brief: 'Failed root A', status: 'failed', severity_rollup: 'failed' });
    const completed = rootTask({ task_id: 'TASK-COMP-A', brief: 'Completed root A', status: 'completed', severity_rollup: 'completed' });
    server.use(escalatedHandler([escalated], [running, failed, completed]));

    mountAt(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Founder decision A');

    const list = screen.getByTestId('tasks-responsive-list');
    const headings = within(list).getAllByRole('heading');
    expect(headings.map((h) => h.textContent)).toEqual([
      'Waiting on you',
      'In progress1',
      'Failed1',
      'Completed1',
    ]);

    const header = list.querySelector('.tasks-column-header');
    const escalatedSection = list.querySelector('[aria-labelledby="waiting-on-you-heading"]') as HTMLElement | null;
    expect(header).not.toBeNull();
    expect(escalatedSection).not.toBeNull();
    // The escalated group is a descendant of the shared list shell, positioned
    // after the column header and before every ordinary group.
    expect(header!.compareDocumentPosition(escalatedSection!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(list.querySelector('section')).toBe(escalatedSection);
    expect(within(escalatedSection!).getByText('Founder decision A')).toBeInTheDocument();
    expect(escalatedSection!.querySelectorAll('li')).toHaveLength(1);

    // Same class-token set as an ordinary group rows-card (classList.contains,
    // never a substring/word-boundary match).
    for (const token of ['bg-surface-raised', 'rounded-xl', 'border', 'shadow-sm']) {
      expect(escalatedSection!.classList.contains(token)).toBe(true);
    }
    expect(escalatedSection!.classList.contains('bg-surface-page')).toBe(false);
    expect(escalatedSection!.classList.contains('mx-6')).toBe(false);
    expect(escalatedSection!.classList.contains('p-3')).toBe(false);

    // No outer padded/inset wrapper — the group is exactly the shared wrapper.
    const group = headings[0].closest('.tasks-group') as HTMLElement | null;
    expect(group).not.toBeNull();
    expect(group!.classList.contains('mx-6')).toBe(false);
    expect(group!.classList.contains('p-3')).toBe(false);
    expect(group!.classList.contains('bg-surface-page')).toBe(false);
    // Truthful count note from the independent traversal (exact when exhausted).
    expect(within(group!).getByText('1 waiting on you')).toBeInTheDocument();
  });

  test('renders no escalated group when the attention traversal is empty and keeps ordinary order', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const running = rootTask({ task_id: 'TASK-RUN-B', brief: 'Running root B', status: 'in_progress', severity_rollup: 'in_progress' });
    const failed = rootTask({ task_id: 'TASK-FAIL-B', brief: 'Failed root B', status: 'failed', severity_rollup: 'failed' });
    const completed = rootTask({ task_id: 'TASK-COMP-B', brief: 'Completed root B', status: 'completed', severity_rollup: 'completed' });
    server.use(escalatedHandler([], [running, failed, completed]));

    mountAt(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Running root B');

    const list = screen.getByTestId('tasks-responsive-list');
    expect(within(list).queryByRole('heading', { name: /Waiting on you/ })).toBeNull();
    expect(screen.queryByText(/waiting on you/)).not.toBeInTheDocument();
    expect(list.querySelector('[aria-labelledby="waiting-on-you-heading"]')).toBeNull();
    const headings = within(list).getAllByRole('heading');
    // First heading is the first ordinary status group; Failed still precedes
    // Completed (GROUP_ORDER_STATUS unchanged).
    expect(headings.map((h) => h.textContent)).toEqual(['In progress1', 'Failed1', 'Completed1']);
  });

  test.each(['loading', 'error'] as const)(
    'keeps the list shell and escalated group when the ordinary traversal is %s',
    async (mode) => {
      sessionStorage.setItem('happyranch.token', 'tok');
      const escalated = rootTask({ task_id: 'TASK-ESC-C', brief: 'Founder decision C', status: 'escalated', severity_rollup: 'escalated' });
      server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, async ({ request }) => {
        if (new URL(request.url).searchParams.get('status') === 'escalated') {
          return HttpResponse.json({ tasks: [escalated], next_cursor: null });
        }
        if (mode === 'error') return new HttpResponse(null, { status: 500 });
        await new Promise(() => undefined);
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }));

      mountAt(`/orgs/${SLUG}/tasks`);
      const heading = await screen.findByRole('heading', { name: 'Waiting on you' });
      const list = screen.getByTestId('tasks-responsive-list');
      expect(within(list).getByRole('heading', { name: 'Waiting on you' })).toBe(heading);
      expect(screen.getByText('Founder decision C')).toBeInTheDocument();
      if (mode === 'error') {
        // Ordinary initial error stays truthful and visible below the shell.
        expect(await screen.findByText('Could not load tasks')).toBeInTheDocument();
      } else {
        expect(screen.getByText('Loading…')).toBeInTheDocument();
      }
      // The visible escalated row is never contradicted by an empty-ordinary claim.
      expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    },
  );

  test('keeps ordinary rows visible while the attention traversal is still loading', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const running = rootTask({ task_id: 'TASK-RUN-D', brief: 'Running root D', status: 'in_progress', severity_rollup: 'in_progress' });
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('status') === 'escalated') {
        await new Promise(() => undefined);
      }
      return HttpResponse.json({ tasks: [running], next_cursor: null });
    }));

    mountAt(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Running root D');
    expect(screen.getByText('Loading waiting-on-you tasks…')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Waiting on you' })).toBeNull();
    const list = screen.getByTestId('tasks-responsive-list');
    expect(within(list).getByText('Running root D')).toBeInTheDocument();
    expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
  });
});

// THR-037 Change B Phase 2: the status-GROUP header maps must speak the Path-B
// vocabulary. `escalated` is a first-class attention group (red dot, surfaced
// early); `cancelled` is a calm terminal group (muted dot, full opacity);
// `blocked` is fully retired from this presentation surface.
describe('TasksPage — Path-B status group vocabulary (THR-037 Change B Phase 2)', () => {
  function mountStatuses(tasks: TaskRecord[]) {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/tasks`);
  }

  test('escalated group renders the amber attention dot + a proper label and sorts early', async () => {
    const running = rootTask({
      task_id: 'TASK-0600',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'A healthy running root',
    });
    const escalated = rootTask({
      task_id: 'TASK-0601',
      status: 'escalated',
      severity_rollup: 'escalated',
      brief: 'A root escalated to the founder',
    });
    mountStatuses([running, escalated]);

    // Proper display label (not a raw-lowercase fallback). THR-046 msg-11:
    // "Waiting on you" is the escalated attention group label.
    const escalatedHeading = await screen.findByRole('heading', {
      name: /Waiting on you/,
    });
    // Amber attention dot — the SAME token StatusBadge uses for escalated.
    const dot = escalatedHeading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-attention-text');

    // Sorts EARLY: the escalated attention group precedes the in_progress group
    // in document order (first-class attention, surfaced near the top).
    const activeHeading = screen.getByRole('heading', { name: /In progress/ });
    expect(
      escalatedHeading.compareDocumentPosition(activeHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Escalated is an ATTENTION state, NOT dimmed/terminal. THR-061 a-tasks:
    // the group label sits above its rows-card, so the dimming lives on the
    // heading's wrapper (its parent), not on the rows-card section.
    expect(escalatedHeading.parentElement).not.toHaveClass('opacity-60');
  });

  test('cancelled group renders the muted/terminal treatment without dimming', async () => {
    const cancelled = rootTask({
      task_id: 'TASK-0602',
      status: 'cancelled',
      severity_rollup: 'cancelled',
      brief: 'A cancelled root',
    });
    mountStatuses([cancelled]);

    const cancelledHeading = await screen.findByRole('heading', {
      name: /Cancelled/,
    });
    // Muted/terminal dot — the SAME token StatusBadge uses for cancelled
    // (mirrors superseded).
    const dot = cancelledHeading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-status-archived');

    // Cancelled retains full opacity; only superseded rows are dimmed.
    expect(cancelledHeading.parentElement).not.toHaveClass('opacity-60');
  });

  test('no `blocked` group label or dot path remains on this surface', async () => {
    // Render the full Path-B vocabulary; no surface should fall back to the
    // retired `blocked` label or its dot token.
    const tasks = [
      rootTask({ task_id: 'TASK-0610', status: 'in_progress', severity_rollup: 'in_progress' }),
      rootTask({ task_id: 'TASK-0611', status: 'escalated', severity_rollup: 'escalated' }),
      rootTask({ task_id: 'TASK-0612', status: 'cancelled', severity_rollup: 'cancelled' }),
      rootTask({ task_id: 'TASK-0613', status: 'completed', severity_rollup: 'completed' }),
    ];
    mountStatuses(tasks);

    await screen.findByRole('heading', { name: /In progress/ });
    // No retired `blocked` group heading.
    expect(screen.queryByRole('heading', { name: /Blocked/ })).toBeNull();
    // No retired blocked dot token anywhere in the rendered surface.
    expect(document.querySelector('.text-status-blocked')).toBeNull();
  });
});

describe('TasksPage — Direction-A list reshape (THR-030 TASKS-01/02/03)', () => {
  function mountTasks(tasks: TaskRecord[]) {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/tasks`);
  }

  // TASKS-03: page eyebrow (derived from loaded list data) + serif title.
  test('renders serif title and a derived eyebrow with root/waiting/failed counts', async () => {
    const running = rootTask({
      task_id: 'TASK-0400',
      status: 'in_progress',
      severity_rollup: 'in_progress',
    });
    const escalated = rootTask({
      task_id: 'TASK-0401',
      status: 'escalated',
      severity_rollup: 'escalated',
    });
    const failed = rootTask({
      task_id: 'TASK-0402',
      status: 'failed',
      severity_rollup: 'failed',
    });
    mountTasks([running, escalated, failed]);

    // Serif title replaces the bare "Tasks" heading.
    expect(
      await screen.findByRole('heading', { name: 'What the org is working on' }),
    ).toBeInTheDocument();

    // Eyebrow derives from the ordinary list: escalated roots are owned by the
    // separate Waiting-on-you presentation. Wait for the roots query to populate
    // (the static header renders before the fetch resolves).
    await waitFor(() =>
      expect(screen.getByText(/ROOT TASKS/)).toHaveTextContent('2 LOADED MATCHING ROOT TASKS'),
    );
    const eyebrow = screen.getByText(/ROOT TASKS/);
    expect(eyebrow).toHaveTextContent('SUBTASKS ROLL UP');
    expect(eyebrow).not.toHaveTextContent('WAITING ON YOU');
    expect(eyebrow).toHaveTextContent('1 FAILED');
  });

  // TASKS-01: column header row aligned above the rows. THR-041: STATUS · TASK · TITLE · AGENT · THREAD · UPDATED.
  test('renders the STATUS · TASK · TITLE · AGENT · THREAD · UPDATED column header row', async () => {
    mountTasks([
      rootTask({ task_id: 'TASK-0410', status: 'in_progress', severity_rollup: 'in_progress' }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('STATUS')).toBeInTheDocument();
    });
    expect(screen.getByText('TASK')).toBeInTheDocument();
    expect(screen.getByText('TITLE')).toBeInTheDocument();
    expect(screen.getByText('AGENT')).toBeInTheDocument();
    expect(screen.getByText('THREAD')).toBeInTheDocument();
    expect(screen.getByText('UPDATED')).toBeInTheDocument();
    // Verify the DOM order: STATUS before TASK, TASK before TITLE.
    // THR-061 a-tasks: column header is a bordered surface-page card (matching
    // the group row-cards below), replacing the old sunken bar.
    const headerDivs = document.querySelectorAll('[class*="rounded-xl"][class*="bg-surface-page"] > div');
    const labels = Array.from(headerDivs).map((d) => d.textContent);
    expect(labels).toEqual(['STATUS', 'TASK', 'TITLE', 'AGENT', 'THREAD', 'UPDATED']);
  });

  // TASKS-02: agent rendered as AgentChip (avatar idiom), thread as a chip,
  // task ID as a monospace IdBadge, row click-through preserved to the detail route.
  test('renders task ID as a monospace IdBadge between status and title', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0410',
        assigned_agent: 'dev_agent',
        dispatched_from_thread_id: 'THR-0030',
        status: 'in_progress',
        severity_rollup: 'in_progress',
        brief: 'Reshape the tasks list rows',
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('TASK-0410')).toBeInTheDocument();
    });
    // Task ID renders as a monospace IdBadge (tinted, not plain text).
    const taskId = screen.getByText('TASK-0410');
    expect(taskId).toHaveClass('font-mono');
    expect(taskId).toHaveClass('text-id-task');
    // The task ID is inside the row Link (the whole row is clickable) but the
    // IdBadge itself renders without a `to` prop, so it does NOT create a
    // nested anchor — the span's direct parent is a div.COL.taskId, not an <a>.
    expect(taskId.parentElement?.tagName).not.toBe('A');
  });

  // THR-041: long titles truncate cleanly with ellipsis so they cannot
  // overlap the Agent/Thread/Updated columns.
  // THR-049 msg-9 + task/TASK-1223: STATUS column renders compact primary
  // task status only — no block_kind derived qualifier ('waiting on subtasks'
  // / 'waiting on jobs') and no severity rollup. Both the waiting qualifier
  // and worst-child rollup render as second-line context in the TITLE column.
  // The reduced scope covers the founder-reported case where status=in_progress,
  // block_kind=delegated, severity_rollup=in_progress — the waiting qualifier
  // was still leaking into the STATUS column through StatusBadge.
  //
  // Case 1: status='in_progress' + block_kind='delegated' + severity_rollup='in_progress'
  //   → STATUS shows compact 'in_progress', TITLE shows 'waiting on subtasks'.
  test('STATUS compact for delegated in_progress task — waiting qualifier in TITLE context (THR-049)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const delegated = rootTask({
      task_id: 'TASK-0800',
      status: 'in_progress',
      block_kind: 'delegated',
      severity_rollup: 'in_progress',
      brief: 'Delegated root waiting on its children',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [delegated] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('TASK-0800')).toBeInTheDocument();
    });
    const row = document.querySelector('a[href*="/tasks/TASK-0800"]') as HTMLElement;
    // STATUS column: compact 'in_progress' only, no 'waiting on subtasks'.
    const statusCell = row.querySelector('.whitespace-nowrap') as HTMLElement;
    const statusText = statusCell?.textContent ?? '';
    expect(statusText).toContain('in_progress');
    expect(statusText).not.toContain('waiting on subtasks');
    expect(statusText).not.toContain('waiting on jobs');
    // TITLE column: 'waiting on subtasks' appears as second-line context.
    expect(within(row).getByText('waiting on subtasks')).toBeInTheDocument();
    // No rollup line when rollup matches own status.
    expect(within(row).queryByText('subtask')).not.toBeInTheDocument();
  });

  test('STATUS compact when rollup worse than own status — rollup in TITLE context', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const aggravated = rootTask({
      task_id: 'TASK-0801',
      status: 'in_progress',
      block_kind: null,
      severity_rollup: 'escalated',
      brief: 'Root in progress with an escalated child',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [aggravated] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('TASK-0801')).toBeInTheDocument();
    });
    const row = document.querySelector('a[href*="/tasks/TASK-0801"]') as HTMLElement;
    // STATUS column: compact primary 'in_progress' only.
    const statusCell = row.querySelector('.whitespace-nowrap') as HTMLElement;
    const statusText = statusCell?.textContent ?? '';
    expect(statusText).toContain('in_progress');
    expect(statusText).not.toContain('escalated');
    // TITLE column: 'subtask escalated' appears as second-line context.
    const rollup = within(row).getByText('subtask escalated');
    expect(rollup).toHaveClass('text-attention-text');
  });

  test('STATUS compact when delegated + worse rollup — both waiting and rollup in TITLE', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const both = rootTask({
      task_id: 'TASK-0802',
      status: 'in_progress',
      block_kind: 'delegated',
      severity_rollup: 'escalated',
      brief: 'Root waiting AND has escalated child',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [both] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('TASK-0802')).toBeInTheDocument();
    });
    const row = document.querySelector('a[href*="/tasks/TASK-0802"]') as HTMLElement;
    // STATUS column: compact 'in_progress' only.
    const statusCell = row.querySelector('.whitespace-nowrap') as HTMLElement;
    const statusText = statusCell?.textContent ?? '';
    expect(statusText).toContain('in_progress');
    expect(statusText).not.toContain('waiting');
    expect(statusText).not.toContain('escalated');
    // TITLE column: both 'waiting on subtasks' and 'subtask escalated' appear.
    expect(within(row).getByText('waiting on subtasks')).toBeInTheDocument();
    expect(within(row).getByText('subtask escalated')).toBeInTheDocument();
  });

  test('STATUS compact for in_progress + blocked_on_job — waiting on jobs in TITLE', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const waitingJob = rootTask({
      task_id: 'TASK-0803',
      status: 'in_progress',
      block_kind: 'blocked_on_job',
      severity_rollup: 'in_progress',
      brief: 'Root waiting on a job',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [waitingJob] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('TASK-0803')).toBeInTheDocument();
    });
    const row = document.querySelector('a[href*="/tasks/TASK-0803"]') as HTMLElement;
    const statusCell = row.querySelector('.whitespace-nowrap') as HTMLElement;
    const statusText = statusCell?.textContent ?? '';
    expect(statusText).toContain('in_progress');
    expect(statusText).not.toContain('waiting');
    expect(within(row).getByText('waiting on jobs')).toBeInTheDocument();
  });

  test('truncates long titles with ellipsis and keeps status on one line', async () => {
    const longBrief = 'A'.repeat(500) + ' should be clipped';
    mountTasks([
      rootTask({
        task_id: 'TASK-0440',
        assigned_agent: 'dev_agent',
        dispatched_from_thread_id: 'THR-0030',
        status: 'in_progress',
        severity_rollup: 'in_progress',
        brief: longBrief,
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('TASK-0440')).toBeInTheDocument();
    });
    // The title span should carry the truncate class.
    const titleSpan = screen.getByText((content, element) => {
      return element?.tagName === 'SPAN' && content.startsWith('AAAA');
    });
    expect(titleSpan).toHaveClass('truncate');
    // The status badge cell in the data row carries whitespace-nowrap so the
    // pill cannot wrap. The header also has whitespace-nowrap (via COL.status),
    // so we scope to the data row specifically.
    const statusCell = document.querySelector(
      'a[href*="/tasks/TASK-0440"] .whitespace-nowrap',
    );
    expect(statusCell).not.toBeNull();
    expect(statusCell!.textContent).toContain('in_progress');
  });

  // THR-041: status column now only shows the StatusBadge (no task ID).
  // Row click-through to the detail route is preserved.
  test('renders agent as an AgentChip avatar and thread as an inline chip', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0420',
        assigned_agent: 'dev_agent',
        dispatched_from_thread_id: 'THR-0030',
        status: 'in_progress',
        severity_rollup: 'in_progress',
        brief: 'Reshape the tasks list rows',
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
    });
    // Agent is the AgentChip idiom (role-colored dot), not plain text.
    expect(document.querySelector('.bg-agent-worker')).not.toBeNull();
    // Thread reference renders as an inline (tinted) chip.
    expect(screen.getByText('THR-0030')).toBeInTheDocument();
    // Row click-through to the detail route is preserved.
    const rowLink = screen.getByRole('link', { name: /Reshape the tasks list rows/ });
    expect(rowLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0420`);
  });

  // TASKS-02 honesty fence: missing agent/thread render a neutral fallback,
  // never a fabricated identity.
  test('renders neutral em-dash fallbacks when agent and thread are absent', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0430',
        assigned_agent: null,
        status: 'pending',
        severity_rollup: 'pending',
        brief: 'Unassigned, no thread',
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('Unassigned, no thread')).toBeInTheDocument();
    });
    // No fabricated agent chip for this row.
    expect(document.querySelector('.bg-agent-worker')).toBeNull();
    // Both the agent and thread cells fall back to an em-dash.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });
});

// THR-046 msg-11: wider layout, cream canvas, rounded column header,
// rounded bordered group-section cards, right-aligned group-by control,
// "Waiting on you" escalation label, "In progress" label.
describe('TasksPage — THR-046 msg-11 layout reshape', () => {
  function mountTasks(tasks: TaskRecord[]) {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/tasks`);
  }

  test('column header is a rounded bordered surface-page card', async () => {
    mountTasks([
      rootTask({ task_id: 'TASK-0700', status: 'in_progress', severity_rollup: 'in_progress' }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('STATUS')).toBeInTheDocument();
    });
    // THR-061 a-tasks: the column header is the first bordered surface-page card
    // in the <main> scroll area — it precedes the group row-cards in DOM order.
    const header = document.querySelector('[class*="rounded-xl"][class*="bg-surface-page"]');
    expect(header).not.toBeNull();
    expect(header).toHaveClass('rounded-xl');
    expect(header).toHaveClass('bg-surface-page');
    expect(header).toHaveClass('border');
  });

  test('each group section is a rounded bordered card', async () => {
    mountTasks([
      rootTask({ task_id: 'TASK-0710', status: 'escalated', severity_rollup: 'escalated' }),
      rootTask({ task_id: 'TASK-0711', status: 'completed', severity_rollup: 'completed' }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('TASK-0710')).toBeInTheDocument();
    });
    // Group sections inside <main> are bordered rounded cards.
    const sections = document.querySelectorAll('main section');
    expect(sections.length).toBeGreaterThanOrEqual(2);
    sections.forEach((s) => {
      expect(s).toHaveClass('rounded-xl');
      expect(s).toHaveClass('border');
    });
  });

  test('group-by control is right-aligned in the header flex row', async () => {
    mountTasks([
      rootTask({ task_id: 'TASK-0720', status: 'in_progress', severity_rollup: 'in_progress' }),
    ]);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'What the org is working on' })).toBeInTheDocument();
    });
    // The header contains a flex row with justify-between — the title (left)
    // and the group-by tabs (right) are siblings.
    const headerFlex = screen.getByTestId('tasks-page-header');
    expect(headerFlex).not.toBeNull();
    const tablist = headerFlex!.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();
  });

  test('escalated group renders as "Waiting on you" with amber attention dot', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0730',
        status: 'escalated',
        severity_rollup: 'escalated',
        brief: 'A root escalated for attention',
      }),
    ]);
    const heading = await screen.findByRole('heading', {
      name: /Waiting on you/,
    });
    // Amber attention dot.
    const dot = heading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-attention-text');
    // Not dimmed (dimming lives on the heading's wrapper — a-tasks label
    // above rows-card).
    expect(heading.parentElement).not.toHaveClass('opacity-60');
  });

  test('in_progress group renders as "In progress" with blue status dot', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0740',
        status: 'in_progress',
        severity_rollup: 'in_progress',
        brief: 'Active root task',
      }),
    ]);
    const heading = await screen.findByRole('heading', {
      name: /In progress/,
    });
    const dot = heading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-info');
    // Count badge present.
    expect(within(heading).getByText('1')).toBeInTheDocument();
  });
});

describe('TaskDetailPage — jobs cross-link', () => {
  function stubHandlers(jobs: JobRecord[]) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json(TASK),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: null,
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs }),
      ),
    );
  }

  test('shows jobs section when task has jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers([JOB]);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Jobs from this task/i)).toBeInTheDocument(),
    );
    const link = screen.getByRole('link', { name: 'JOB-0001' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0001`);
    expect(screen.getByText(/Generate sitemap/)).toBeInTheDocument();
    expect(screen.getByText(/completed/)).toBeInTheDocument();
  });

  test('hides jobs section when task has no jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers([]);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Jobs from this task/i)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPage — workflow chain timeline', () => {
  const ACTIVE_CHAIN: ActiveChainResponse = {
    step_index: 1,
    first_leg_expect_verdict: null,
    legs: [
      { agent: 'senior_dev', prompt: 'review the PR', expect_verdict: 'APPROVE' },
      { agent: 'qa_engineer', prompt: 'run QA suite', expect_verdict: 'PASS' },
    ],
    step_audit_id: 14,
  };

  const TASK_DETAIL_ENVELOPE = {
    task: TASK,
    results: [],
    audit_log: [],
    revisit_chain: [],
    direct_revisits: [],
    predecessor_prior_status: null,
    blocked_on_jobs: null,
  };

  function stubHandlers(
    active_chain: ActiveChainResponse | null,
    taskOverrides?: Partial<TaskRecord> & Record<string, unknown>,
    blocked_on_jobs?: unknown,
  ) {
    const detailTask = { ...TASK, ...taskOverrides } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}`, () =>
        HttpResponse.json({
          ...TASK_DETAIL_ENVELOPE,
          task: detailTask,
          active_chain,
          blocked_on_jobs: blocked_on_jobs ?? null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: detailTask.task_id,
          assigned_agent: null,
          brief: detailTask.brief,
          status: detailTask.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('renders the chain timeline when active_chain is set', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(ACTIVE_CHAIN);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText('senior_dev')).toBeInTheDocument();
    expect(screen.getByText('qa_engineer')).toBeInTheDocument();
    expect(screen.getByText(/APPROVE/)).toBeInTheDocument();
  });

  test('does not render the chain timeline when active_chain is null', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(null);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Workflow chain/i)).not.toBeInTheDocument();
  });

  test('renders blocked chain node when task is escalated', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Path B: a genuine escalation is the top-level `escalated` status.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      { status: 'escalated', block_kind: null },
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    // The blocked node should show "Blocked on: escalation"
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    expect(screen.getByText(/escalation/)).toBeInTheDocument();
  });

  test('renders blocked chain node with job IDs from blocked_on_jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Path B: a task waiting on a job is in_progress + blocked_on_job.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 1 },
      { status: 'in_progress', block_kind: 'blocked_on_job' },
      [{ job_id: 'JOB-0042', status: 'pending' }],
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    expect(screen.getByText(/JOB-0042/)).toBeInTheDocument();
  });

  test('falls through to generic job copy for pending_review (gate removed)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // pending_review is no longer accepted by parseActiveFanout (THR-012 msg 129/131).
    // The fan-out approval copy should NOT render; generic job IDs fall through.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      {
        status: 'in_progress',
        block_kind: 'blocked_on_job',
        active_fanout: JSON.stringify({ status: 'pending_review', width: 5 }),
      },
      [{ job_id: 'JOB-0099', status: 'pending' }],
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    // Must NOT show fan-out approval copy — pending_review is rejected
    expect(
      screen.queryByText(/awaiting approval to spawn/),
    ).not.toBeInTheDocument();
    // Generic job ID copy shows instead
    expect(screen.getByText(/JOB-0099/)).toBeInTheDocument();
  });

  test('renders generic job wait for ordinary blocked_on_job without fan-out', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Ordinary blocked_on_job: no active_fanout at all. Must still render
    // generic job-waiting copy, not fan-out approval language.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      {
        status: 'in_progress',
        block_kind: 'blocked_on_job',
        active_fanout: undefined,
      },
      [{ job_id: 'JOB-0077', status: 'pending' }],
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    // Must show generic job ID
    expect(screen.getByText(/JOB-0077/)).toBeInTheDocument();
    // Must NOT show fan-out approval copy
    expect(
      screen.queryByText(/awaiting approval/),
    ).not.toBeInTheDocument();
  });

  test('renders waiting-on-subtasks copy for active spawned fan-out', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Active spawned fan-out: delegated block_kind with active_fanout.status='spawned'.
    // Should render width-aware delegation copy, not fan-out approval nor job IDs.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      {
        status: 'in_progress',
        block_kind: 'delegated',
        active_fanout: JSON.stringify({ status: 'spawned', width: 3 }),
      },
      null,
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    // Must show width-aware delegation copy
    expect(
      screen.getByText(/waiting on 3 subtasks/),
    ).toBeInTheDocument();
    // Must NOT show fan-out approval copy
    expect(
      screen.queryByText(/awaiting approval/),
    ).not.toBeInTheDocument();
    // Must NOT show generic delegation copy
    expect(screen.queryByText(/^delegation$/)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPage — fan-out status band (TASK-1717)', () => {
  interface BandStubOpts {
    taskOverrides?: Partial<TaskRecord> & Record<string, unknown>;
    audit_log?: unknown[];
    recallChildren?: unknown[];
    jobs?: JobRecord[];
  }

  function reviewJob(overrides?: Partial<JobRecord>): JobRecord {
    return {
      ...JOB,
      id: 'JOB-APPROVAL',
      title: 'Approve fan-out (spawn 2 subtasks)',
      status: 'pending',
      review_required: true,
      exit_code: null,
      ...overrides,
    };
  }

  function stubBand(opts: BandStubOpts) {
    const detailTask = { ...TASK, ...opts.taskOverrides } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}`, () =>
        HttpResponse.json({
          task: detailTask,
          results: [],
          audit_log: opts.audit_log ?? [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: detailTask.task_id,
          assigned_agent: null,
          brief: detailTask.brief,
          status: detailTask.status,
          output_summary: null,
          children: opts.recallChildren ?? [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: opts.jobs ?? [] }),
      ),
    );
  }

  function mountDetail() {
    sessionStorage.setItem('happyranch.token', 'tok');
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
  }

  test('pending_review: fan-out band does NOT render (gate removed per THR-012)', async () => {
    stubBand({
      taskOverrides: {
        status: 'in_progress',
        block_kind: 'blocked_on_job',
        active_fanout: JSON.stringify({
          status: 'pending_review',
          width: 2,
          children_details: [
            { agent: 'content_writer', prompt: 'Draft the intro section' },
            { agent: 'seo_specialist', prompt: 'Audit the target keywords' },
          ],
        }),
      },
      jobs: [reviewJob()],
      recallChildren: [],
    });
    mountDetail();

    // pending_review is no longer accepted by parseActiveFanout —
    // the fan-out band should NOT render.
    // The status band region should not exist at all.
    await screen.findByText(/Execution subtasks/i);
    expect(
      screen.queryByRole('region', { name: 'Fan-out status' }),
    ).not.toBeInTheDocument();
  });

  test('running: progress counts come from recall children and child task links are preserved', async () => {
    stubBand({
      taskOverrides: {
        status: 'in_progress',
        block_kind: 'delegated',
        active_fanout: JSON.stringify({
          status: 'spawned',
          width: 3,
          children_ids: ['TASK-C1', 'TASK-C2', 'TASK-C3'],
        }),
      },
      recallChildren: [
        {
          task_id: 'TASK-C1',
          assigned_agent: 'content_writer',
          brief: 'Child one brief',
          status: 'completed',
          output_summary: 'done one',
          children: [],
        },
        {
          task_id: 'TASK-C2',
          assigned_agent: 'content_writer',
          brief: 'Child two brief',
          status: 'in_progress',
          output_summary: null,
          children: [],
        },
        {
          task_id: 'TASK-C3',
          assigned_agent: 'content_writer',
          brief: 'Child three brief',
          status: 'pending',
          output_summary: null,
          children: [],
        },
      ],
    });
    mountDetail();

    const band = await screen.findByRole('region', { name: 'Fan-out status' });
    // Terminal count (1 completed) of width 3, derived from recall statuses.
    expect(within(band).getByText(/Running fan-out — 1 of 3 done/)).toBeInTheDocument();
    expect(
      within(band).getByText(/1 of 3 complete · 1 running · 1 queued/),
    ).toBeInTheDocument();
    // Backed metadata: width + constant join mode.
    expect(within(band).getByText('all-terminal')).toBeInTheDocument();

    // Child task links preserved in the execution-subtasks list.
    const subtasks = screen
      .getByText('Execution subtasks')
      .closest('section') as HTMLElement;
    const c1 = within(subtasks).getByRole('link', { name: 'TASK-C1' });
    expect(c1).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-C1`);
    expect(within(subtasks).getByRole('link', { name: 'TASK-C3' })).toBeInTheDocument();
  });

  test('joined: renders from the fanout_join audit row even when active_fanout is cleared', async () => {
    stubBand({
      taskOverrides: {
        status: 'completed',
        block_kind: null,
        active_fanout: undefined, // cleared after join
      },
      audit_log: [
        { action: 'fanout_spawned', payload: { width: 2 } },
        {
          action: 'fanout_join',
          payload: { width: 2, children_ids: ['TASK-J1', 'TASK-J2'] },
        },
      ],
      recallChildren: [
        {
          task_id: 'TASK-J1',
          assigned_agent: 'content_writer',
          brief: 'Joined child one',
          status: 'completed',
          output_summary: 'ok',
          children: [],
        },
        {
          task_id: 'TASK-J2',
          assigned_agent: 'content_writer',
          brief: 'Joined child two',
          status: 'failed',
          output_summary: 'boom',
          children: [],
        },
      ],
    });
    mountDetail();

    const band = await screen.findByRole('region', { name: 'Fan-out status' });
    expect(within(band).getByText(/Fan-out joined — 1 of 2 succeeded/)).toBeInTheDocument();
    expect(within(band).getByText(/1 subtask did not succeed/)).toBeInTheDocument();
    expect(within(band).getByText('all-terminal')).toBeInTheDocument();
    // Inspectable child rows preserved in the execution-subtasks list.
    const subtasks = screen
      .getByText('Execution subtasks')
      .closest('section') as HTMLElement;
    expect(within(subtasks).getByRole('link', { name: 'TASK-J1' })).toBeInTheDocument();
  });

  test('regular non-fan-out task renders NO fan-out band', async () => {
    stubBand({
      taskOverrides: { status: 'completed', block_kind: null },
      audit_log: [{ action: 'task_started', payload: {} }],
      recallChildren: [],
    });
    mountDetail();

    await waitFor(() =>
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole('region', { name: 'Fan-out status' }),
    ).not.toBeInTheDocument();
  });

  test('ordinary blocked_on_job (no fan-out) renders NO fan-out band', async () => {
    stubBand({
      taskOverrides: {
        status: 'in_progress',
        block_kind: 'blocked_on_job',
        active_fanout: undefined,
      },
      audit_log: [],
      jobs: [{ ...JOB, id: 'JOB-XYZ', status: 'pending' }],
      recallChildren: [],
    });
    mountDetail();

    await waitFor(() =>
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole('region', { name: 'Fan-out status' }),
    ).not.toBeInTheDocument();
    // No fan-out copy anywhere on the page.
    expect(screen.queryByText(/Awaiting approval to spawn/)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPage — execution subtasks', () => {
  function stubHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json(TASK),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: 'content_writer',
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [
            {
              task_id: 'TASK-0092',
              assigned_agent: 'content_writer',
              brief: 'Section 4: currency policy',
              status: 'completed',
              output_summary: 'Wrote section 4.',
              children: [],
            },
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('shows execution subtasks from recall tree', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() => {
      expect(screen.getByText(/Execution subtasks/i)).toBeInTheDocument();
    });
    expect(screen.getAllByText('TASK-0092').length).toBeGreaterThan(0);
    expect(screen.getAllByText('content_writer').length).toBeGreaterThan(0);
  });
});

describe('TaskDetailPage — full-page surface', () => {
  function stubHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      // Detail endpoint returns the envelope; useTask selects response.task.
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json({
          task: TASK,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: null,
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    );
  }

  test('renders the task body with a "‹ All tasks" back link to the roots list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Wait for the data-driven Brief section (gated on task.data.brief) — the
    // task id heading renders synchronously from the route param, so awaiting
    // it would not wait for the detail fetch.
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();

    // Full-page body renders: task id heading + brief content, no drawer overlay.
    expect(
      screen.getByRole('heading', { name: new RegExp(TASK.task_id) }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Draft Hong Kong visa guide/).length,
    ).toBeGreaterThan(0);

    // Back-nav returns to the roots list.
    const backLink = screen.getByRole('link', { name: /‹ All tasks/ });
    expect(backLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks`);
  });
});

describe('TaskDetailPage — property grid (TASKDET-03)', () => {
  // Detail task carrying every property-grid field that has REAL backing in the
  // TaskRecord payload: status, assigned_agent, dispatched_from_thread_id,
  // created_at. Executor / Churn / Priority have no backing field and are
  // honestly omitted (see TaskDetailPage PropertyRail doc-comment).
  const DETAIL_TASK = {
    ...TASK,
    assigned_agent: 'content_writer',
    dispatched_from_thread_id: 'THR-0030',
    created_at: '2026-05-18T10:00:00Z',
  } as TaskRecord;

  function stubHandlers(jobs: JobRecord[], task: TaskRecord = DETAIL_TASK) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [task] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${task.task_id}`, () =>
        HttpResponse.json({
          task,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${task.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: task.task_id,
          assigned_agent: task.assigned_agent,
          brief: task.brief,
          status: task.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs })),
    );
  }

  async function mountAndGetRail() {
    sessionStorage.setItem('happyranch.token', 'tok');
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${DETAIL_TASK.task_id}`,
    });
    return (await screen.findByRole('complementary', {
      name: /task status and properties/i,
    })) as HTMLElement;
  }

  test('renders a labeled property grid of the backed fields', async () => {
    const rail = await (async () => {
      stubHandlers([JOB]);
      return mountAndGetRail();
    })();

    // Backed fields — each label/value renders inside the rail.
    expect(within(rail).getByText('Status')).toBeInTheDocument();
    expect(within(rail).getByText('Assignee')).toBeInTheDocument();
    expect(within(rail).getByText('content_writer')).toBeInTheDocument();
    expect(within(rail).getByText('Thread')).toBeInTheDocument();
    const threadLink = within(rail).getByRole('link', { name: 'THR-0030' });
    expect(threadLink).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/threads/THR-0030`,
    );
    expect(within(rail).getByText('Job')).toBeInTheDocument();
    const jobLink = within(rail).getByRole('link', { name: 'JOB-0001' });
    expect(jobLink).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0001`);
    expect(within(rail).getByText('Created')).toBeInTheDocument();
  });

  test('wraps multiple job links within the property rail in returned order (THR-137)', async () => {
    const jobs = [
      { ...JOB, id: 'JOB-0338' },
      { ...JOB, id: 'JOB-0337' },
      { ...JOB, id: 'JOB-0336' },
      { ...JOB, id: 'JOB-0335' },
    ];
    stubHandlers(jobs);
    const rail = await mountAndGetRail();

    const jobRow = within(rail).getByText('Job').closest('div') as HTMLElement;
    const links = within(jobRow).getAllByRole('link');
    expect(links.map((link) => link.textContent)).toEqual(jobs.map((job) => job.id));
    expect(links.map((link) => link.getAttribute('href'))).toEqual(
      jobs.map((job) => `/orgs/${SLUG}/jobs/${job.id}`),
    );

    // jsdom cannot assert geometry; lock the Job-only shrink-and-wrap contract instead.
    const value = jobRow.querySelector('dd') as HTMLElement;
    expect(value).toHaveClass('min-w-0', 'flex-1');
    expect(value).not.toHaveClass('min-w-max');
    expect(value.firstElementChild).toHaveClass('flex', 'flex-wrap');
  });

  test('honestly omits fields with no backing payload (Executor / Churn / Priority)', async () => {
    stubHandlers([JOB]);
    const rail = await mountAndGetRail();
    expect(within(rail).queryByText('Executor')).toBeNull();
    expect(within(rail).queryByText('Churn')).toBeNull();
    expect(within(rail).queryByText('Priority')).toBeNull();
  });

  test('keeps a long assignee identifier fully available with wrap-safe styling', async () => {
    const longAssignee = `frontend_${'engineer'.repeat(30)}`;
    stubHandlers([], { ...DETAIL_TASK, assigned_agent: longAssignee });
    const rail = await mountAndGetRail();
    const identity = within(rail).getByText(longAssignee);
    expect(identity).toHaveClass('min-w-0', 'break-all');
    expect(identity).not.toHaveClass('truncate');
  });

  // THR-137: a long in_progress + delegated status badge must stay readable in
  // the narrow property rail. jsdom cannot prove geometry, so the test asserts
  // the responsive layout contract (wrap-capable row, min-content value cell,
  // no-wrap badge wrapper) plus the visible qualifier text.
  test('keeps a long in_progress + delegated status badge readable in the narrow rail (THR-137)', async () => {
    const WAITING_TASK = {
      ...DETAIL_TASK,
      status: 'in_progress',
      block_kind: 'delegated',
    } as TaskRecord;
    stubHandlers([], WAITING_TASK);
    const rail = await mountAndGetRail();

    // The waiting qualifier is rendered and readable inside the rail.
    const qualifier = within(rail).getByText('· waiting on subtasks');
    expect(qualifier).toBeInTheDocument();

    // Responsive layout contract: the row can wrap so the value is not squeezed.
    const statusRow = within(rail).getByText('Status').closest('div') as HTMLElement;
    expect(statusRow).toHaveClass('flex-wrap');
    const value = qualifier.closest('dd') as HTMLElement;
    expect(value).toHaveClass('min-w-0');
    // The badge remains complete and can wrap as a unit at narrow widths.
    expect(value.querySelector('span.inline-flex')).not.toBeNull();
  });

  test('omits the Thread and Job rows when those fields are absent', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json({
          task: TASK,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: null,
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    const rail = (await screen.findByRole('complementary', {
      name: /task status and properties/i,
    })) as HTMLElement;
    // No thread / no jobs → those rows are absent (not fabricated).
    expect(within(rail).queryByText('Thread')).toBeNull();
    expect(within(rail).queryByText('Job')).toBeNull();
    // Always-present backed fields still render.
    expect(within(rail).getByText('Status')).toBeInTheDocument();
    expect(within(rail).getByText('Created')).toBeInTheDocument();
  });
});

describe('TaskDetailPage — escalation reason', () => {
  const ESCALATION_NOTE = 'Agent exhausted failure-round bound after 5 attempts';

  function stubDetailHandlers(
    overrides: Partial<TaskRecord> & Record<string, unknown>,
  ) {
    const detailTask = { ...TASK, ...overrides } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}`, () =>
        HttpResponse.json({
          task: detailTask,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: detailTask.task_id,
          assigned_agent: null,
          brief: detailTask.brief,
          status: detailTask.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('displays escalation reason for a Path B escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'escalated',
      block_kind: null,
      note: ESCALATION_NOTE,
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    // Wait for the data-driven Brief section to confirm detail fetch completed.
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // Escalation reason banner is visible.
    expect(screen.getByText(/Escalation reason:/)).toBeInTheDocument();
    expect(screen.getByText(ESCALATION_NOTE)).toBeInTheDocument();
    // The escalated action set (Continue) is present because the task is escalated.
    expect(screen.getByRole('button', { name: /^Continue$/ })).toBeInTheDocument();
  });

  test('displays escalation reason for a legacy blocked+escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'blocked',
      block_kind: 'escalated',
      note: 'Legacy escalation: budget override required',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Escalation reason:/)).toBeInTheDocument();
    expect(
      screen.getByText('Legacy escalation: budget override required'),
    ).toBeInTheDocument();
    // The escalated action set (Continue) is present for the legacy form too.
    expect(screen.getByRole('button', { name: /^Continue$/ })).toBeInTheDocument();
  });

  test('does not display escalation reason for a non-escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // A completed task with a note — note belongs to a prior failure, not escalation.
    stubDetailHandlers({
      status: 'completed',
      block_kind: null,
      note: 'Some note from a prior escalation',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Escalation reason:/)).not.toBeInTheDocument();
    // No escalation-only Continue action for non-escalated tasks.
    expect(
      screen.queryByRole('button', { name: /^Continue$/ }),
    ).not.toBeInTheDocument();
  });

  test('does not display escalation reason for an escalated task with empty note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'escalated',
      block_kind: null,
      note: '',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // Empty note → no escalation reason banner.
    expect(screen.queryByText(/Escalation reason:/)).not.toBeInTheDocument();
    // Continue action is still present (task is escalated, just no note).
    expect(screen.getByRole('button', { name: /^Continue$/ })).toBeInTheDocument();
  });
});

describe('TaskDetailPage — superseded by link', () => {
  const SUPERSEDED_TASK = {
    ...TASK,
    status: 'superseded',
    block_kind: null,
    note: 'Resolved: superseded by continuation TASK-SUCC',
  } as TaskRecord;

  function stubDetail(overrides: Record<string, unknown>) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${SUPERSEDED_TASK.task_id}`, () =>
        HttpResponse.json({
          task: SUPERSEDED_TASK,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
          ...overrides,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${SUPERSEDED_TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: SUPERSEDED_TASK.task_id,
          assigned_agent: null,
          brief: SUPERSEDED_TASK.brief,
          status: SUPERSEDED_TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('shows superseded-by link when task has superseded_by_task_id', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail({ superseded_by_task_id: 'TASK-SUCC' });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${SUPERSEDED_TASK.task_id}`,
    });
    // Wait for data-driven content (Brief section).
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // The superseded-by link appears in the lineage metadata.
    const link = screen.getByRole('link', { name: 'TASK-SUCC' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/tasks/TASK-SUCC`,
    );
    // The label text is present.
    expect(screen.getByText(/superseded by/)).toBeInTheDocument();
  });

  test('does not show superseded-by link when superseded_by_task_id is null', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail({ superseded_by_task_id: null });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${SUPERSEDED_TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // No superseded-by link or label.
    expect(screen.queryByText(/superseded by/)).not.toBeInTheDocument();
    // The task id heading still renders.
    expect(
      screen.getByRole('heading', { name: new RegExp(SUPERSEDED_TASK.task_id) }),
    ).toBeInTheDocument();
  });

  test('does not show superseded-by link for a non-superseded task (omitted key)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Override the task to in_progress — no supersession.
    const normalTask = { ...TASK, status: 'in_progress' } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${normalTask.task_id}`, () =>
        HttpResponse.json({
          task: normalTask,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
          superseded_by_task_id: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${normalTask.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: normalTask.task_id,
          assigned_agent: null,
          brief: normalTask.brief,
          status: normalTask.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${normalTask.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/superseded by/)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPage — Activity route isolation (TASK-4827)', () => {
  const FIRST_TASK = rootTask({
    task_id: 'TASK-4809',
    brief: 'First task activity fixture',
    status: 'in_progress',
    severity_rollup: 'in_progress',
  });
  const SECOND_TASK = rootTask({
    task_id: 'TASK-4819',
    brief: 'Second task activity fixture',
    status: 'in_progress',
    severity_rollup: 'in_progress',
  });

  function event(action: string, payload: Record<string, string>): TaskEvent {
    return {
      timestamp: '2026-08-08T00:00:00Z',
      type: 'audit',
      action,
      payload,
    };
  }

  test('drops prior-task events on route navigation and ignores a late old callback', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const tasksById = new Map([
      [FIRST_TASK.task_id, FIRST_TASK],
      [SECOND_TASK.task_id, SECOND_TASK],
    ]);
    const tails = new Map<string, SSEOptions<unknown>>();
    vi.spyOn(api, 'subscribeSSE').mockImplementation((path, options) => {
      const taskId = path.split('/').at(-2);
      if (taskId) tails.set(taskId, options);
      return new Promise<void>(() => {});
    });
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [FIRST_TASK, SECOND_TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/:taskId`, ({ params }) => {
        const task = tasksById.get(params.taskId as string);
        return HttpResponse.json({
          task,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/tasks/:taskId/recall`, ({ params }) => {
        const task = tasksById.get(params.taskId as string) as TaskRecord;
        return HttpResponse.json({
          task_id: task.task_id,
          assigned_agent: null,
          brief: task.brief,
          status: task.status,
          output_summary: null,
          children: [],
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/orgs/${SLUG}/tasks/${FIRST_TASK.task_id}`]}>
        <AppProvider client={makeQueryClient()}>
          <Link to={`/orgs/${SLUG}/tasks/${SECOND_TASK.task_id}`}>Open second task</Link>
          <I18nTestBoundary>
            <AppRoutes />
          </I18nTestBoundary>
        </AppProvider>
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Activity' });
    await waitFor(() => expect(tails.get(FIRST_TASK.task_id)).toBeDefined());
    const firstTail = tails.get(FIRST_TASK.task_id);
    act(() => firstTail?.onMessage(event('task_4809_activity', { source: 'TASK-4809' })));
    expect(screen.getByText('task_4809_activity')).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Open second task' }));
    await screen.findByRole('heading', { name: new RegExp(SECOND_TASK.task_id) });
    await waitFor(() => expect(tails.get(SECOND_TASK.task_id)).toBeDefined());
    expect(screen.queryByText('task_4809_activity')).not.toBeInTheDocument();
    expect(screen.getByText(`Loading events for ${SECOND_TASK.task_id}…`)).toBeInTheDocument();

    act(() => firstTail?.onMessage(event('task_4809_late_activity', { source: 'late TASK-4809' })));
    expect(screen.queryByText('task_4809_late_activity')).not.toBeInTheDocument();

    const secondTail = tails.get(SECOND_TASK.task_id);
    act(() => {
      secondTail?.onOpen?.();
      secondTail?.onMessage(event('task_4819_activity', { source: 'TASK-4819' }));
    });
    expect(screen.getByText('task_4819_activity')).toBeInTheDocument();
    expect(screen.queryByText('task_4809_activity')).not.toBeInTheDocument();
  });
});

// ── THR-266 / TASK-8671: stale subtask-failed presentation ──────────────
//
// The root row shows "subtask <status>" iff severity_rollup differs from the
// root's own status. Once the derive curates stale FAILED contributions, a
// retired lineage no longer renders "subtask failed"; the root's own waiting
// qualifier is preserved. Cases C1/C2/C3/C9a/C9b/C10 of the accepted design
// (`engineering_manager/output/TASK-8671/design-correction/case-design.md`,
// SHA256 1e0ef4de…b17263).

describe('THR-266 current-status subtask rollup presentation', () => {
  const mutable: { tasks: TaskRecord[] } = { tasks: [] };

  function rootsHandler(tasks: TaskRecord[]) {
    return http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('status') === 'escalated') {
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }
      return HttpResponse.json({ tasks, next_cursor: null });
    });
  }

  function mountWithClient(route: string) {
    const qc = makeQueryClient();
    qc.setQueryDefaults(['orgs'], { staleTime: Infinity });
    qc.setQueryData(['orgs'], { orgs: [{ slug: SLUG, root: '/x' }] });
    const utils = render(
      <MemoryRouter initialEntries={[route]}>
        <AppProvider client={qc}><I18nTestBoundary><AppRoutes /></I18nTestBoundary></AppProvider>
      </MemoryRouter>,
    );
    return { qc, ...utils };
  }

  async function refetchRoots(qc: ReturnType<typeof makeQueryClient>) {
    await act(async () => {
      await qc.refetchQueries({ queryKey: ['tasks-roots-infinite'], exact: false });
    });
  }

  // The page eyebrow is the uppercase "N LOADED … · N FAILED" line derived
  // from the same severity rollup the rows display.
  function eyebrowText(): string {
    return document.querySelector('.tasks-eyebrow')?.textContent ?? '';
  }
  function eyebrow(failed: number): string {
    return `1 LOADED MATCHING ROOT TASKS · SUBTASKS ROLL UP · ${failed} FAILED`;
  }

  test('C1/C2 rendered root shows no stale subtitle and keeps its own qualifier', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(rootsHandler([rootTask({
      task_id: 'TASK-0700',
      status: 'in_progress',
      block_kind: 'delegated',
      severity_rollup: 'in_progress',
      brief: 'Active root with a linked recovery',
    })]));
    mountAt(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Active root with a linked recovery');
    expect(screen.queryByText('subtask failed')).not.toBeInTheDocument();
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    // The qualifier comes from the root's OWN status/block_kind.
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();
  });

  test('C3 refetch drops then restores the stale subtitle from live payloads', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const step = (rollup: string) => [rootTask({
      task_id: 'TASK-0710',
      status: 'in_progress',
      block_kind: 'delegated',
      severity_rollup: rollup,
      brief: 'Retry transition root',
    })];
    // Initial stale payload may arrive failed; the accepted same-instance
    // transition is active retry -> retry failed -> F4 completed recovery.
    mutable.tasks = step('failed');
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('status') === 'escalated') {
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }
      return HttpResponse.json({ tasks: mutable.tasks, next_cursor: null });
    }));
    const { qc } = mountWithClient(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Retry transition root');
    expect(screen.getByText('subtask failed')).toBeInTheDocument();
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // Active retry: the successor is current, so the stale subtitle is gone.
    mutable.tasks = step('in_progress');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask failed')).not.toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // The retry failed again: the truthful subtitle returns.
    mutable.tasks = step('failed');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.getByText('subtask failed')).toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // F4 completed recovery: the last recovery must execute and clear it.
    mutable.tasks = step('in_progress');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask failed')).not.toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();
  });

  test('C9a in_progress/delegated root refetch keeps qualifier, toggles subtitle', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const step = (rollup: string) => [rootTask({
      task_id: 'TASK-0720',
      status: 'in_progress',
      block_kind: 'delegated',
      severity_rollup: rollup,
      brief: 'Fixed in-progress root',
    })];
    mutable.tasks = step('failed');
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('status') === 'escalated') {
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }
      return HttpResponse.json({ tasks: mutable.tasks, next_cursor: null });
    }));
    const { qc } = mountWithClient(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Fixed in-progress root');
    expect(screen.getByText('subtask failed')).toBeInTheDocument();
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // Step 2: active retry -> stale subtitle absent, root badge/qualifier fixed.
    mutable.tasks = step('in_progress');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask failed')).not.toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // Step 3: completed recovery -> still in_progress rollup, subtitle absent.
    mutable.tasks = step('in_progress');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask failed')).not.toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();

    // Step 4: a newly failed delegated attempt (recurrence) restores it.
    mutable.tasks = step('failed');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.getByText('subtask failed')).toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();
  });

  test('C9b completed root refetch shows no waiting qualifier at any step', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const step = (rollup: string) => [rootTask({
      task_id: 'TASK-0730',
      status: 'completed',
      block_kind: null,
      severity_rollup: rollup,
      brief: 'Fixed completed root',
    })];
    mutable.tasks = step('failed');
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('status') === 'escalated') {
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }
      return HttpResponse.json({ tasks: mutable.tasks, next_cursor: null });
    }));
    const { qc } = mountWithClient(`/orgs/${SLUG}/tasks`);
    await screen.findByText('Fixed completed root');
    expect(screen.getByText('subtask failed')).toBeInTheDocument();
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.queryByText('waiting on subtasks')).not.toBeInTheDocument();

    // Step 2: active retry -> literal subtitle, root badge/qualifier fixed.
    mutable.tasks = step('in_progress');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask failed')).not.toBeInTheDocument());
    expect(screen.getByText('subtask in progress')).toBeInTheDocument();
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.queryByText('waiting on subtasks')).not.toBeInTheDocument();

    // Step 3: completed recovery -> root/completed tie, no subtitle.
    mutable.tasks = step('completed');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.queryByText('subtask in progress')).not.toBeInTheDocument());
    expect(screen.queryByText('subtask failed')).not.toBeInTheDocument();
    expect(eyebrowText()).toBe(eyebrow(0));
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.queryByText('waiting on subtasks')).not.toBeInTheDocument();

    // Step 4: newly failed attempt -> subtitle returns, still no qualifier.
    mutable.tasks = step('failed');
    await refetchRoots(qc);
    await waitFor(() => expect(screen.getByText('subtask failed')).toBeInTheDocument());
    expect(eyebrowText()).toBe(eyebrow(1));
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.queryByText('waiting on subtasks')).not.toBeInTheDocument();
  });

  test('C10 TaskCard and TaskListRow keep their documented separate consumer behavior', async () => {
    const routes = { detail: (id: string) => `/orgs/x/tasks/${id}` };

    // (a) legacy/stale payload: the card badge shows the rollup it is given.
    // The FAILED rollup is not in_progress, so the card renders no qualifier;
    // the accepted revisit/supersede lineage links still render.
    const stale = rootTask({
      status: 'in_progress', block_kind: 'delegated',
      severity_rollup: 'failed', brief: 'Card root',
      revisit_of_task_id: 'TASK-0088',
      direct_revisits: ['TASK-0092'],
    });
    const a = renderWithProviders(
      <TaskCard task={stale} to="/orgs/x/tasks/TASK-0091" taskRoutes={routes} />,
    );
    expect(a.getByText('failed')).toBeInTheDocument();
    expect(a.queryByText('· waiting on subtasks')).not.toBeInTheDocument();
    expect(a.getByText('supersedes')).toBeInTheDocument();
    expect(a.getByText('TASK-0088')).toBeInTheDocument();
    expect(a.getByText('superseded by')).toBeInTheDocument();
    expect(a.getByText('TASK-0092')).toBeInTheDocument();
    a.unmount();

    // (b) corrected payload: badge in_progress + waiting qualifier as separate
    // text nodes inside the same pill.
    const corrected = rootTask({
      status: 'in_progress', block_kind: 'delegated',
      severity_rollup: 'in_progress', brief: 'Card root',
    });
    const b = renderWithProviders(<TaskCard task={corrected} to="/orgs/x/tasks/TASK-0091" />);
    expect(b.getByText('in_progress')).toBeInTheDocument();
    expect(b.getByText('· waiting on subtasks')).toBeInTheDocument();
    b.unmount();

    // D2 variant: root-own completed, rollup in_progress, delegated.
    const variant = rootTask({
      status: 'completed', block_kind: 'delegated',
      severity_rollup: 'in_progress', brief: 'Variant root',
      revisit_of_task_id: 'TASK-0088',
      direct_revisits: ['TASK-0092'],
    });
    const card = renderWithProviders(
      <TaskCard task={variant} to="/orgs/x/tasks/TASK-0091" taskRoutes={routes} />,
    );
    // The card consumes the ROLLUP for its badge, keeping its own qualifier.
    expect(card.getByText('in_progress')).toBeInTheDocument();
    expect(card.getByText('· waiting on subtasks')).toBeInTheDocument();
    expect(card.getByText('supersedes')).toBeInTheDocument();
    expect(card.getByText('TASK-0088')).toBeInTheDocument();
    card.unmount();

    const row = renderWithProviders(
      <TaskListRow
        task={variant}
        to="/orgs/x/tasks/TASK-0091"
        taskRoutes={routes}
      />,
    );
    // The list row consumes the ROOT-OWN status; the rollup is the subtitle.
    expect(row.getByText('completed')).toBeInTheDocument();
    expect(row.getByText('subtask in progress')).toBeInTheDocument();
    expect(row.queryByText('waiting on subtasks')).not.toBeInTheDocument();
    expect(row.queryByText('· waiting on subtasks')).not.toBeInTheDocument();
    expect(row.getByText('supersedes')).toBeInTheDocument();
    expect(row.getByText('superseded by')).toBeInTheDocument();
    row.unmount();

    // Same completed-root / in_progress-rollup fixed, block_kind null: the
    // card qualifier is absent while the list subtitle stays unchanged.
    const noBlock = rootTask({
      status: 'completed', block_kind: null,
      severity_rollup: 'in_progress', brief: 'No block root',
    });
    const c = renderWithProviders(<TaskCard task={noBlock} to="/orgs/x/tasks/TASK-0091" />);
    expect(c.getByText('in_progress')).toBeInTheDocument();
    expect(c.queryByText('· waiting on subtasks')).not.toBeInTheDocument();
    c.unmount();

    const noBlockRow = renderWithProviders(
      <TaskListRow task={noBlock} to="/orgs/x/tasks/TASK-0091" taskRoutes={routes} />,
    );
    expect(noBlockRow.getByText('completed')).toBeInTheDocument();
    expect(noBlockRow.getByText('subtask in progress')).toBeInTheDocument();
    expect(noBlockRow.queryByText('waiting on subtasks')).not.toBeInTheDocument();
    noBlockRow.unmount();
  });
});

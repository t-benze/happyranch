import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Link, MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { TaskListRow } from './TaskListRow';
import { AppRoutes } from '@/routes';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { mockDashboardApi } from '@/design-system/providers/_mock-dashboard';
import { mockTasksApi } from '@/design-system/providers/_mock-tasks';
import { __resetTokenCacheForTests } from '@/lib/auth';
import type { TaskRecord } from '@/lib/api/types';
import { server } from '@/test/server';

const summary = mockDashboardApi.useDashboardSummary().data;
const orgs = { orgs: [{ slug: 'org-a', root: '/synthetic/a' }, { slug: 'org-b', root: '/synthetic/b' }] };
const clients: ReturnType<typeof makeQueryClient>[] = [];
const mounts: ReturnType<typeof render>[] = [];
function task(id: string, status = 'pending', agent = 'agent-a'): TaskRecord {
  return { task_id: id, brief: `Brief ${id}`, status, assigned_agent: agent, team: 'engineering',
    block_kind: null, parent_task_id: null, revisit_of_task_id: null,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-02T00:00:00Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null, severity_rollup: status,
  } as TaskRecord;
}
function client() {
  const qc = makeQueryClient();
  qc.setQueryDefaults(['orgs'], { staleTime: Infinity });
  qc.setQueryDefaults(['dashboard-summary'], { staleTime: Infinity });
  qc.setQueryData(['orgs'], orgs);
  for (const slug of ['org-a', 'org-b']) qc.setQueryData(['dashboard-summary', slug], summary);
  clients.push(qc);
  return qc;
}
function Location() { const location = useLocation(); return <output aria-label="Current URL">{location.pathname}</output>; }
function mount(qc: ReturnType<typeof client>, slug = 'org-a') {
  const result = render(<MemoryRouter initialEntries={[`/orgs/${slug}/tasks`]}><AppProvider client={qc}>
    <Link to="/orgs/org-a/tasks">Go A</Link><Link to="/orgs/org-b/tasks">Go B</Link><Location /><AppRoutes />
  </AppProvider></MemoryRouter>);
  mounts.push(result); return result;
}
beforeEach(() => {
  __resetTokenCacheForTests(); sessionStorage.clear();
  sessionStorage.setItem('happyranch.token', 'synthetic-stale');
  server.use(http.get('/api/v1/orgs', () => HttpResponse.json(orgs)),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json(summary)));
});
afterEach(async () => {
  for (const mounted of mounts.splice(0)) mounted.unmount();
  for (const qc of clients.splice(0)) { await qc.cancelQueries(); qc.clear(); }
  __resetTokenCacheForTests(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});
async function keyboardRetry() {
  const button = screen.getByRole('button', { name: 'Retry' });
  button.focus(); await userEvent.keyboard('{Enter}');
}
function noFalseSuccess() {
  expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
  expect(screen.queryByText('End of list')).not.toBeInTheDocument();
}
function ids() {
  return Array.from(document.querySelectorAll('[data-tasks-responsive-list] li > div > a')).map((el) => el.getAttribute('href')?.split('/').at(-1)).sort();
}
async function apply(status = '', agent = '') {
  const toggle = screen.getByRole('button', { name: 'Filter' });
  if (toggle.getAttribute('aria-expanded') === 'false') await userEvent.click(toggle);
  await userEvent.selectOptions(screen.getByLabelText('Task status'), status);
  const input = screen.getByLabelText('Assigned agent (exact name)');
  await userEvent.clear(input); if (agent) await userEvent.type(input, agent);
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
}

describe('C13 actual Tasks route auth recovery', () => {
  test.each(['a', 'b', 'c', 'd'] as const)('C13-%s exact attempts and final recovery', async (variant) => {
    const qc = client();
    qc.setQueryData(['tasks-roots-infinite', 'org-a', undefined], { pages: [{ tasks: [task('ALIEN')], next_cursor: null }], pageParams: [undefined] });
    if (variant === 'd') { __resetTokenCacheForTests(); sessionStorage.clear(); }
    let recovered = false; let bootstraps = 0;
    const roots: { bearer: string | null; params: string; slug: string }[] = [];
    server.use(
      http.get('/api/v1/auth/bootstrap', () => {
        bootstraps++;
        return variant === 'd' && !recovered ? new HttpResponse(null, { status: 403 }) : HttpResponse.json({ token: 'synthetic-fresh' });
      }),
      http.get('/api/v1/orgs/:slug/tasks/roots', ({ request, params }) => {
        roots.push({ bearer: request.headers.get('Authorization'), params: new URL(request.url).search, slug: String(params.slug) });
        if (!recovered && variant === 'a') return new HttpResponse(null, { status: 403 });
        if (!recovered && (variant === 'c' || (variant === 'b' && roots.length === 1))) return new HttpResponse(null, { status: 401 });
        return HttpResponse.json({ tasks: [task('B-1'), task('B-2')], next_cursor: null });
      }),
    );
    // Observe requests independently of MSW's successful shell handlers: a handled
    // org/dashboard request is still an isolation failure in these four cases.
    const shellRequests: string[] = [];
    const observeRequest = ({ request }: { request: Request }) => {
      const path = new URL(request.url).pathname;
      if (path !== '/api/v1/auth/bootstrap' && !/^\/api\/v1\/orgs\/[^/]+\/tasks\/roots$/.test(path)) {
        shellRequests.push(`${request.method} ${request.url}`);
      }
    };
    function healthyShell() {
      expect(shellRequests, 'no shell or unrelated HTTP, even when MSW handles it').toEqual([]);
      for (const [key, data] of [
        [['orgs'], orgs],
        [['dashboard-summary', 'org-a'], summary],
        [['dashboard-summary', 'org-b'], summary],
      ] as const) {
        // Bootstrap can fail before HTTP reaches MSW; the ledger alone cannot
        // prove that a shell query remained healthy in that case.
        expect(qc.getQueryState(key), `healthy shell query ${key.join('/')}`).toMatchObject({
          status: 'success', fetchStatus: 'idle', error: null, errorUpdateCount: 0,
          fetchFailureCount: 0, fetchFailureReason: null, data,
        });
      }
    }
    const taskHrefs = () => Array.from(document.querySelectorAll('a[href*="/tasks/"]'))
      .map((anchor) => anchor.getAttribute('href')).sort();
    server.events.on('request:start', observeRequest);
    try {
      mount(qc, 'org-b');
      if (variant === 'b') {
        await screen.findByText('Brief B-2');
        expect(roots.map((r) => r.bearer)).toEqual(['Bearer synthetic-stale', 'Bearer synthetic-fresh']);
        expect(roots[0].params).toBe(roots[1].params);
      } else {
        await screen.findByText('Could not load tasks'); noFalseSuccess(); expect(ids()).toEqual([]);
        expect(roots).toHaveLength(variant === 'd' ? 0 : variant === 'c' ? 2 : 1);
        expect(bootstraps).toBe(variant === 'a' ? 0 : 1);
        healthyShell();
        expect(taskHrefs()).toEqual([]);
        expect(screen.queryByText('Brief ALIEN')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Current URL').textContent).toBe('/orgs/org-b/tasks');
        recovered = true; await keyboardRetry(); await screen.findByText('Brief B-2');
      }
      healthyShell();
      expect(taskHrefs()).toEqual(['/orgs/org-b/tasks/B-1', '/orgs/org-b/tasks/B-2']);
      expect(screen.getByText('Brief B-1')).toBeInTheDocument();
      expect(screen.getByText('Brief B-2')).toBeInTheDocument();
      expect(screen.getByLabelText('Current URL').textContent).toBe('/orgs/org-b/tasks');
      expect(roots.every((r) => r.slug === 'org-b' && r.params === '?limit=50')).toBe(true);
      expect(roots).toHaveLength(variant === 'c' ? 3 : variant === 'd' ? 1 : 2);
      expect(bootstraps).toBe(variant === 'a' ? 0 : variant === 'd' ? 2 : 1);
      if (variant !== 'a') expect(roots.at(-1)?.bearer).toBe('Bearer synthetic-fresh');
      expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
      expect(screen.queryByText('Brief ALIEN')).not.toBeInTheDocument();
    } finally {
      server.events.removeListener('request:start', observeRequest);
    }
  });
});

test('C06 real provider Apply/Clear, closed drafts, exact params and truthful mock parity', async () => {
  const fixtures = [task('P-A'), task('P-B', 'pending', 'agent-b'), task('C-A', 'completed'), task('C-B', 'completed', 'agent-b')];
  const ledger: URLSearchParams[] = [];
  server.use(http.get('/api/v1/orgs/org-a/tasks/roots', ({ request }) => {
    const p = new URL(request.url).searchParams; ledger.push(p);
    return HttpResponse.json({ tasks: fixtures.filter((t) => (!p.get('status') || t.status === p.get('status')) && (!p.get('assigned_agent') || t.assigned_agent === p.get('assigned_agent'))), next_cursor: null });
  }));
  mount(client()); await screen.findByText('Brief P-A');
  await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
  await userEvent.selectOptions(screen.getByLabelText('Task status'), 'completed');
  await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
  expect(ledger).toHaveLength(1); expect(ids()).toHaveLength(4);
  await apply('pending'); await waitFor(() => expect(ids()).toEqual(['P-A', 'P-B']));
  await apply('', 'agent-b'); await waitFor(() => expect(ids()).toEqual(['C-B', 'P-B']));
  await apply('completed', 'agent-a'); await waitFor(() => expect(ids()).toEqual(['C-A']));
  await userEvent.click(screen.getByRole('tab', { name: 'Agent' }));
  expect(ids()).toEqual(['C-A']); expect(screen.getByText(/Applied filters:/)).toHaveTextContent('completed');
  await apply('', 'missing-agent'); await screen.findByText('No tasks'); expect(ids()).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
  await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
  await waitFor(() => expect(ids()).toEqual(['C-A', 'C-B', 'P-A', 'P-B']));
  expect(screen.queryByText(/Applied filters:/)).not.toBeInTheDocument();
  expect(ledger.every((p) => p.get('limit') === '50' && !p.has('before'))).toBe(true);
  expect(ledger.map((p) => [p.get('status'), p.get('assigned_agent')])).toEqual([[null, null], ['pending', null], [null, 'agent-b'], ['completed', 'agent-a'], [null, 'missing-agent']]);
  // Clear reuses the still-fresh unfiltered cache; it never requests a fake null assignee.
  const mock = mockTasksApi.useTasksRootsInfinite({ status: 'completed', assigned_agent: 'missing-agent' });
  expect(mock.data?.pages.flatMap((p) => p.tasks)).toEqual([]);
});

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}
const combinations = (['status', 'agent', 'org'] as const).flatMap((switchBy) =>
  (['initial', 'stale'] as const).flatMap((mode) => [false, true].flatMap((oldFail) => [false, true].map((finalFail) => ({ switchBy, mode, oldFail, finalFail })))));

describe.each(['M', 'R'] as const)('C07-%s manual recovery ownership', (family) => {
  test.each(combinations)('$switchBy $mode oldFail=$oldFail finalFail=$finalFail', async ({ switchBy, mode, oldFail, finalFail }) => {
    const qc = client();
    const params = (context: 'A' | 'B') => switchBy === 'status' ? { status: context === 'A' ? 'pending' : 'completed' }
      : switchBy === 'agent' ? { assigned_agent: context === 'A' ? 'agent-a' : 'agent-b' } : undefined;
    const slug = (context: 'A' | 'B') => switchBy === 'org' && context === 'B' ? 'org-b' : 'org-a';
    const key = (context: 'A' | 'B') => ['tasks-roots-infinite', slug(context), params(context)];
    const rows = (context: string, revision: string, page: number) => [task(`${context}-${revision}-${page}`, context === 'B' && switchBy === 'status' ? 'completed' : 'pending', context === 'B' && switchBy === 'agent' ? 'agent-b' : 'agent-a')];
    if (mode === 'stale') for (const context of ['A', 'B'] as const) qc.setQueryData(key(context), {
      pages: [{ tasks: rows(context, 'cached', 1), next_cursor: `${context}-cursor` }, { tasks: rows(context, 'cached', 2), next_cursor: null }], pageParams: [undefined, `${context}-cursor`],
    });
    const old = gate(); const current = gate();
    let oldPageTwo = false;
    type Phase = 'error' | 'old' | 'current';
    const phase: Record<'A' | 'B', Phase> = { A: 'error', B: 'error' };
    const ledger: { context: string; phase: Phase; before: string | null; status: string | null; agent: string | null; limit: string | null; settled: boolean }[] = [];
    server.use(http.get('/api/v1/orgs/:slug/tasks/roots', async ({ request, params: route }) => {
      const p = new URL(request.url).searchParams;
      if (switchBy !== 'org' && !p.has('status') && !p.has('assigned_agent')) return HttpResponse.json({ tasks: [], next_cursor: null });
      const context = (switchBy === 'org' ? route.slug === 'org-b' : switchBy === 'status' ? p.get('status') === 'completed' : p.get('assigned_agent') === 'agent-b') ? 'B' : 'A';
      // This provider does not consume AbortSignal. A cancelled stale refresh
      // can finish its already-started page chain after its query promise settles.
      // The old gate is released before the current first page, so its page two
      // is distinguishable without relabeling it as a current attempt.
      const oldContinuation = family === 'R' && context === 'A' && mode === 'stale' && !oldFail && p.has('before') && !oldPageTwo;
      if (oldContinuation) oldPageTwo = true;
      const state = oldContinuation ? 'old' : phase[context];
      const receipt = { context, phase: state, before: p.get('before'), status: p.get('status'), agent: p.get('assigned_agent'), limit: p.get('limit'), settled: false }; ledger.push(receipt);
      if (state === 'old') await old.promise;
      if (state === 'current') await current.promise;
      receipt.settled = true;
      if (state === 'error' || (state === 'old' ? oldFail : finalFail)) return new HttpResponse(null, { status: 500 });
      return HttpResponse.json({ tasks: rows(context, state, p.has('before') ? 2 : 1), next_cursor: mode === 'stale' && !p.has('before') ? `${context}-cursor` : null });
    }));
    async function switchTo(context: 'A' | 'B') {
      if (switchBy === 'org') await userEvent.click(screen.getByRole('link', { name: `Go ${context}` }));
      else await apply(params(context)?.status ?? '', params(context)?.assigned_agent ?? '');
    }
    async function establishError(context: 'A' | 'B') {
      await act(() => qc.invalidateQueries({ queryKey: key(context), exact: true }));
      await screen.findByText(mode === 'initial' ? 'Could not load tasks' : 'Tasks may be out of date');
    }
    try {
      mount(qc);
      if (switchBy !== 'org') await switchTo('A');
      await establishError('A');
      phase.A = 'old'; await keyboardRetry();
      await waitFor(() => expect(ledger.some((r) => r.phase === 'old')).toBe(true));
      await switchTo('B');
      const target = family === 'M' ? 'B' : 'A';
      if (family === 'R') {
        // Cancellation settles the old real query promise now, not at late network release.
        await act(() => qc.cancelQueries({ queryKey: key('A'), exact: true }));
        expect(qc.getQueryState(key('A'))?.fetchStatus).toBe('idle');
        phase.A = 'error'; await switchTo('A');
      }
      await establishError(target);
      phase[target] = 'current'; await keyboardRetry();
      await waitFor(() => expect(ledger.some((r) => r.phase === 'current')).toBe(true));
      const pending = () => mode === 'initial' ? expect(screen.getByRole('button', { name: 'Retrying…' })).toBeInTheDocument()
        : expect(screen.getByRole('button', { name: 'Retry' })).toBeDisabled();
      pending();
      old.release();
      await waitFor(() => expect(ledger.filter((r) => r.phase === 'old').every((r) => r.settled)).toBe(true));
      if (family === 'R' && mode === 'stale' && !oldFail) await waitFor(() => expect(oldPageTwo).toBe(true));
      if (family === 'M') await waitFor(() => expect(qc.getQueryState(key('A'))?.fetchStatus).toBe('idle'));
      pending(); expect(qc.getQueryState(key(target))?.fetchStatus).toBe('fetching');
      expect(ids()).toEqual(mode === 'initial' ? [] : [`${target}-cached-1`, `${target}-cached-2`]);
      current.release();
      await waitFor(() => expect(qc.getQueryState(key(target))?.fetchStatus).toBe('idle'));
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Retrying…' })).not.toBeInTheDocument());
      if (finalFail) {
        await screen.findByText(mode === 'initial' ? 'Could not load tasks' : 'Tasks may be out of date'); noFalseSuccess();
        expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
        expect(ids()).toEqual(mode === 'initial' ? [] : [`${target}-cached-1`, `${target}-cached-2`]);
      } else {
        await waitFor(() => expect(ids()).toEqual(mode === 'initial' ? [`${target}-current-1`] : [`${target}-current-1`, `${target}-current-2`]));
        expect(screen.queryByText('Tasks may be out of date')).not.toBeInTheDocument();
        expect(screen.queryByText('Could not load tasks')).not.toBeInTheDocument();
      }
      const currentRequests = ledger.filter((r) => r.phase === 'current');
      expect(currentRequests.map((r) => r.before)).toEqual(mode === 'stale' && !finalFail ? [null, `${target}-cursor`] : [null]);
      expect(currentRequests.every((r) => r.context === target && r.limit === '50' && r.status === (params(target)?.status ?? null) && r.agent === (params(target)?.assigned_agent ?? null))).toBe(true);
      expect(screen.getByLabelText('Current URL')).toHaveTextContent(`/orgs/${slug(target)}/tasks`);
    } finally {
      old.release(); current.release();
      await act(() => qc.cancelQueries());
      await waitFor(() => expect(ledger.every((r) => r.settled)).toBe(true));
    }
  }, 20000);
});

test('C01/C02/C04/C05 common scroll/grid, seven groups, honest content and per-row opacity', async () => {
  const statuses = ['escalated', 'in_progress', 'pending', 'completed', 'failed', 'cancelled', 'superseded'];
  const fixtures = statuses.map((s, i) => ({ ...task(`T-${i}`, s, i === 0 ? '' : 'agent-a'), dispatched_from_thread_id: i === 0 ? null : 'THR-A' }));
  fixtures[1] = { ...fixtures[1], brief: '\n\n# First headline\nPrivate second line', block_kind: 'delegated', severity_rollup: 'failed' } as typeof fixtures[1];
  server.use(http.get('/api/v1/orgs/org-a/tasks/roots', () => HttpResponse.json({ tasks: fixtures, next_cursor: null })));
  mount(client()); await screen.findByText('First headline');
  expect(screen.queryByText('Private second line')).not.toBeInTheDocument();
  expect(screen.getByText('First headline')).toHaveAttribute('title', fixtures[1].brief);
  expect(screen.getByText('waiting on subtasks')).toBeInTheDocument();
  expect(screen.getByText('subtask failed')).toBeInTheDocument();
  const list = screen.getByTestId('tasks-responsive-list');
  const headings = () => within(list).getAllByRole('heading');
  expect(headings().map((h) => h.textContent)).toEqual(['Waiting on you1', 'In progress1', 'Pending1', 'Completed1', 'Failed1', 'Cancelled1', 'Resolved1']);
  expect(screen.getByText(/LOADED MATCHING ROOT TASKS/)).toHaveTextContent('7 LOADED MATCHING ROOT TASKS');
  expect(screen.getByTestId('tasks-page-header').closest('.overflow-y-auto')).toBe(list.closest('.overflow-y-auto'));
  expect(list.firstElementChild).toHaveClass('tasks-grid');
  for (const group of ['Agent', 'Thread', 'Status']) {
    await userEvent.click(screen.getByRole('tab', { name: group }));
    expect(ids()).toEqual(fixtures.map((t) => t.task_id).sort());
    for (const row of list.querySelectorAll('li > div')) {
      const anchor = row.querySelector('a')!;
      expect(anchor).toHaveClass('tasks-grid');
      expect(anchor.querySelectorAll(':scope > div')).toHaveLength(6);
      expect(row.classList.contains('opacity-60')).toBe(anchor.getAttribute('href')?.endsWith('T-6'));
    }
  }
  expect(screen.queryByRole('button', { name: /New task|show subtasks/i })).not.toBeInTheDocument();
});

test.each([false, true])('C10/C11 duplicate sentinel, repeated retry and terminal empty=%s', async (emptyTerminal) => {
  let intersect: IntersectionObserverCallback | undefined;
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) { intersect = callback; }
    observe() {} disconnect() {} unobserve() {}
  });
  const qc = client(); const held = gate(); let pageAttempts = 0;
  const first = Array.from({ length: 50 }, (_, i) => task(`P-${String(i).padStart(2, '0')}`));
  const ledger: string[] = [];
  server.use(http.get('/api/v1/orgs/org-a/tasks/roots', async ({ request }) => {
    const before = new URL(request.url).searchParams.get('before'); ledger.push(before ?? 'first');
    if (!before) return HttpResponse.json({ tasks: first, next_cursor: 'next' });
    const attempt = ++pageAttempts;
    if (attempt === 1) await held.promise;
    return attempt < 3 ? new HttpResponse(null, { status: 500 }) : HttpResponse.json({ tasks: emptyTerminal ? [] : [task('LAST', 'completed')], next_cursor: null });
  }));
  const trigger = () => act(async () => { intersect?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver); });
  try {
    mount(qc); await screen.findByText('Brief P-49');
    await trigger(); await trigger(); await waitFor(() => expect(pageAttempts).toBe(1));
    expect(ids()).toHaveLength(50); held.release();
    await screen.findByText('Could not load more tasks');
    await trigger(); expect(pageAttempts).toBe(1);
    const retryPage = () => userEvent.click(screen.getByRole('button', { name: 'Retry loading more tasks' }));
    await retryPage(); await waitFor(() => expect(pageAttempts).toBe(2));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry loading more tasks' })).toBeEnabled());
    expect(ids()).toHaveLength(50); noFalseSuccess();
    await retryPage(); await screen.findByText('End of list');
    expect(ids()).toEqual([...(emptyTerminal ? [] : ['LAST']), ...first.map((t) => t.task_id)]);
    await trigger();
    expect(screen.queryByText('Could not load more tasks')).not.toBeInTheDocument();
    expect(ledger).toEqual(['first', 'next', 'next', 'next']);
  } finally { held.release(); await qc.cancelQueries(); }
});

describe('C07-P page error and pending ownership', () => {
  test.each((['status', 'agent', 'org'] as const).flatMap((switchBy) =>
    (['failed', 'held-success', 'held-failure'] as const).flatMap((oldResult) => [false, true].map((returnToA) => ({ switchBy, oldResult, returnToA })))))('$switchBy $oldResult return=$returnToA', async ({ switchBy, oldResult, returnToA }) => {
    const qc = client(); const old = gate(); const current = gate();
    let observer: IntersectionObserverCallback | undefined;
    vi.stubGlobal('IntersectionObserver', class { constructor(callback: IntersectionObserverCallback) { observer = callback; } observe() {} disconnect() {} });
    const params = (c: string) => switchBy === 'status' ? { status: c === 'A' ? 'pending' : 'completed' } : switchBy === 'agent' ? { assigned_agent: c === 'A' ? 'agent-a' : 'agent-b' } : undefined;
    const slug = (c: string) => switchBy === 'org' && c === 'B' ? 'org-b' : 'org-a';
    const key = (c: string) => ['tasks-roots-infinite', slug(c), params(c)];
    let phase: 'old' | 'current' | 'recovery' = 'old';
    const ledger: { context: string; before: string | null; phase: string; settled: boolean }[] = [];
    server.use(http.get('/api/v1/orgs/:slug/tasks/roots', async ({ request, params: route }) => {
      const p = new URL(request.url).searchParams;
      if (switchBy !== 'org' && !p.has('status') && !p.has('assigned_agent')) return HttpResponse.json({ tasks: [], next_cursor: null });
      const c = (switchBy === 'org' ? route.slug === 'org-b' : switchBy === 'status' ? p.get('status') === 'completed' : p.get('assigned_agent') === 'agent-b') ? 'B' : 'A';
      const state = phase; const before = p.get('before'); const receipt = { context: c, before, phase: state, settled: false }; ledger.push(receipt);
      if (!before) { receipt.settled = true; return HttpResponse.json({ tasks: [task(`${c}-first`)], next_cursor: `${c}-cursor` }); }
      if (state === 'old' && oldResult !== 'failed') await old.promise;
      if (state === 'current') await current.promise;
      receipt.settled = true;
      if (state === 'current' || (state === 'old' && oldResult !== 'held-success')) return new HttpResponse(null, { status: 500 });
      return HttpResponse.json({ tasks: [task(`${c}-${state}`)], next_cursor: null });
    }));
    const trigger = () => act(async () => { observer?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver); });
    const switchTo = async (c: 'A' | 'B') => {
      if (switchBy === 'org') await userEvent.click(screen.getByRole('link', { name: `Go ${c}` }));
      else await apply(params(c)?.status ?? '', params(c)?.assigned_agent ?? '');
    };
    try {
      mount(qc); if (switchBy !== 'org') await switchTo('A'); await screen.findByText('Brief A-first');
      await trigger(); await waitFor(() => expect(ledger.some((r) => r.before === 'A-cursor')).toBe(true));
      if (oldResult === 'failed') await screen.findByText('Could not load more tasks');
      await switchTo('B'); await screen.findByText('Brief B-first');
      expect(screen.queryByText('Could not load more tasks')).not.toBeInTheDocument();
      if (returnToA) {
        await act(() => qc.cancelQueries({ queryKey: key('A'), exact: true }));
        await switchTo('A'); await screen.findByText('Brief A-first');
      }
      const target = returnToA ? 'A' : 'B'; phase = 'current'; await trigger();
      await waitFor(() => expect(ledger.some((r) => r.phase === 'current' && r.before === `${target}-cursor`)).toBe(true));
      old.release(); await waitFor(() => expect(ledger.filter((r) => r.phase === 'old').every((r) => r.settled)).toBe(true));
      expect(qc.getQueryState(key(target))?.fetchStatus).toBe('fetching');
      expect(ids()).toEqual([`${target}-first`]);
      expect(screen.queryByText('Could not load more tasks')).not.toBeInTheDocument();
      current.release(); await screen.findByText('Could not load more tasks'); noFalseSuccess();
      phase = 'recovery'; await userEvent.click(screen.getByRole('button', { name: 'Retry loading more tasks' }));
      await screen.findByText(`Brief ${target}-recovery`); await screen.findByText('End of list');
      expect(ids()).toEqual([`${target}-first`, `${target}-recovery`]);
      expect(ledger.filter((r) => r.context === 'B' && !r.before)).toHaveLength(1);
      expect(ledger.every((r) => !r.before || r.before === `${r.context}-cursor`)).toBe(true);
    } finally { old.release(); current.release(); await qc.cancelQueries(); await waitFor(() => expect(ledger.every((r) => r.settled)).toBe(true)); }
  });
});

test('C03 exact root/predecessor/successor navigation and C12 list-only AppBar', async () => {
  const fixture = { ...task('ROOT', 'completed'), revisit_of_task_id: 'PREVIOUS', direct_revisits: ['NEXT-1', 'NEXT-2'] };
  server.use(
    http.get('/api/v1/orgs/:slug/tasks/roots', () => HttpResponse.json({ tasks: [fixture], next_cursor: null })),
    http.get('/api/v1/orgs/:slug/tasks/:id', ({ params }) => HttpResponse.json({ task: task(String(params.id), 'completed'), active_chain: null })),
    http.get('/api/v1/orgs/:slug/tasks/:id/recall', ({ params }) => HttpResponse.json({ ...task(String(params.id), 'completed'), children: [] })),
    http.get('/api/v1/orgs/:slug/jobs/', () => HttpResponse.json({ jobs: [] })),
  );
  mount(client()); await screen.findByText('Brief ROOT');
  expect(document.querySelector('.tasks-appbar')).not.toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }));
  expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  expect(document.querySelector('a a')).toBeNull();
  for (const id of ['ROOT', 'PREVIOUS', 'NEXT-1', 'NEXT-2']) {
    const link = document.querySelector(`a[href="/orgs/org-a/tasks/${id}"]`) as HTMLElement;
    link.focus(); await userEvent.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByLabelText('Current URL')).toHaveTextContent(`/orgs/org-a/tasks/${id}`));
    expect((await screen.findAllByText(`Brief ${id}`)).length).toBeGreaterThan(0);
    expect(document.querySelector('.tasks-appbar')).toBeNull();
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    await userEvent.click(screen.getByRole('link', { name: 'Go A' })); await screen.findByText('Brief ROOT');
  }
  await userEvent.click(screen.getByRole('link', { name: 'Go B' })); await screen.findByText('Brief ROOT');
  expect(document.querySelector('a[href="/orgs/org-b/tasks/PREVIOUS"]')).not.toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));
  expect(document.documentElement).toHaveAttribute('data-theme', 'light');
});

test.each([[0, 'just now'], [5, '5m'], [120, '2h'], [2880, '2d']] as const)('C02 relative age %s minutes', (minutes, expected) => {
  const now = Date.parse('2026-09-13T12:00:00Z'); vi.spyOn(Date, 'now').mockReturnValue(now);
  const record = { ...task('LONG-TASK-IDENTIFIER'), updated_at: new Date(now - minutes * 60_000).toISOString() };
  render(<MemoryRouter><TaskListRow task={record} to="/orgs/org-a/tasks/LONG-TASK-IDENTIFIER" taskRoutes={{ detail: (id) => `/orgs/org-a/tasks/${id}` }} /></MemoryRouter>);
  expect(screen.getByText(expected)).toHaveClass('text-right', 'font-mono', 'text-task-meta');
  expect(screen.getByTitle(record.task_id)).toHaveTextContent(record.task_id);
});

test('C04 recency within groups and alphabetic agent/thread order', async () => {
  const fixtures = [
    { ...task('EARLY', 'pending', 'agent-z'), updated_at: '2026-09-01T00:00:00Z', dispatched_from_thread_id: 'THR-Z' },
    { ...task('LATE', 'pending', 'agent-z'), updated_at: '2026-09-03T00:00:00Z', dispatched_from_thread_id: 'THR-Z' },
    { ...task('ALPHA'), dispatched_from_thread_id: 'THR-A' }, task('NONE', 'pending', ''),
  ];
  server.use(http.get('/api/v1/orgs/org-a/tasks/roots', () => HttpResponse.json({ tasks: fixtures, next_cursor: null })));
  mount(client()); await screen.findByText('Brief EARLY');
  const list = screen.getByTestId('tasks-responsive-list');
  const orderedIds = (container: Element) => Array.from(container.querySelectorAll('li > div > a')).map((a) => a.getAttribute('href')?.split('/').at(-1));
  expect(orderedIds(list)).toEqual(['LATE', 'ALPHA', 'NONE', 'EARLY']);
  await userEvent.click(screen.getByRole('tab', { name: 'Agent' }));
  expect(within(list).getAllByRole('heading').map((h) => h.textContent)).toEqual(['agent-a1', 'agent-z2', 'Unassigned1']);
  expect(orderedIds(screen.getByRole('heading', { name: 'agent-z 2' }).parentElement!)).toEqual(['LATE', 'EARLY']);
  await userEvent.click(screen.getByRole('tab', { name: 'Thread' }));
  expect(within(list).getAllByRole('heading').map((h) => h.textContent)).toEqual(['No thread1', 'THR-A1', 'THR-Z2']);
  expect(ids()).toEqual(['ALPHA', 'EARLY', 'LATE', 'NONE']);
});

test('C12 Tasks AppBar assistant action reaches the real closed/open dock', async () => {
  let requests = 0;
  server.use(
    http.get('/api/v1/orgs/org-a/tasks/roots', () => HttpResponse.json({ tasks: [task('ROOT')], next_cursor: null })),
    http.get('/api/v1/assistant/status', () => { requests++; return HttpResponse.json({ state: 'uninitialized', selected_executor: null, workspace_path: null, detail: null }); }),
  );
  mount(client()); await screen.findByText('Brief ROOT'); expect(requests).toBe(0);
  await userEvent.click(screen.getByRole('button', { name: 'Open assistant' }));
  const dock = screen.getByRole('dialog', { name: 'Ranch Assistant' });
  expect(await within(dock).findByText('Assistant is not ready. Set it up from Settings → Assistant.')).toBeInTheDocument();
  expect(requests).toBe(1);
  await userEvent.click(screen.getByRole('button', { name: 'Close assistant' }));
  await waitFor(() => expect(dock).not.toHaveAttribute('aria-modal'));
  expect(dock).toHaveClass('pointer-events-none');
  expect(ids()).toEqual(['ROOT']);
});

test('C02 legacy blocked displays its exact status and full long identities, without a filter option', async () => {
  const agent = 'agent-with-a-long-exact-runtime-identity';
  const thread = 'THR-WITH-A-LONG-EXACT-RUNTIME-IDENTITY';
  const record = { ...task('TASK-WITH-A-LONG-EXACT-RUNTIME-IDENTITY', 'blocked', agent), dispatched_from_thread_id: thread,
    brief: `\n# ${'UnbrokenHeadline'.repeat(20)}\nFull second line remains accessible`,
  };
  server.use(http.get('/api/v1/orgs/org-a/tasks/roots', () => HttpResponse.json({ tasks: [record], next_cursor: null })));
  mount(client()); await screen.findByText('End of list');
  const row = document.querySelector('[data-tasks-responsive-list] li > div > a')! as HTMLElement;
  expect(within(row).getByText('blocked')).toHaveClass('tasks-status');
  const headline = within(row).getByText('UnbrokenHeadline'.repeat(20));
  expect(headline).toHaveClass('truncate');
  expect(headline).toHaveAttribute('title', record.brief);
  expect(within(row).getByTitle(record.task_id)).toHaveTextContent(record.task_id);
  expect(within(row).getByTitle(agent)).toHaveTextContent(agent);
  expect(within(row).getByTitle(thread)).toHaveTextContent(thread);
  await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
  expect(screen.queryByRole('option', { name: 'blocked' })).not.toBeInTheDocument();
});

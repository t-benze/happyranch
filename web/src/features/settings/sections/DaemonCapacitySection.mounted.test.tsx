/**
 * L4 / L5 — the REAL frontend boundary.
 *
 * Production component + `hooks/settings` + `DataContext` + `_real-settings`
 * + React Query + `client.ts` + HTTP, mounted under a data router with the
 * navigation guard live. NOTHING is mocked below the wire.
 *
 * Every "a request was / was not issued" assertion is made from the captured
 * request list at the HTTP boundary — never from a DOM `disabled` attribute.
 * A `disabled` attribute is a render-time property, not an ordering guarantee.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { focusManager } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { capacityObservation, capacityQueryKey } from '@/design-system/providers/_capacity-ordering';
import { server } from '@/test/server';
import { formatReceipt, classifySnapshot } from './capacityModel';
import { renderGuarded } from './capacityTestMount';

const SLUG = 'alpha';
const CAPACITY = `/api/v1/orgs/${SLUG}/settings/daemon-capacity`;
const REV_A = `sha256:${'a'.repeat(64)}`;
const REV_B = `sha256:${'b'.repeat(64)}`;
const REV_C = `sha256:${'c'.repeat(64)}`;
const REV_D = `sha256:${'d'.repeat(64)}`;

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'startup-resolved settings snapshot',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [] as string[],
    environment_warning: null as string | null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [] as string[],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: { queue_workers: 'g-w', host_global_session_cap: 'g-h', enforced: false },
    authorization: 'daemon bearer required',
    ...overrides,
  };
}

const SETTINGS_PAYLOAD = {
  system: {
    claude_cli_path: { value: 'claude', restart_required: true },
    codex_cli_path: { value: 'codex', restart_required: true },
    opencode_cli_path: { value: 'opencode', restart_required: true },
    pi_cli_path: { value: 'pi', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    reviewer_agents: [],
    dreaming: { enabled: false, schedule: { time: '02:00', timezone: 'UTC' }, catch_up_on_startup: false, agents: { mode: 'all', include: [], exclude: [] } },
    threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
    working_hours: {
      enabled: false, agents: { mode: 'all', include: [], exclude: [] },
      default: { mode: 'always', window: null, interval: null, days: [], catch_up_on_startup: false },
      teams: {}, overrides: {},
    },
  },
};

interface CapturedRequest {
  method: string;
  ifMatch: string | null;
  rawBody: string;
}

let captured: CapturedRequest[] = [];
/** Paths hit that this venue never declared — a fail-closed receipt. */
let undeclared: string[] = [];

function gets(): CapturedRequest[] {
  return captured.filter((r) => r.method === 'GET');
}
function puts(): CapturedRequest[] {
  return captured.filter((r) => r.method === 'PUT');
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/**
 * Hand every request its OWN undisturbed body.
 *
 * A gated fixture (`put: () => gate.promise`) returns the SAME `Response`
 * object to every request that reaches it. The first delivery consumes that
 * body; the second makes the interceptor construct a `Response` from a
 * disturbed source and throws `TypeError: Response body object should not be
 * disturbed or locked` as an UNHANDLED rejection — the suite still reports
 * green tests while the run exits non-zero. Cloning on the way out means the
 * fixture's own object is never consumed and each request gets a fresh body.
 */
async function fresh(source: Promise<Response> | Response): Promise<Response> {
  return (await source).clone();
}

/**
 * Declare the venue. Every capacity request is captured; an `/api/` path this
 * venue did not declare is RECORDED and fails the exercise rather than quietly
 * succeeding.
 */
function stubVenue(options: {
  get?: (index: number) => Promise<Response> | Response;
  put?: (index: number) => Promise<Response> | Response;
} = {}) {
  let getIndex = 0;
  let putIndex = 0;
  server.use(
    http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'tok' })),
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () => HttpResponse.json(SETTINGS_PAYLOAD)),
    // App-shell sidebar request — declared explicitly so it is an allowed,
    // enumerated path rather than an undeclared fall-through.
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({
      counts: {}, recent_tasks: [], escalations: [], agents: [],
    })),
    http.get(CAPACITY, async ({ request }) => {
      captured.push({ method: 'GET', ifMatch: request.headers.get('if-match'), rawBody: '' });
      const index = getIndex;
      getIndex += 1;
      return fresh(options.get ? options.get(index) : HttpResponse.json(snapshot()));
    }),
    http.put(CAPACITY, async ({ request }) => {
      captured.push({
        method: 'PUT',
        ifMatch: request.headers.get('if-match'),
        rawBody: await request.text(),
      });
      const index = putIndex;
      putIndex += 1;
      return fresh(options.put ? options.put(index) : HttpResponse.json(snapshot({ revision: REV_B })));
    }),
    http.all('/api/*', ({ request }) => {
      undeclared.push(new URL(request.url).pathname);
      return new HttpResponse(null, { status: 599 });
    }),
  );
}

function mount(path = `/orgs/${SLUG}/settings/daemon-capacity`) {
  return renderGuarded(<AppRoutes />, { entries: [path] });
}

const workers = () => screen.getByLabelText(/Task session slots/);
const cap = () => screen.getByLabelText(/Host session admission limit/);
const reasonBox = () => screen.getByLabelText('Reason for change');
/**
 * Container-scoped accessors. Two editors can be mounted at once with the SAME
 * `label[for]` id, so a global label query is ambiguous; these read the control
 * inside one editor's own subtree.
 */
const workersIn = (root: HTMLElement) =>
  root.querySelector('#capacity-workers') as HTMLInputElement;
const capIn = (root: HTMLElement) =>
  root.querySelector('#capacity-cap') as HTMLInputElement;
const reasonIn = (root: HTMLElement) =>
  root.querySelector('#capacity-reason') as HTMLTextAreaElement;
const saveButton = () => screen.getByRole('button', { name: /Save for next restart|Saving/ });
function workersRow(): HTMLTableRowElement {
  return within(screen.getByRole('table')).getAllByRole('row')[1] as HTMLTableRowElement;
}
const runningCell = () => workersRow().cells[1];
const savedCell = () => workersRow().cells[2];
const nextCell = () => workersRow().cells[3];
/** The ACCEPTED BASE row of the reconciliation panel — not the daemon-observed table. */
const acceptedBaseText = () =>
  screen.getByText('Accepted base').parentElement?.textContent ?? '';

async function ready() {
  await screen.findByRole('heading', { name: 'Capacity' }, { timeout: 5000 });
  await waitFor(() => expect(workers()).toHaveValue('3'));
}

async function setPair(w: string, h: string) {
  const user = userEvent.setup();
  await user.clear(workers());
  await user.type(workers(), w);
  await user.clear(cap());
  await user.type(cap(), h);
}

async function saveWith(reason = 'measured receipts') {
  const user = userEvent.setup();
  await user.type(reasonBox(), reason);
  await user.click(saveButton());
}

beforeEach(() => {
  captured = [];
  undeclared = [];
});

// ---------------------------------------------------------------------------
describe('1 / 5 / 6 — staged save at the real boundary', () => {
  test('1.1 a usable success advances saved/expected/revision without touching running', async () => {
    stubVenue({
      put: () => HttpResponse.json(snapshot({
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
        restart_pending: true, revision: REV_B,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();

    await screen.findByText('Saved for next restart. Running limits are unchanged.');
    // Running is UNCHANGED by a save.
    expect(runningCell()).toHaveTextContent('3');
    expect(savedCell()).toHaveTextContent('5');
    expect(nextCell()).toHaveTextContent('5');
    expect(screen.getByText('Restart pending')).toBeVisible();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(reasonBox()).toHaveValue('');
    expect(undeclared, `undeclared paths: ${undeclared.join(', ')}`).toEqual([]);
  });

  test('1.2 a 200 missing revision is UNKNOWN: base is not advanced and the submission is retained', async () => {
    const bad = snapshot({ revision: undefined as unknown as string });
    stubVenue({ put: () => HttpResponse.json(bad) });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();

    await screen.findByText(/Save result unknown/);
    expect(screen.queryByText(/Saved for next restart\./)).not.toBeInTheDocument();
    expect(savedCell()).toHaveTextContent('3');
    expect(await screen.findByText(/You submitted Task session slots 5/)).toBeInTheDocument();
  });

  test('1.4 no saved/applied claim appears while the request is in flight', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();

    await screen.findByText('Saving for next restart…');
    expect(savedCell()).toHaveTextContent('3');
    expect(document.body).not.toHaveTextContent(/Saved for next restart\.|Applied/);
    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
  });

  test('5.1 a below-envelope value SAVES with the waiting warning and NO extra acknowledgment', async () => {
    // A COHERENT accepted response for the 3/5 submission: the same pair that
    // was sent, with restart_pending true because next-start 3/5 differs from
    // running 3/10. The default 3/10 fixture would have hidden a wrong result.
    stubVenue({
      put: () => HttpResponse.json(snapshot({
        revision: REV_B,
        persisted_yaml: { queue_workers: 3, host_global_session_cap: 5 },
        next_start: { queue_workers: 3, host_global_session_cap: 5 },
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await setPair('3', '5');
    expect(document.body).toHaveTextContent(/Additional sessions will wait/);
    // No environment shadow, so no acknowledgment control exists at all.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();

    await saveWith('intentional backpressure');
    await waitFor(() => expect(puts()).toHaveLength(1));
    const body = JSON.parse(puts()[0].rawBody);
    expect(body.host_global_session_cap).toBe(5);
    expect(body.confirm_environment_shadow).toBe(false);
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('3'));
    // The HOST row's next start moved 10 -> 5 (the workers row stays 3).
    const hostNextCell = () =>
      (within(screen.getByRole('table')).getAllByRole('row')[2] as HTMLTableRowElement).cells[3];
    await waitFor(() => expect(hostNextCell()).toHaveTextContent('5'));
    expect(nextCell()).toHaveTextContent('3');
    // No extra mandatory acknowledgment was introduced by the waiting warning.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
  });

  test('5.5 L4 a real mounted excess-cap save adds no capability claim before OR after settlement', async () => {
    stubVenue({
      put: () => HttpResponse.json(snapshot({
        revision: REV_B,
        persisted_yaml: { queue_workers: 3, host_global_session_cap: 30 },
        next_start: { queue_workers: 3, host_global_session_cap: 30 },
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await userEvent.clear(cap());
    await userEvent.type(cap(), '30');
    // The excess cap is above the worker-pool total 10: the copy says extra room
    // adds no producers and never claims more capacity/throughput.
    expect(document.body).toHaveTextContent(/Extra admission room does not create additional producers/);
    expect(document.body).not.toHaveTextContent(/more capacity|higher throughput|additional capability|faster/i);

    await saveWith('raise the cap');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 3,
      host_global_session_cap: 30,
      rationale: 'raise the cap',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('3'));
    const hostNextCell = () =>
      (within(screen.getByRole('table')).getAllByRole('row')[2] as HTMLTableRowElement).cells[3];
    await waitFor(() => expect(hostNextCell()).toHaveTextContent('30'));
    // The honest excess-cap copy survives into the settled result panel, and no
    // capability/throughput claim appears anywhere in the post-success state.
    expect(document.body).toHaveTextContent(/Extra admission room does not create additional producers/);
    expect(document.body).not.toHaveTextContent(/more capacity|higher throughput|additional capability|faster/i);
    expect(document.body).not.toHaveTextContent(/Applied|Apply now|Restart daemon/);
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
  });

  test('6.2 staging absent keys sends both values explicitly and moves presence to present', async () => {
    stubVenue({
      get: () => HttpResponse.json(snapshot({
        persisted_yaml: { queue_workers: null, host_global_session_cap: null },
      })),
      put: () => HttpResponse.json(snapshot({ revision: REV_B })),
    });
    mount();
    await ready();
    expect(within(screen.getByRole('table')).getAllByText('Not set in file')).toHaveLength(2);

    // No digit is changed — only a reason is typed.
    await saveWith('stage the observed defaults');
    await waitFor(() => expect(puts()).toHaveLength(1));
    const body = JSON.parse(puts()[0].rawBody);
    expect(body.queue_workers).toBe(3);
    expect(body.host_global_session_cap).toBe(10);
    await waitFor(() => expect(savedCell()).toHaveTextContent('3'));
    expect(within(screen.getByRole('table')).queryAllByText('Not set in file')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
describe('2 — in-flight capture, ordering and receipt ownership', () => {
  test('2.1 controls are disabled while the write is pending', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();

    await waitFor(() => expect(saveButton()).toBeDisabled());
    expect(workers()).toBeDisabled();
    expect(cap()).toBeDisabled();
    expect(reasonBox()).toBeDisabled();
    expect(screen.getByRole('button', { name: /Discard draft/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Refresh running state/ })).toBeDisabled();
    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
  });

  test('2.2 duplicate clicks issue EXACTLY ONE PUT', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    mount();
    await ready();
    await setPair('5', '12');
    const user = userEvent.setup();
    await user.type(reasonBox(), 'r');
    await user.click(saveButton());
    await user.click(saveButton()).catch(() => undefined);
    await user.click(saveButton()).catch(() => undefined);

    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
    expect(puts()).toHaveLength(1);
  });

  test('2.3 the success callback reports the CAPTURED submission, never a concurrent read', async () => {
    const gate = deferred<Response>();
    stubVenue({
      // A concurrent background read carrying 9/9 lands while the PUT is open.
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: 9, host_global_session_cap: 9 },
          next_start: { queue_workers: 9, host_global_session_cap: 9 },
          revision: REV_C,
        }))),
      put: () => gate.promise,
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    await view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    gate.resolve(HttpResponse.json(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      revision: REV_B,
    })));

    await screen.findByText(/Saved/);
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(savedCell()).not.toHaveTextContent('9');
  });

  test('2.9 the receipt advances on a byte-IDENTICAL successful refetch', async () => {
    stubVenue();
    const view = mount();
    await ready();
    const first = screen.getByText(/Last received/).textContent;
    expect(first).toMatch(/this browser's clock/);
    const observedFirst = capacityObservation(SLUG);
    expect(observedFirst?.receiptAt).toEqual(expect.any(Number));

    // An identical payload: React Query structural sharing returns a
    // reference-identical object, so a component-observed data change would be
    // ABSENT here. The provider-owned receipt still advances.
    await new Promise((r) => setTimeout(r, 1100));
    const getsBefore = gets().length;
    await view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await waitFor(() => {
      expect(screen.getByText(/Last received/).textContent).not.toBe(first);
    });
    // A genuine network response was issued, and the provider-owned receipt —
    // not component state — is what moved.
    expect(gets().length).toBe(getsBefore + 1);
    const observedSecond = capacityObservation(SLUG);
    expect(observedSecond!.receiptAt!).toBeGreaterThan(observedFirst!.receiptAt!);
    expect(observedSecond!.issuedSeq).toBeGreaterThan(observedFirst!.issuedSeq);
  });

  test('2.10 a cached remount issues no request and invents no receipt', async () => {
    stubVenue();
    // The Organization panel we pass through owns one request of its own. It is
    // declared EXPLICITLY so it stays an enumerated allowed path; the capacity
    // fence below still fails closed on anything else.
    server.use(http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })));
    const user = userEvent.setup();
    mount();
    await ready();
    const receipt = screen.getByText(/Last received/).textContent;
    const observedBefore = capacityObservation(SLUG);
    const getsBefore = gets().length;
    expect(getsBefore).toBe(1);

    // A REAL remount against the SAME QueryClient: the operator navigates to
    // another Settings panel — which unmounts the capacity section — and back.
    // Rerendering `<div />` would only have proved that an unmounted tree makes
    // no request; it never remounts the section against the cache at all.
    await user.click(screen.getByRole('link', { name: 'Organization' }));
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Capacity' })).not.toBeInTheDocument();
    });
    await user.click(screen.getByRole('link', { name: 'Daemon / Capacity' }));
    await screen.findByRole('heading', { name: 'Capacity' });
    await waitFor(() => expect(workers()).toHaveValue('3'));

    // Served from the cache: zero additional GETs at the HTTP boundary, the
    // rendered receipt is byte-identical, and NO new observation was recorded.
    expect(gets()).toHaveLength(getsBefore);
    expect(screen.getByText(/Last received/).textContent).toBe(receipt);
    expect(capacityObservation(SLUG)).toEqual(observedBefore);
    expect(undeclared).toEqual([]);
  });

  test('2.13 what is ACTUALLY suppressed, measured at the HTTP boundary — a globally disabled focus trigger, NOT a pending-specific one', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    const before = gets().length;
    const observedBefore = capacityObservation(SLUG);

    // (a) The ONE trigger this app really does suppress. A non-operator trigger
    // owned by React Query: the window regains focus while the write is in
    // flight. `AppProvider` ships `refetchOnWindowFocus: false`, so the trigger
    // is disabled BEFORE any request leaves.
    //
    // HONESTY FENCE, and the reason this case is worded conditionally: this is
    // a GLOBAL default, not pending-specific behaviour. The implementation has
    // NO suppression keyed on `save.isPending`, and this observation must never
    // be presented as one. Asserting `Refresh` is `disabled` is case 2.5.
    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await new Promise((r) => setTimeout(r, 50));
    expect(gets()).toHaveLength(before);
    expect(capacityObservation(SLUG)).toEqual(observedBefore);
    expect(screen.getByText('Saving for next restart…')).toBeVisible();

    // (b) The REAL provider refetch attempt during the pending window. Because
    // no pending-specific suppression exists, it DOES reach the network. The
    // accepted case allows exactly this: its zero-request clause is conditional
    // on an implementation that suppresses reads while a write is pending.
    const permitted = view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await waitFor(() => expect(gets()).toHaveLength(before + 1));
    const afterProviderAttempt = gets().length;

    // (c) MANDATORY SECOND HALF — a genuinely SEPARATE stale capacity observer
    // mounted in the same window, on the SAME QueryClient, WITHOUT resetting
    // the ordering ledger. It carries none of this component's pending state,
    // and it DOES issue its own GET: suppression in React Query is
    // per-observer, never per-key. This is exactly why the during-PUT ordering
    // fence (2.14-2.16) is required IN ADDITION TO any suppression.
    await permitted;
    await view.client.invalidateQueries({
      queryKey: capacityQueryKey(SLUG), refetchType: 'none',
    });
    // The overlapping consumer STAYS MOUNTED through the pending write and its
    // FINAL settlement — it is not unmounted to make the ending convenient.
    const second = renderGuarded(<AppRoutes />, {
      client: view.client,
      entries: [`/orgs/${SLUG}/settings/daemon-capacity`],
      resetOrdering: false,
    });
    await waitFor(() => expect(gets().length).toBeGreaterThan(afterProviderAttempt));

    // Coherent settlement for the SUBMITTED 5/12 — the same pair the operator
    // sent, a new revision, and a pending restart because next-start 5/12
    // differs from running 3/10.
    gate.resolve(HttpResponse.json(snapshot({
      revision: REV_B,
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
    })));
    await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('usable'));
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
    expect(capacityObservation(SLUG)?.receiptAt).not.toBeNull();

    // The still-mounted overlapping consumer settles on the SAME accepted
    // result: its cache carries the accepted pair/revision and its query
    // observation is a success, with no fabricated usable state from the
    // during-write read.
    await waitFor(() => {
      expect(
        second.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
      ).toBe(REV_B);
    });
    expect(
      second.client.getQueryData<{ persisted_yaml: { queue_workers: number } }>(
        capacityQueryKey(SLUG),
      )?.persisted_yaml.queue_workers,
    ).toBe(5);
    expect(second.client.getQueryState(capacityQueryKey(SLUG))?.status).toBe('success');
    expect(second.client.isMutating()).toBe(0);
    // The FIRST editor accepted the write: clean draft/reason and disarmed guard.
    expect(view.client.getQueryState(capacityQueryKey(SLUG))?.status).toBe('success');
    expect(undeclared).toEqual([]);
    second.unmount();
  });

  test('C3 / 2.13b a clean SECOND editor adopts another editor\'s accepted write and saves the ADOPTED base', async () => {
    const gate = deferred<Response>();
    stubVenue({
      put: (i) => (i === 0
        ? gate.promise
        : HttpResponse.json(snapshot({
          revision: REV_C,
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 5, host_global_session_cap: 12 },
          restart_pending: true,
        }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    // A REAL second editor on the SAME QueryClient, mounted during the pending
    // write, with the ordering ledger NOT reset.
    await view.client.invalidateQueries({ queryKey: capacityQueryKey(SLUG), refetchType: 'none' });
    const second = renderGuarded(<AppRoutes />, {
      client: view.client,
      entries: [`/orgs/${SLUG}/settings/daemon-capacity`],
      resetOrdering: false,
    });
    const secondUi = within(second.container);
    await waitFor(() => expect(workersIn(second.container)).toHaveValue('3'));

    // The FIRST editor's coherent 5/12 @ REV_B settlement.
    gate.resolve(HttpResponse.json(snapshot({
      revision: REV_B,
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
    })));
    const firstUi = within(view.container);

    // INITIATOR: accepted, clean, with NO phantom "changed elsewhere" against
    // the revision it just saved.
    await firstUi.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(workersIn(view.container)).toHaveValue('5'));
    expect(capIn(view.container)).toHaveValue('12');
    expect(reasonIn(view.container)).toHaveValue('');
    expect(firstUi.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(firstUi.queryByText(/Configuration changed elsewhere\./)).not.toBeInTheDocument();
    expect(firstUi.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);

    // SECOND editor: adopted the SAME accepted base and pair from the shared
    // cache; it is clean and has manufactured no reconciliation.
    await waitFor(() => expect(workersIn(second.container)).toHaveValue('5'));
    expect(capIn(second.container)).toHaveValue('12');
    expect(secondUi.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(secondUi.queryByText(/Configuration changed elsewhere\./)).not.toBeInTheDocument();
    expect(
      second.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_B);

    // Submit against the ADOPTED base. The stale-base defect sent If-Match REV_A
    // with the 3/10 pair here.
    await userEvent.type(reasonIn(second.container), 'second editor reason');
    await userEvent.click(secondUi.getByRole('button', { name: /Save for next restart/ }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'second editor reason',
      confirm_environment_shadow: false,
    });

    // The second editor drains its OWN coherent settlement.
    await secondUi.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(reasonIn(second.container)).toHaveValue(''));
    expect(workersIn(second.container)).toHaveValue('5');
    expect(
      second.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_C);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
    expect(undeclared).toEqual([]);
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
    second.unmount();
  });

  /**
   * Drive the accepted during-PUT ordering scenario to its FINAL settled state.
   *
   * The write is gated; a permitted non-operator read is issued WHILE it is in
   * flight; the write settles with the accepted 5/12 @ REV_B; then the obsolete
   * read settles with `lateRead`. Returns everything the caller needs to assert
   * the final surface, the provider receipt and the cache.
   */
  async function duringPutOrdering(lateRead: () => Response) {
    const putGate = deferred<Response>();
    const getGate = deferred<Response>();
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : getGate.promise),
      // Only the FIRST write is gated. Later saves are separate requests and
      // get their own responses — reusing the gated one would hand the
      // interceptor a consumed body.
      put: (i) => (i === 0 ? putGate.promise : HttpResponse.json(snapshot({
        persisted_yaml: { queue_workers: 6, host_global_session_cap: 12 },
        next_start: { queue_workers: 6, host_global_session_cap: 12 },
        restart_pending: true, revision: REV_C,
      }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    // Issued DURING the write — a higher ISSUE seq than the write's issue, so a
    // naive issue-keyed filter would let it through.
    const stale = view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    putGate.resolve(HttpResponse.json(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true, revision: REV_B,
    })));
    await screen.findByText(/Saved for next restart/);
    const receiptAfterSave = capacityObservation(SLUG)?.receiptAt ?? null;
    const renderedReceipt = screen.getAllByText(/Last received/)[0].textContent;
    expect(receiptAfterSave).not.toBeNull();

    getGate.resolve(lateRead());
    await stale.catch(() => undefined);
    return { view, receiptAfterSave, renderedReceipt };
  }

  /** Every assertion the accepted 2.14/2.15 FINAL state requires. */
  async function expectAcceptedPostSaveState(
    view: ReturnType<typeof mount>,
    receiptAfterSave: number | null,
    renderedReceipt: string | null | undefined,
  ) {
    // Displayed surface: accepted pair, revision and badge; no flicker back.
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(nextCell()).toHaveTextContent('5');
    expect(runningCell()).toHaveTextContent('3');
    expect(screen.getByText('Restart pending')).toBeVisible();
    // The load-error branch must NOT replace the saved surface.
    expect(screen.queryByText(/No values are displayed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not load daemon capacity/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
    expect(screen.queryByText('Last known')).not.toBeInTheDocument();
    // Editor: draft equals base, reason cleared, no guard, no residual lock.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Reconcile the saved values/)).not.toBeInTheDocument();
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
    // PROVIDER receipt and CACHE, not just a formatted string.
    expect(capacityObservation(SLUG)?.receiptAt ?? null).toBe(receiptAfterSave);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
    expect(
      view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_B);
    expect(screen.getAllByText(/Last received/)[0].textContent).toBe(renderedReceipt);
  }

  /**
   * 2.16 — recovery CONTINUING from a settled 2.14/2.15 outcome. A read issued
   * AFTER the write settled publishes normally, the receipt ADVANCES, and a
   * separate manual save carries the recovered revision.
   */
  async function expectLaterRecovery(
    view: ReturnType<typeof mount>,
    receiptAfterSave: number | null,
  ) {
    // Declare the coherent CONTINUING fixtures BEFORE any request is issued: a
    // usable recovery read at REV_C, and an accepted response for the 7/12
    // submission below carrying the same pair at a NEW revision with
    // restart_pending true.
    server.use(
      http.get(CAPACITY, async ({ request }) => {
        captured.push({ method: 'GET', ifMatch: request.headers.get('if-match'), rawBody: '' });
        return HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 5, host_global_session_cap: 12 },
          restart_pending: true, revision: REV_C,
        }));
      }),
      http.put(CAPACITY, async ({ request }) => {
        captured.push({
          method: 'PUT',
          ifMatch: request.headers.get('if-match'),
          rawBody: await request.text(),
        });
        return HttpResponse.json(snapshot({
          revision: REV_D,
          persisted_yaml: { queue_workers: 7, host_global_session_cap: 12 },
          next_start: { queue_workers: 7, host_global_session_cap: 12 },
          restart_pending: true,
        }));
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C));
    // Ordering safety was not bought by refusing later reads forever.
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect((capacityObservation(SLUG)?.receiptAt ?? 0)).toBeGreaterThanOrEqual(receiptAfterSave ?? 0);
    expect(capacityObservation(SLUG)?.receiptAt).not.toBe(receiptAfterSave);
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('');

    const receiptBeforeFinal = capacityObservation(SLUG)?.receiptAt ?? null;
    const putsBefore = puts().length;
    await setPair('7', '12');
    await saveWith('after recovery');
    await waitFor(() => expect(puts()).toHaveLength(putsBefore + 1));
    const final = puts()[putsBefore];
    expect(final.ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(final.rawBody)).toEqual({
      queue_workers: 7,
      host_global_session_cap: 12,
      rationale: 'after recovery',
      confirm_environment_shadow: false,
    });

    // Drive the FINAL manual save to its ACTUAL terminal settlement: returned
    // pair/revision, cache, receipt, draft/reason, lock/submission and guard.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('7'));
    expect(nextCell()).toHaveTextContent('7');
    expect(workers()).toHaveValue('7');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('');
    expect(
      view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_D);
    expect(
      view.client.getQueryData<{ persisted_yaml: { queue_workers: number } }>(
        capacityQueryKey(SLUG),
      )?.persisted_yaml.queue_workers,
    ).toBe(7);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.receiptAt ?? null).not.toBe(receiptBeforeFinal);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Reconcile the saved values/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  }

  test('2.14 / 2.16 a permitted during-PUT read landing as a stale SUCCESS is dropped, then a later read recovers', async () => {
    const { view, receiptAfterSave, renderedReceipt } = await duringPutOrdering(
      () => HttpResponse.json(snapshot({ revision: REV_A })),
    );
    await expectAcceptedPostSaveState(view, receiptAfterSave, renderedReceipt);

    // The FINAL separate manual save carries the ACCEPTED revision, read from
    // the captured header — never from component state.
    await setPair('6', '12');
    await saveWith('follow-up');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    const body = JSON.parse(puts()[1].rawBody);
    expect(body).toEqual({
      queue_workers: 6,
      host_global_session_cap: 12,
      rationale: 'follow-up',
      confirm_environment_shadow: false,
    });
    expect('revision' in body).toBe(false);
    // Drain the final transition rather than leaving a response in flight, and
    // assert the returned pair/revision, cache, receipt, clean draft/reason,
    // released submission/lock and disarmed guard.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('6'));
    expect(nextCell()).toHaveTextContent('6');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('');
    expect(
      view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_C);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test.each([
    ['a network failure', () => HttpResponse.error()],
    ['an HTTP 500', () => new HttpResponse(null, { status: 500 })],
    ['a raw `not json` 200 body', () => new HttpResponse('not json', {
      headers: { 'content-type': 'application/json' },
    })],
  ])('2.15 / 2.16 the same during-PUT read landing as %s never downgrades the accepted state, and recovery still works', async (_label, late) => {
    const { view, receiptAfterSave, renderedReceipt } = await duringPutOrdering(late as () => Response);
    await expectAcceptedPostSaveState(view, receiptAfterSave, renderedReceipt);
    await expectLaterRecovery(view, receiptAfterSave);
  });

  test('2.16 recovery CONTINUES the 2.14 stale-success predecessor to a genuine receipt advance and a separate manual save', async () => {
    const { view, receiptAfterSave, renderedReceipt } = await duringPutOrdering(
      () => HttpResponse.json(snapshot({ revision: REV_A })),
    );
    await expectAcceptedPostSaveState(view, receiptAfterSave, renderedReceipt);
    await expectLaterRecovery(view, receiptAfterSave);
  });
});

// ---------------------------------------------------------------------------
describe('4 — acknowledgment identity at the real boundary', () => {
  const shadow = (resolvedW: number, cap = 12) => snapshot({
    environment_shadowed: ['queue_workers'],
    environment_warning: 'Environment overrides win.',
    next_start: { queue_workers: resolvedW, host_global_session_cap: 12 },
    effective_admission_cap: cap,
  });

  test('3.1 / 3.4 the W-only override resolves the consequence BEFORE and AFTER a successful save', async () => {
    const shadowW = {
      environment_shadowed: ['queue_workers'],
      environment_warning: 'Environment overrides win.',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    };
    stubVenue({
      get: () => HttpResponse.json(snapshot(shadowW)),
      put: () => HttpResponse.json(snapshot({
        ...shadowW,
        revision: REV_B,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 14 },
        next_start: { queue_workers: 3, host_global_session_cap: 14 },
        // The resolved next-start pair (3/14) differs from running (3/10), so a
        // coherent server response reports a pending restart.
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await setPair('5', '14');

    // PRE-SAVE: resolved W = 3, H = 14; pool 3 + 7 = 10 — never the drafted 5.
    expect(document.body).toHaveTextContent(/Task session slots 3, Host session admission limit 14/);
    expect(document.body).toHaveTextContent(/Task session slots is set by the environment/);
    expect(screen.getByText('Worker-pool total').nextElementSibling?.firstChild?.textContent).toBe('10');
    expect(screen.getByText('Worker-pool total').parentElement?.textContent)
      .toContain('3 task + 7 other producers');
    expect(document.body).not.toHaveTextContent(/worker-pool total 12/);
    // 3.4: no predicted FUTURE effective admission cap anywhere.
    expect(document.body).toHaveTextContent(/Assumes unchanged environment and\s+worker topology/);
    expect(document.body).not.toHaveTextContent(/future effective|effective admission (cap )?will/i);

    await userEvent.click(screen.getByRole('checkbox'));
    await saveWith('raise task slots');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 14,
      rationale: 'raise task slots',
      confirm_environment_shadow: true,
    });

    // POST-SAVE: the result panel resolves the SAME W = 3 / H = 14 pair and
    // names only the shadowed key. No "Applied", no restart claim.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    expect(document.body).toHaveTextContent(/Saved value overridden: Task session slots is set by the environment/);
    expect(document.body).toHaveTextContent(/Expected next start: Task session slots 3, Host session admission limit 14/);
    expect(document.body).not.toHaveTextContent(/Applied|Apply now|Restart daemon/);
    expect(savedCell()).toHaveTextContent('5');
    expect(nextCell()).toHaveTextContent('3');
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
  });

  test('4.1 an identical context refresh PRESERVES the acknowledgment', async () => {
    stubVenue({ get: () => HttpResponse.json(shadow(3)) });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));
    expect(screen.getByRole('checkbox')).toBeChecked();
    expect(screen.queryByText(/environment override changed/)).not.toBeInTheDocument();
  });

  test('4.1b a CHANGED RESOLVED VALUE at the SAME revision clears the ack and blocks the write', async () => {
    // Same revision, same shadowed keys, same warning text — only the
    // environment-resolved value moves 3 -> 4. The daemon resolves shadowed
    // keys from the environment while the revision is file-bytes-derived, so
    // this combination is genuinely reachable.
    stubVenue({
      get: (i) => HttpResponse.json(shadow(i === 0 ? 3 : 4)),
      // Coherent accepted response for the renewed 5/12 submission: the
      // persisted pair that was sent, the environment still resolving W to 4,
      // and a pending restart.
      put: () => HttpResponse.json(snapshot({
        environment_shadowed: ['queue_workers'],
        environment_warning: 'Environment overrides win.',
        revision: REV_C,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 4, host_global_session_cap: 12 },
        effective_admission_cap: 12,
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    const user = userEvent.setup();
    await user.type(reasonBox(), 'raising slots');
    await user.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();

    await user.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('The environment override changed; confirm it again before saving.');

    // Base, draft and reason are PRESERVED; only the ack is cleared.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('raising slots');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    // The preview refreshes to the NEW resolved value.
    expect(document.body).toHaveTextContent(/Task session slots 4, Host session admission limit 12/);
    // 4.1b: at the SAME revision, with the resolved override changed and the
    // renewed acknowledgment missing, Save is EXPLICITLY disabled.
    expect(saveButton()).toBeDisabled();

    // No PUT until renewed confirmation.
    await user.click(saveButton());
    expect(puts()).toHaveLength(0);

    await user.click(screen.getByRole('checkbox'));
    expect(saveButton()).toBeEnabled();
    await user.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_A}"`);
    // FULL final request body after renewal — H, rationale and ack included.
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'raising slots',
      confirm_environment_shadow: true,
    });
    // ...and the accepted result settles coherently.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(nextCell()).toHaveTextContent('4');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
  });

  test('4.5 an unrelated effective_admission_cap change must NOT over-reset the acknowledgment', async () => {
    stubVenue({ get: (i) => HttpResponse.json(shadow(3, i === 0 ? 12 : 4)) });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));
    expect(screen.getByRole('checkbox')).toBeChecked();
    expect(screen.queryByText(/environment override changed/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
describe('7 / 8 — dirty refresh and conflict reconciliation', () => {
  test('7.1 a same-revision refresh updates observations with NO changed-elsewhere notice', async () => {
    stubVenue({ get: (i) => HttpResponse.json(snapshot({ effective_admission_cap: i === 0 ? 12 : 4 })) });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    expect(screen.queryByText(/Configuration changed elsewhere/)).not.toBeInTheDocument();
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('why');
  });

  test('8.1 / 8.2 a 409 preserves the draft, then rebase + manual save carries the LATEST revision', async () => {
    stubVenue({
      put: (i) => (i === 0
        ? HttpResponse.json({
          detail: {
            code: 'stale_revision',
            latest: snapshot({
              revision: REV_B,
              persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
            }),
          },
        }, { status: 409 })
        : HttpResponse.json(snapshot({
          revision: REV_C,
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 5, host_global_session_cap: 12 },
        }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');

    await screen.findByText('Saved settings changed elsewhere. Your draft is preserved.');
    // Draft, reason and base are all intact.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('measured receipts');
    expect(savedCell()).toHaveTextContent('3');
    expect(document.body).toHaveTextContent(/Task session slots 2, Host session admission limit 9/);

    // 8.4 — a second save WITHOUT choosing a control issues no PUT.
    await userEvent.click(saveButton());
    expect(puts()).toHaveLength(1);
    expect(await screen.findByText(/Reconcile the saved values before saving again/)).toBeInTheDocument();

    // 8.2 — rebase keeps the draft verbatim and sends NO request by itself.
    await userEvent.click(screen.getByRole('button', { name: /Keep my draft, rebase onto latest/ }));
    expect(puts()).toHaveLength(1);
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('measured receipts');

    // The FINAL separate manual save carries If-Match: "<REV_B>".
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    const body = JSON.parse(puts()[1].rawBody);
    expect(body.queue_workers).toBe(5);
    expect(body.host_global_session_cap).toBe(12);
    expect(body.rationale).toBe('measured receipts');
    await screen.findByText(/^Saved(\.| for next restart\.)/);
  });

  test('8.3 accept-latest resets the form, sends no request, and a later edit uses the latest revision', async () => {
    stubVenue({
      put: (i) => (i === 0
        ? HttpResponse.json({
          detail: {
            code: 'stale_revision',
            latest: snapshot({
              revision: REV_B,
              persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
            }),
          },
        }, { status: 409 })
        : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText('Saved settings changed elsewhere. Your draft is preserved.');

    await userEvent.click(screen.getByRole('button', { name: /Discard draft, accept latest/ }));
    expect(puts()).toHaveLength(1);
    await waitFor(() => expect(workers()).toHaveValue('2'));
    expect(cap()).toHaveValue('9');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();

    // A SUBSEQUENT intentional edit + save carries the adopted revision.
    await setPair('8', '15');
    await saveWith('new intent');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody).queue_workers).toBe(8);
  });
});

// ---------------------------------------------------------------------------
describe('10 / 11 — uncertain and unknown outcomes', () => {
  test('10.2 / 10.3 a typed publication-uncertain retains the submission and checks saved values', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          revision: REV_B,
        }))),
      put: () => HttpResponse.json(
        { detail: { code: 'config_publication_uncertain', artifact_state: 'unknown' } },
        { status: 503 },
      ),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');

    await screen.findByText(/durability, verification, or cleanup did not complete/);
    expect(document.body).toHaveTextContent(/not a confirmation that your values are in effect/i);
    expect(await screen.findByText(/You submitted Task session slots 5/)).toBeInTheDocument();
    expect(savedCell()).toHaveTextContent('3');

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/Saved values now match what you submitted/);
    // The wording is exactly this and NOTHING stronger.
    expect(document.body).toHaveTextContent(/does not confirm your request caused it/i);
    expect(document.body).not.toHaveTextContent(/save succeeded|audit completed/i);
    // The read did NOT advance BASE automatically. `base` is the accepted-base
    // row of the reconciliation panel; the table column is the daemon's own
    // observation and is expected to move with the read.
    expect(acceptedBaseText()).toMatch(/Task session slots 3/);
  });

  test('10.4 a differing reread names the difference and never auto-resubmits', async () => {
    stubVenue({
      put: () => HttpResponse.json(
        { detail: { code: 'config_publication_uncertain' } }, { status: 503 },
      ),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    const putsBefore = puts().length;
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/The saved values are unchanged from your accepted base/);
    expect(document.body).toHaveTextContent(/outcome of your request is still unknown/i);
    expect(puts()).toHaveLength(putsBefore);
  });

  test('11.2 after settlement a NEWER draft is held independently from the pinned submission', async () => {
    stubVenue({ put: () => HttpResponse.error() });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);

    // Fields re-enable after settlement (S4 disables only during flight).
    await waitFor(() => expect(workers()).toBeEnabled());
    await setPair('7', '14');

    // Both records are named distinctly; neither is reported as the other.
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(document.body).toHaveTextContent(/current draft is Task session slots 7/);
    expect(document.body).toHaveTextContent(/held separately from the submitted values/);
  });

  test('11.8 the write stays blocked and the guard stays armed until an explicit choice', async () => {
    stubVenue({ put: () => HttpResponse.error() });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);

    const putsBefore = puts().length;
    await userEvent.click(saveButton());
    expect(puts()).toHaveLength(putsBefore);
    expect(await screen.findByText(/Reconcile the saved values before saving again/)).toBeInTheDocument();
    // The unresolved outcome keeps unsaved-work protection armed.
    expect(screen.getByText(/Unsaved changes/)).toBeVisible();
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  test('9.4 a generic 500 where a 409 was expected is UNKNOWN, not "rejected" and not "failed safely"', async () => {
    stubVenue({ put: () => new HttpResponse(null, { status: 500 }) });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Save result unknown/);
    expect(document.body).not.toHaveTextContent(/failed safely|rejected/i);
  });
});

// ---------------------------------------------------------------------------
describe('13 / 14 — denied and unusable reads', () => {
  test('13.3 an initial 401 renders NO protected values', async () => {
    stubVenue({ get: () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 }) });
    mount();
    await screen.findByText(/Could not load daemon capacity/, {}, { timeout: 5000 });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/startup-resolved settings snapshot/);
    expect(document.body).not.toHaveTextContent(/startup policy/);
    expect(screen.queryByLabelText(/Task session slots/)).not.toBeInTheDocument();
  });

  const SHADOW_W = {
    environment_shadowed: ['queue_workers'],
    environment_warning: 'Environment overrides win; a restart alone will not make YAML win.',
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
  };

  /** Every rendered receipt string on screen. There is more than one venue. */
  function receiptStrings(): string[] {
    return screen.getAllByText(/Last received/).map((node) => node.textContent ?? '');
  }

  test('14.1 an unusable refresh keeps base/draft/reason/ack, labels the retained values "Last known", and blocks save AND rebase', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot(SHADOW_W))
        : HttpResponse.json(snapshot({
          ...SHADOW_W,
          persisted_yaml: { queue_workers: '3', host_global_session_cap: 10 },
        }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();
    const before = capacityObservation(SLUG)?.receiptAt ?? null;

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText(/Cannot read capacity configuration/);

    // Editor state survives in full.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('why');
    expect(screen.getByRole('checkbox')).toBeChecked();

    // R9 14.1: the prior observations are actually RETAINED and LABELLED, not
    // merely described by a sentence — the running cards and the four-column
    // table still render the last usable values under "Last known".
    expect(screen.getByText('Last known')).toBeVisible();
    expect(screen.queryByText('Running now')).not.toBeInTheDocument();
    expect(runningCell()).toHaveTextContent('3');
    expect(savedCell()).toHaveTextContent('3');
    expect(receiptStrings().length).toBeGreaterThan(0);

    // The unusable read is neither a saveable base nor a rebase target.
    expect(saveButton()).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();

    // Handler-level refusal, proven at the HTTP boundary.
    const form = saveButton().closest('form') as HTMLFormElement;
    fireEvent.submit(form);
    await screen.findByText(/could not be read, so nothing was sent/);
    expect(puts()).toHaveLength(0);
    // S5-R5: an unusable BODY still arrived on a genuine successful network
    // response, so the browser receipt advances — but the OBSERVATION is not
    // usable, so nothing was accepted from it (R7).
    expect(capacityObservation(SLUG)?.receiptAt ?? null).not.toBe(before);
    expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
  });

  test('14.2 a refresh missing revision is unusable in exactly the same way', async () => {
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        const { revision: _revision, ...withoutRevision } = snapshot();
        return HttpResponse.json(withoutRevision);
      },
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));

    await screen.findByText(/Cannot read capacity configuration/);
    expect(screen.getByText('Last known')).toBeVisible();
    expect(savedCell()).toHaveTextContent('3');
    expect(workers()).toHaveValue('5');
    expect(saveButton()).toBeDisabled();
    expect(puts()).toHaveLength(0);
  });

  test('14.3 / 14.4 R1 — a FAILED refresh preserves last-known values, receipt, editor and ack, refuses the PUT at handler AND control, and only a usable reread reconciles', async () => {
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot(SHADOW_W));
        if (i === 1) return HttpResponse.error();
        return HttpResponse.json(snapshot({
          ...SHADOW_W,
          revision: REV_B,
          persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }));
      },
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'intent');
    await userEvent.click(screen.getByRole('checkbox'));
    const receiptBefore = receiptStrings();
    const observedBefore = capacityObservation(SLUG)?.receiptAt ?? null;
    expect(observedBefore).not.toBeNull();

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(
      view.client.getQueryState(capacityQueryKey(SLUG))?.status,
    ).toBe('error'));
    expect(capacityObservation(SLUG)?.outcome).toBe('failed');

    // The warning is the accepted 14.3 wording, and the retained values are
    // explicitly labelled rather than presented as confirmed.
    await screen.findByText(/Could not refresh\. Current state unverified\./);
    expect(screen.getByText('Last known')).toBeVisible();
    expect(runningCell()).toHaveTextContent('3');
    expect(savedCell()).toHaveTextContent('3');
    // The receipt belongs to the last GENUINE successful response and does not
    // advance for a failure (S2 / S5-R5).
    expect(receiptStrings()).toEqual(expect.arrayContaining(receiptBefore));
    expect(capacityObservation(SLUG)?.receiptAt ?? null).toBe(observedBefore);

    // Editor, reason and acknowledgment all survive.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('intent');
    expect(screen.getByRole('checkbox')).toBeChecked();

    // R1: refused at the control AND at the handler — measured at the wire.
    expect(saveButton()).toBeDisabled();
    await userEvent.click(saveButton());
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
    await screen.findByText(/could not be read, so nothing was sent/);
    await waitFor(() => expect(view.client.isMutating()).toBe(0));
    expect(puts()).toHaveLength(0);

    // 14.4: a usable recovery NEVER silently rebases — it requires an explicit
    // choice, and only then does a write become possible.
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets()).toHaveLength(3));
    await screen.findByText('Configuration changed elsewhere.');
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('intent');
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
    expect(capacityObservation(SLUG)?.receiptAt ?? null).not.toBe(observedBefore);

    await userEvent.click(screen.getByRole('button', { name: 'Keep my draft, rebase onto latest' }));
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'intent',
      confirm_environment_shadow: true,
    });
  });

  test('14.4 a usable recovery after an unusable read NEVER silently rebases', async () => {
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        if (i === 1) return HttpResponse.json(snapshot({ persisted_yaml: { queue_workers: '3', host_global_session_cap: 10 } }));
        return HttpResponse.json(snapshot({
          revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }));
      },
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText(/Cannot read capacity configuration/);

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBe(3));
    // Recovery does not silently adopt the new values into the editor.
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('why');
  });

  // -------------------------------------------------------------------------
  // C1 — the retained values and their receipt are ONE record.
  //
  // A successful HTTP response whose body is unusable still advances the
  // PROVIDER receipt (S5-R5: it identifies a real response). It did not,
  // however, produce the values still on screen. Before the repair the editor
  // stored only the snapshot and rendered the LATEST provider receipt beside
  // it, so an unusable 200 re-labelled the previous values with the new
  // response's time. Independent reviewer red proof: `additional.test.tsx` /
  // `receipt-probe.log` at head a04446ca (usable 3/10 at 1800000000000, then a
  // quoted-W 200 at 1800000060000 displayed the old values with the new time).
  // -------------------------------------------------------------------------
  test('C1 / 14.1 a successful GET with an UNUSABLE body retains the last values WITH their own receipt, while the provider receipt still advances', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1800000000000);
    try {
      stubVenue({
        get: (i) => (i === 0
          ? HttpResponse.json(snapshot())
          : HttpResponse.json(snapshot({
            persisted_yaml: { queue_workers: 'bad', host_global_session_cap: 10 },
          }))),
      });
      mount();
      await ready();

      const retainedReceipt = formatReceipt(capacityObservation(SLUG)?.receiptAt ?? null);
      expect(retainedReceipt).not.toBeNull();
      expect(screen.getByText(/Last received/).textContent).toContain(retainedReceipt!);

      await userEvent.type(reasonBox(), 'retain exact draft');
      clock.mockReturnValue(1800000060000);
      await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
      await screen.findByText(/Cannot read capacity configuration/);

      // The successful response really arrived, so the PROVIDER receipt moved
      // and the observation is correctly recorded as unusable.
      expect(capacityObservation(SLUG)?.receiptAt).toBe(1800000060000);
      expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
      expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();

      // The retained values keep the receipt of the response that produced
      // them; the newer response's time is NOT attributed to them.
      expect(screen.getByText('Last known')).toBeVisible();
      expect(workers()).toHaveValue('3');
      expect(savedCell()).toHaveTextContent('3');
      expect(reasonBox()).toHaveValue('retain exact draft');
      const newReceipt = formatReceipt(1800000060000);
      expect(newReceipt).not.toBe(retainedReceipt);
      const receipts = receiptStrings();
      expect(receipts.length).toBeGreaterThan(0);
      expect(receipts[0]).toContain(retainedReceipt!);
      expect(receipts.every((text) => text.includes(retainedReceipt!))).toBe(true);
      expect(receipts.some((text) => text.includes(newReceipt!))).toBe(false);
    } finally {
      clock.mockRestore();
    }
  });

  test('C1 a byte-IDENTICAL successful refresh is retained with ITS OWN receipt when a later read fails', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1800000000000);
    try {
      // Two byte-identical usable successes, then a genuine failure.
      stubVenue({
        get: (i) => (i < 2 ? HttpResponse.json(snapshot()) : HttpResponse.error()),
      });
      const view = mount();
      await ready();
      const before = view.client.getQueryData(capacityQueryKey(SLUG));

      clock.mockReturnValue(1800000060000);
      await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
      await waitFor(() => expect(capacityObservation(SLUG)?.receiptAt).toBe(1800000060000));

      // The premise of the defect: React Query structurally shares the
      // byte-identical body, so the component sees the SAME data object. A
      // classification-only retention effect therefore never runs — yet a real
      // usable response WAS received and its receipt must be retained.
      expect(view.client.getQueryData(capacityQueryKey(SLUG))).toBe(before);
      expect(receiptStrings()[0]).toContain(formatReceipt(1800000060000)!);

      clock.mockReturnValue(1800000120000);
      await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
      await screen.findByText(/Could not refresh/);

      // The failure carries the provider receipt forward unchanged, and the
      // retained values keep t1 — never rolling back to the t0 receipt.
      expect(capacityObservation(SLUG)?.outcome).toBe('failed');
      expect(capacityObservation(SLUG)?.receiptAt).toBe(1800000060000);
      expect(receiptStrings().length).toBeGreaterThan(0);
      expect(receiptStrings().every((text) => text.includes(formatReceipt(1800000060000)!))).toBe(true);
      expect(receiptStrings().some((text) => text.includes(formatReceipt(1800000000000)!))).toBe(false);
    } finally {
      clock.mockRestore();
    }
  });

  test('C1 / 14.1–14.4 usable -> shape-malformed -> representation-unusable -> failed -> usable keeps draft/reason/ack/base and one retained receipt, and never silently rebases', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1800000000000);
    try {
      stubVenue({
        get: (i) => {
          if (i === 0) return HttpResponse.json(snapshot(SHADOW_W));
          if (i === 1) {
            // shape-malformed: a quoted number in a nullable slot
            return HttpResponse.json(snapshot({
              ...SHADOW_W,
              persisted_yaml: { queue_workers: '3', host_global_session_cap: 10 },
            }));
          }
          if (i === 2) {
            // representation-unusable: a token that did not survive JSON.parse
            // as a safe integer (2**53 is not safe)
            return HttpResponse.json(snapshot({
              ...SHADOW_W,
              persisted_yaml: { queue_workers: 2 ** 53, host_global_session_cap: 10 },
            }));
          }
          if (i === 3) return HttpResponse.error();
          // usable recovery on a NEW revision, still dirty -> explicit choice
          return HttpResponse.json(snapshot({
            ...SHADOW_W,
            revision: REV_B,
            persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
          }));
        },
        // A COHERENT accepted response for the final 5/12 manual save: the same
        // persisted pair the operator sent at a new revision. `queue_workers`
        // is environment-shadowed, so the RESOLVED next-start W stays 3 and the
        // acknowledged override remains valid. The default fixture's 3/10 body
        // would let an incoherent final state pass.
        put: () => HttpResponse.json(snapshot({
          ...SHADOW_W,
          revision: REV_C,
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 3, host_global_session_cap: 12 },
          restart_pending: true,
        })),
      });
      const view = mount();
      await ready();
      await setPair('5', '12');
      await userEvent.type(reasonBox(), 'why');
      await userEvent.click(screen.getByRole('checkbox'));
      expect(screen.getByRole('checkbox')).toBeChecked();

      const retainedReceipt = formatReceipt(capacityObservation(SLUG)?.receiptAt ?? null);
      expect(retainedReceipt).not.toBeNull();

      const refresh = async (index: number) => {
        clock.mockReturnValue(1800000000000 + index * 60000);
        await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
        await waitFor(() => expect(gets()).toHaveLength(index + 1));
      };

      // (1) shape-malformed successful 200
      await refresh(1);
      await screen.findByText(/Cannot read capacity configuration/);
      expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
      expect(receiptStrings().every((text) => text.includes(retainedReceipt!))).toBe(true);

      // (2) representation-unusable successful 200 — same retention semantics
      await refresh(2);
      await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('unusable'));
      expect(receiptStrings().every((text) => text.includes(retainedReceipt!))).toBe(true);

      // (3) genuine failure — receipt carried forward, still retained
      await refresh(3);
      await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('failed'));
      await screen.findByText(/Could not refresh/);
      expect(receiptStrings().every((text) => text.includes(retainedReceipt!))).toBe(true);

      // Across every unusable/failed read the editor, reason, ack and accepted
      // base survive byte-for-byte, and Save is refused.
      expect(workers()).toHaveValue('5');
      expect(cap()).toHaveValue('12');
      expect(reasonBox()).toHaveValue('why');
      expect(screen.getByRole('checkbox')).toBeChecked();
      // The ACCEPTED base is still the original 3/10 in the displayed table.
      expect(savedCell()).toHaveTextContent('3');
      expect(runningCell()).toHaveTextContent('3');
      expect(saveButton()).toBeDisabled();

      // (4) usable recovery on a new revision: NEVER a silent rebase. The newer
      // observation advances base only through an explicit choice, and the
      // displayed values switch back to the CURRENT observation (with its own
      // advancing receipt) rather than staying pinned to the old one.
      await refresh(4);
      await screen.findByText('Configuration changed elsewhere.');
      expect(workers()).toHaveValue('5');
      expect(reasonBox()).toHaveValue('why');
      // The accepted BASE is still 3/10 — the new revision was recorded as an
      // observation, not adopted.
      expect(acceptedBaseText()).toMatch(/Task session slots 3/);
      expect(screen.queryByText('Last known')).not.toBeInTheDocument();
      expect(capacityObservation(SLUG)?.outcome).toBe('usable');
      expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
      expect(receiptStrings()[0]).not.toContain(retainedReceipt!);

      await userEvent.click(screen.getByRole('button', { name: 'Keep my draft, rebase onto latest' }));
      // The rebase adopted the observed REV_B 2/9 as the accepted base. The
      // reconciliation panel is intentionally cleared, so the accepted base is
      // read from the displayed comparison table (saved 2, running 3).
      await waitFor(() => expect(savedCell()).toHaveTextContent('2'));
      expect(runningCell()).toHaveTextContent('3');
      clock.mockReturnValue(1800000300000);
      const receiptBeforeSave = capacityObservation(SLUG)?.receiptAt ?? null;
      await userEvent.click(saveButton());
      await waitFor(() => expect(puts()).toHaveLength(1));
      expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
      expect(JSON.parse(puts()[0].rawBody)).toEqual({
        queue_workers: 5,
        host_global_session_cap: 12,
        rationale: 'why',
        confirm_environment_shadow: true,
      });
      // Drain the manual save to its ACTUAL terminal settlement: the returned
      // 5/12 pair (not the default 3/10 body) is displayed AND cached, the
      // provider receipt advanced to the accepted response and is rendered, the
      // draft/reason are clean and the navigation guard is disarmed.
      await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
      await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
      // The environment-resolved next-start W stays 3 for the shadowed key,
      // while the persisted pair is the accepted 5/12.
      expect(nextCell()).toHaveTextContent('3');
      expect(runningCell()).toHaveTextContent('3');
      expect(workers()).toHaveValue('5');
      expect(cap()).toHaveValue('12');
      expect(reasonBox()).toHaveValue('');
      const cached = view.client.getQueryData<{
        revision: string;
        persisted_yaml: { queue_workers: number; host_global_session_cap: number };
        next_start: { queue_workers: number; host_global_session_cap: number };
      }>(capacityQueryKey(SLUG));
      expect(cached?.revision).toBe(REV_C);
      expect(cached?.persisted_yaml).toEqual({ queue_workers: 5, host_global_session_cap: 12 });
      expect(cached?.next_start).toEqual({ queue_workers: 3, host_global_session_cap: 12 });
      expect(capacityObservation(SLUG)?.outcome).toBe('usable');
      expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
      expect(capacityObservation(SLUG)?.receiptAt).not.toBeNull();
      expect(capacityObservation(SLUG)?.receiptAt).not.toBe(receiptBeforeSave);
      expect(
        receiptStrings().some((text) =>
          text.includes(formatReceipt(capacityObservation(SLUG)!.receiptAt)!),
        ),
      ).toBe(true);
      expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
      expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
      expect(saveButton()).toBeEnabled();
      const unload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(false);
    } finally {
      clock.mockRestore();
    }
  });
});

// ---------------------------------------------------------------------------
describe('19 — cache scoping and route geometry', () => {
  test('19.3 the capacity cache is scoped per org: beta never shows alpha, and saves carry beta If-Match', async () => {
    const BETA = 'beta';
    const BETA_REV = `sha256:${'d'.repeat(64)}`;
    const betaPuts: CapturedRequest[] = [];
    server.use(
      http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'tok' })),
      http.get('/api/v1/orgs', () => HttpResponse.json({
        orgs: [{ slug: SLUG, root: '/x' }, { slug: BETA, root: '/y' }],
      })),
      http.get(`/api/v1/orgs/${SLUG}/settings`, () => HttpResponse.json(SETTINGS_PAYLOAD)),
      http.get(`/api/v1/orgs/${BETA}/settings`, () => HttpResponse.json(SETTINGS_PAYLOAD)),
      http.get(`/api/v1/orgs/${BETA}/dashboard/summary`, () => HttpResponse.json({
        counts: {}, recent_tasks: [], escalations: [], agents: [],
      })),
      // Alpha is mounted first now, so its app-shell path is declared too.
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({
        counts: {}, recent_tasks: [], escalations: [], agents: [],
      })),
      http.get(CAPACITY, () => HttpResponse.json(snapshot())),
      http.get(`/api/v1/orgs/${BETA}/settings/daemon-capacity`, () => HttpResponse.json(snapshot({
        revision: BETA_REV,
        persisted_yaml: { queue_workers: 7, host_global_session_cap: 21 },
        next_start: { queue_workers: 7, host_global_session_cap: 21 },
        running_at_daemon_start: { queue_workers: 7, host_global_session_cap: 21 },
      }))),
      http.put(`/api/v1/orgs/${BETA}/settings/daemon-capacity`, async ({ request }) => {
        betaPuts.push({ method: 'PUT', ifMatch: request.headers.get('if-match'), rawBody: await request.text() });
        return HttpResponse.json(snapshot({ revision: BETA_REV }));
      }),
      http.all('/api/*', ({ request }) => {
        undeclared.push(new URL(request.url).pathname);
        return new HttpResponse(null, { status: 599 });
      }),
    );

    // R9 19.3: exercise the ACTUAL alpha -> beta transition. Mounting beta
    // directly never caches alpha, so it cannot prove that alpha's snapshot is
    // not served to beta. Alpha is mounted, cached, and navigated AWAY from.
    const view = mount();
    await ready();
    expect(savedCell()).toHaveTextContent('3');
    await waitFor(() => expect(view.client.getQueryData(['daemon-capacity', SLUG])).toBeDefined());

    act(() => { void view.router.navigate(`/orgs/${BETA}/settings/daemon-capacity`); });
    await waitFor(() => expect(workers()).toHaveValue('7'));
    // Beta's own values and revision, with NO alpha value on screen anywhere.
    expect(savedCell()).toHaveTextContent('7');
    expect(nextCell()).toHaveTextContent('7');
    expect(runningCell()).toHaveTextContent('7');
    expect(
      view.client.getQueryData<{ revision: string }>(['daemon-capacity', BETA])?.revision,
    ).toBe(BETA_REV);
    expect(
      view.client.getQueryData<{ revision: string }>(['daemon-capacity', SLUG])?.revision,
    ).toBe(REV_A);
    expect(document.body).not.toHaveTextContent(REV_A);
    expect(document.body).toHaveTextContent(BETA_REV);

    await setPair('9', '21');
    await saveWith('beta change');
    await waitFor(() => expect(betaPuts).toHaveLength(1));
    // The save carries BETA's If-Match, never alpha's.
    expect(betaPuts[0].ifMatch).toBe(`"${BETA_REV}"`);
    expect(betaPuts[0].ifMatch).not.toBe(`"${REV_A}"`);
    expect(view.client.getQueryData(['daemon-capacity', BETA])).toBeDefined();
    expect(view.client.getQueryData(['daemon-capacity', SLUG])).toBeDefined();
    expect(puts()).toHaveLength(0);
    expect(undeclared, `undeclared paths: ${undeclared.join(', ')}`).toEqual([]);
  });

  test('19.4 the panel is reached at the existing route and the Settings sub-nav shape is unchanged', async () => {
    stubVenue();
    mount();
    await ready();
    const content = screen.getByTestId('settings-content');
    const subnav = within(content).getByRole('complementary');
    expect(within(subnav).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'Daemon / Capacity', 'Assistant', 'Organization', 'Executors',
    ]);
    // The standalone mock's extra menu entries are deliberately NOT reproduced.
    expect(within(subnav).queryByText('Capacity')).not.toBeInTheDocument();
    expect(within(subnav).queryByText('System')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
describe('17 — in-app navigation guard (L5)', () => {
  test('17.2 a dirty draft intercepts an in-app route change; Stay keeps the draft byte-identical', async () => {
    stubVenue();
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'unsaved reason');

    const content = screen.getByTestId('settings-content');
    await userEvent.click(within(content).getByRole('link', { name: 'Organization' }));

    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));

    await waitFor(() => expect(screen.queryByText('Discard unsaved capacity changes?')).not.toBeInTheDocument());
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('unsaved reason');
  });

  test('17.2b Discard and continue completes the navigation', async () => {
    stubVenue();
    mount();
    await ready();
    await userEvent.type(reasonBox(), 'unsaved reason');

    const content = screen.getByTestId('settings-content');
    await userEvent.click(within(content).getByRole('link', { name: 'Organization' }));
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));

    await waitFor(() => expect(screen.queryByLabelText('Reason for change')).not.toBeInTheDocument());
  });

  test('17.5 a clean form navigates immediately with no dialog', async () => {
    stubVenue();
    mount();
    await ready();
    const content = screen.getByTestId('settings-content');
    await userEvent.click(within(content).getByRole('link', { name: 'Organization' }));
    await waitFor(() => expect(screen.queryByLabelText('Reason for change')).not.toBeInTheDocument());
    expect(screen.queryByText('Discard unsaved capacity changes?')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
/**
 * 15 / R7 — raw numeric envelopes at the MOUNTED boundary.
 *
 * Every fixture here is a RAW JSON STRING. An object literal is rounded by
 * `JSON.stringify` before it ever reaches the transport, so an object fixture
 * cannot deliver an unsafe token at all and the case would be vacuous.
 */
describe('15 / R7 — raw numeric envelopes, editor to wire and back', () => {
  const UNSAFE = '9007199254740993';
  const ROUNDED = 9007199254740992;

  /**
   * Inject the EXACT raw JSON `rawToken` at the exact path `path` of `root`,
   * starting from the serialized text.
   *
   * The earlier helper replaced the FIRST textual occurrence of a key, which for
   * `"queue_workers":3` is `running_at_daemon_start.queue_workers` — NOT the
   * `persisted_yaml.queue_workers` the case named. This one serializes the
   * parent object and replaces the parent fragment, so the path is exact; it
   * then re-parses and VERIFIES that the named path now holds the token.
   */
  function injectRaw(root: unknown, path: string[], rawToken: string): string {
    // Replace the target with a UNIQUE sentinel STRING, serialize, then swap the
    // quoted sentinel for the raw token text. This makes the modified path
    // unambiguous even when sibling members serialize identically (the earlier
    // fragment-replace hit `running_at_daemon_start` when the case named
    // `persisted_yaml`).
    const sentinel = `__HR_RAW_${Math.random().toString(36).slice(2)}__`;
    const clone = JSON.parse(JSON.stringify(root)) as Record<string, unknown>;
    const parent = path.slice(0, -1).reduce<Record<string, unknown>>(
      (node, k) => node[k] as Record<string, unknown>,
      clone,
    );
    const key = path[path.length - 1];
    expect(parent, `path ${path.join('.')} not present`).toHaveProperty(key);
    parent[key] = sentinel;
    const text = JSON.stringify(clone);
    const replaced = text.replace(JSON.stringify(sentinel), rawToken);
    expect(replaced, `path ${path.join('.')} not modified`).not.toBe(text);
    // VERIFY the exact modified path, not just "the body contains the token".
    const reparsed = JSON.parse(replaced) as Record<string, unknown>;
    const at = (node: unknown) =>
      path.reduce<unknown>(
        (n, k) => (n as Record<string, unknown>)[k],
        node,
      );
    expect(at(reparsed), `path ${path.join('.')}`).toEqual(JSON.parse(rawToken));
    return replaced;
  }

  /** A raw JSON body with the exact `path` replaced by an unsafe integer. */
  function rawWithUnsafeAt(path: string[], overrides: Record<string, unknown> = {}): Response {
    const text = injectRaw(snapshot(overrides), path, UNSAFE);
    expect(text).toContain(UNSAFE);
    expect(() => JSON.parse(text)).not.toThrow();
    return new HttpResponse(text, { headers: { 'content-type': 'application/json' } });
  }

  test('15.2 MAX_SAFE_INTEGER typed into the MOUNTED editor reaches the wire verbatim', async () => {
    stubVenue({ put: () => HttpResponse.json(snapshot({ revision: REV_B })) });
    mount();
    await ready();
    await setPair('9007199254740991', '12');
    await saveWith('boundary');
    await waitFor(() => expect(puts()).toHaveLength(1));
    // The captured RAW body text — not a re-serialized object.
    expect(puts()[0].rawBody).toContain('"queue_workers":9007199254740991');
    expect(puts()[0].rawBody).not.toMatch(/e\+|9007199254740992/);
    expect(JSON.parse(puts()[0].rawBody).queue_workers).toBe(Number.MAX_SAFE_INTEGER);
  });

  test('15.1 the boundary+1 is refused at the editor: no PUT, and the entered TEXT is unchanged', async () => {
    stubVenue();
    mount();
    await ready();
    await setPair(UNSAFE, '12');
    await saveWith('too big');
    await screen.findByText(/outside the range this editor can represent exactly/);
    expect(workers()).toHaveValue(UNSAFE);
    expect(document.body).not.toHaveTextContent(String(ROUNDED));
    expect(puts()).toHaveLength(0);
  });

  const UNSAFE_RAW_PATHS: [string, string[]][] = [
    ['persisted_yaml.queue_workers', ['persisted_yaml', 'queue_workers']],
    ['persisted_yaml.host_global_session_cap', ['persisted_yaml', 'host_global_session_cap']],
    ['effective_admission_cap', ['effective_admission_cap']],
    ['producer_envelope', ['producer_envelope']],
    ['next_start.queue_workers', ['next_start', 'queue_workers']],
    ['next_start.host_global_session_cap', ['next_start', 'host_global_session_cap']],
    ['running_at_daemon_start.queue_workers', ['running_at_daemon_start', 'queue_workers']],
    ['running_at_daemon_start.host_global_session_cap', ['running_at_daemon_start', 'host_global_session_cap']],
  ];

  test.each(UNSAFE_RAW_PATHS)(
    '15.4 / 15.5 a raw GET with an unsafe token at %s is withheld at the REAL boundary',
    async (_label, path) => {
      stubVenue({
        get: (i) => (i === 0
          ? HttpResponse.json(snapshot())
          : rawWithUnsafeAt(path, { revision: REV_B })),
      });
      const view = mount();
      await ready();
      await setPair('5', '12');
      await userEvent.type(reasonBox(), 'why');
      await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));

      await screen.findByText(/outside the range this editor can represent exactly/);
      expect(document.body).not.toHaveTextContent(String(ROUNDED));
      // Last known is retained and labelled; the editor is untouched.
      expect(screen.getByText('Last known')).toBeVisible();
      expect(savedCell()).toHaveTextContent('3');
      expect(workers()).toHaveValue('5');
      expect(reasonBox()).toHaveValue('why');
      // The observation is NOT usable and no rebase is offered against the bad
      // read. A GET's raw body does reach the React Query cache, so "never
      // marked usable" is asserted through the SHARED classifier rather than by
      // pretending the cache revision is untouched.
      expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
      expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
      expect(
        classifySnapshot(view.client.getQueryData(capacityQueryKey(SLUG))).status,
      ).not.toBe('usable');
      expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
      expect(saveButton()).toBeDisabled();
      fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
      await waitFor(() => expect(view.client.isMutating()).toBe(0));
      expect(puts()).toHaveLength(0);
      view.unmount();
    },
  );

  test('R7 (F9) 15.5 a raw PUT SUCCESS carrying an unsafe token never enters accepted cache or base as usable', async () => {
    stubVenue({
      put: () => rawWithUnsafeAt(['effective_admission_cap'], { revision: REV_B }),
    });
    const view = mount();
    await ready();
    const acceptedBefore = view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG));
    expect(acceptedBefore?.revision).toBe(REV_A);
    const receiptBefore = capacityObservation(SLUG)?.receiptAt ?? null;

    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Save result unknown/);

    // The component says unknown AND the provider agrees: the response is not
    // an accepted observation, the receipt does not advance, and the capacity
    // CACHE still holds the last genuinely usable snapshot.
    expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
    expect(capacityObservation(SLUG)?.receiptAt ?? null).toBe(receiptBefore);
    expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
    const cached = view.client.getQueryData<Record<string, unknown>>(capacityQueryKey(SLUG));
    expect(cached?.revision).toBe(REV_A);
    expect(cached?.effective_admission_cap).toBe(10);
    expect(cached?.effective_admission_cap).not.toBe(ROUNDED);
    // Base did not advance and the rounded stand-in is nowhere on screen.
    expect(savedCell()).toHaveTextContent('3');
    expect(document.body).not.toHaveTextContent(String(ROUNDED));
    // Unresolved: the next Save is refused, not silently retried.
    await userEvent.click(saveButton());
    await screen.findByText(/Reconcile the saved values before saving again/);
    expect(puts()).toHaveLength(1);
  });

  test.each([
    ['persisted_yaml.queue_workers', ['detail', 'latest', 'persisted_yaml', 'queue_workers']],
    ['persisted_yaml.host_global_session_cap', ['detail', 'latest', 'persisted_yaml', 'host_global_session_cap']],
  ] as [string, string[]][])(
    '15.5 a raw 409 LATEST carrying an unsafe token at %s offers no rebase and fabricates no number',
    async (_label, path) => {
      captured = [];
      server.resetHandlers();
      const latestText = injectRaw(
        { detail: { code: 'stale_revision', latest: snapshot({ revision: REV_B }) } },
        path,
        UNSAFE,
      );
      expect(latestText).toContain(UNSAFE);
      stubVenue({
        put: () => new HttpResponse(latestText, {
          status: 409, headers: { 'content-type': 'application/json' },
        }),
      });
      const view = mount();
      await ready();
      await setPair('5', '12');
      await saveWith();
      await screen.findByText(/Latest saved values are outside the range this editor can represent exactly/);
      expect(document.body).not.toHaveTextContent(String(ROUNDED));
      expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
      expect(workers()).toHaveValue('5');
      expect(savedCell()).toHaveTextContent('3');
      expect(puts()).toHaveLength(1);
      view.unmount();
    },
  );

  test('15.5 a raw UNCERTAIN-REREAD carrying an unsafe token is not a usable observation', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : rawWithUnsafeAt(['producer_envelope'], { revision: REV_B })),
      put: () => HttpResponse.json(
        { detail: { code: 'config_publication_uncertain', artifact_state: 'absent' } },
        { status: 503 },
      ),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));
    expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(String(ROUNDED));
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(puts()).toHaveLength(1);
  });

  /**
   * 15.10 L4 — the DOMAIN and NULLABILITY matrix at the REAL boundary, one row
   * per documented field position (not a six-patch sample). Each row's
   * `expectUsable` records the documented outcome; the unrelated metadata stays
   * coherent so a DIFFERENT defect cannot accidentally satisfy the assertion.
   */
  const DOMAIN_RAW_CASES: [string, string[], string, boolean][] = [
    ['persisted_yaml.queue_workers null (documented nullable)', ['persisted_yaml', 'queue_workers'], 'null', true],
    ['persisted_yaml.host_global_session_cap null (documented nullable)', ['persisted_yaml', 'host_global_session_cap'], 'null', true],
    ['effective_admission_cap null (documented nullable)', ['effective_admission_cap'], 'null', true],
    // NULLABILITY — all TEN non-nullable positions reject a raw null. None may
    // be coerced to zero or accepted as a usable base.
    ['running_at_daemon_start.queue_workers null (non-nullable)', ['running_at_daemon_start', 'queue_workers'], 'null', false],
    ['running_at_daemon_start.host_global_session_cap null (non-nullable)', ['running_at_daemon_start', 'host_global_session_cap'], 'null', false],
    ['next_start.queue_workers null (non-nullable)', ['next_start', 'queue_workers'], 'null', false],
    ['next_start.host_global_session_cap null (non-nullable)', ['next_start', 'host_global_session_cap'], 'null', false],
    ['producer_envelope null (non-nullable)', ['producer_envelope'], 'null', false],
    ['producer_components.task_workers null (non-nullable)', ['producer_components', 'task_workers'], 'null', false],
    ['producer_components.thread_workers null (non-nullable)', ['producer_components', 'thread_workers'], 'null', false],
    ['producer_components.dream_workers null (non-nullable)', ['producer_components', 'dream_workers'], 'null', false],
    ['producer_components.wake_workers null (non-nullable)', ['producer_components', 'wake_workers'], 'null', false],
    ['producer_components.schedule_workers null (non-nullable)', ['producer_components', 'schedule_workers'], 'null', false],
    // DOMAIN — W and H strictly positive in every consumed position.
    ['persisted_yaml.queue_workers zero (positive domain)', ['persisted_yaml', 'queue_workers'], '0', false],
    ['persisted_yaml.host_global_session_cap negative (positive domain)', ['persisted_yaml', 'host_global_session_cap'], '-1', false],
    ['next_start.queue_workers zero (positive domain)', ['next_start', 'queue_workers'], '0', false],
    ['next_start.host_global_session_cap zero (positive domain)', ['next_start', 'host_global_session_cap'], '0', false],
    ['running_at_daemon_start.queue_workers zero (positive domain)', ['running_at_daemon_start', 'queue_workers'], '0', false],
    ['running_at_daemon_start.host_global_session_cap negative (positive domain)', ['running_at_daemon_start', 'host_global_session_cap'], '-1', false],
    // DOMAIN — envelope/components nonnegative; every component has its own
    // negative row, including wake_workers.
    ['producer_envelope negative (nonnegative domain)', ['producer_envelope'], '-1', false],
    ['producer_components.task_workers negative (nonnegative domain)', ['producer_components', 'task_workers'], '-1', false],
    ['producer_components.thread_workers negative (nonnegative domain)', ['producer_components', 'thread_workers'], '-1', false],
    ['producer_components.dream_workers negative (nonnegative domain)', ['producer_components', 'dream_workers'], '-1', false],
    ['producer_components.wake_workers negative (nonnegative domain)', ['producer_components', 'wake_workers'], '-1', false],
    ['producer_components.schedule_workers negative (nonnegative domain)', ['producer_components', 'schedule_workers'], '-1', false],
    // Nonnegative boundary that stays usable.
    ['producer_components.wake_workers zero (nonnegative boundary)', ['producer_components', 'wake_workers'], '0', true],
    // Envelope zero beside the default task_workers 3 is the task>envelope
    // INCONSISTENCY case, not a positive zero-envelope control. The coherent
    // zero control is a separate test below.
    ['producer_envelope zero with task_workers 3 (task>envelope -> inconsistent)', ['producer_envelope'], '0', false],
    ['persisted_yaml.queue_workers one (positive boundary)', ['persisted_yaml', 'queue_workers'], '1', true],
    ['next_start.host_global_session_cap one (positive boundary)', ['next_start', 'host_global_session_cap'], '1', true],
  ];

  test.each(DOMAIN_RAW_CASES)(
    '15.10 L4 raw domain/nullability: %s -> usable=%s',
    async (_label, path, token, expectUsable) => {
      stubVenue({
        get: (i) => (i === 0
          ? HttpResponse.json(snapshot())
          : new HttpResponse(
            injectRaw(snapshot({ revision: REV_B }), path, token),
            { headers: { 'content-type': 'application/json' } },
          )),
      });
      const view = mount();
      await ready();
      if (!expectUsable) {
        // A DIRTY editor makes "the bad read supplied a usable base" observable:
        // the draft must survive untouched and a real submit must be refused.
        await setPair('5', '12');
        await userEvent.type(reasonBox(), 'bad-read draft');
      }
      await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
      if (expectUsable) {
        await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('usable'));
        expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
        expect(screen.queryByText(/Cannot read capacity configuration/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Capacity details are inconsistent in this response/)).not.toBeInTheDocument();
      } else {
        await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('unusable'));
        expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
        expect(
          screen.getAllByRole('alert').some((node) =>
            /Cannot read capacity configuration|Capacity details are inconsistent in this response/
              .test(node.textContent ?? '')),
        ).toBe(true);
        const observedStatus = classifySnapshot(
          view.client.getQueryData(capacityQueryKey(SLUG)),
        ).status;
        expect(observedStatus).not.toBe('usable');
        // No usable base, no rounded/coerced form value and no reconciliation
        // target: the accepted base is still 3/10, the dirty draft is intact,
        // no rebase/accept control is offered and a real submit issues ZERO PUTs.
        expect(savedCell()).toHaveTextContent('3');
        expect(workers()).toHaveValue('5');
        expect(cap()).toHaveValue('12');
        expect(reasonBox()).toHaveValue('bad-read draft');
        if (observedStatus === 'inconsistent') {
          expect(document.body).toHaveTextContent(/Capacity details are inconsistent in this response/);
          expect(screen.getByText('Accepted base').parentElement?.textContent)
            .toMatch(/Task session slots 3/);
        } else {
          expect(screen.getByText('Last known')).toBeVisible();
        }
        expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Discard draft, accept latest' })).not.toBeInTheDocument();
        expect(saveButton()).toBeDisabled();
        fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
        await waitFor(() => expect(view.client.isMutating()).toBe(0));
        expect(puts()).toHaveLength(0);
      }
      view.unmount();
    },
  );

  test('15.10 L4 a COHERENT zero producer envelope with zero task workers is USABLE (positive control)', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_B,
          producer_envelope: 0,
          producer_components: {
            task_workers: 0, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
          },
        }))),
    });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(capacityObservation(SLUG)?.outcome).toBe('usable'));
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
    expect(screen.queryByText(/Cannot read capacity configuration/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Capacity details are inconsistent in this response/)).not.toBeInTheDocument();
  });

  test('15.10 L4 task_workers > producer_envelope is INCONSISTENT, never a negative total', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : new HttpResponse(
          injectRaw(snapshot({ revision: REV_B }), ['producer_components', 'task_workers'], '12'),
          { headers: { 'content-type': 'application/json' } },
        )),
    });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findAllByText(/Capacity details are inconsistent in this response/);
    expect(document.body).not.toHaveTextContent(/\b-2\b/);
    expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
  });
});

// ---------------------------------------------------------------------------
describe('R8 — an accepted write finishes a coherent clean state', () => {
  test('R8 (F10) an accepted PUT fences the obsolete during-PUT latest and leaves no residual lock', async () => {
    const gate = deferred<Response>();
    stubVenue({
      get: (i) => HttpResponse.json(i === 0 ? snapshot() : snapshot({
        revision: REV_C,
        persisted_yaml: { queue_workers: 9, host_global_session_cap: 9 },
        next_start: { queue_workers: 9, host_global_session_cap: 9 },
      })),
      put: () => gate.promise,
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    // A permitted read lands DURING the write and is recorded as `latest`.
    await view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await screen.findByText('Configuration changed elsewhere.');

    gate.resolve(HttpResponse.json(snapshot({
      revision: REV_B,
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
    })));
    await screen.findByText('Saved for next restart. Running limits are unchanged.');

    // The whole transition completes: the obsolete observation and every lock
    // it implied are gone, and the surface is clean.
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    expect(screen.queryByText('Currently saved')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('');
    expect(savedCell()).toHaveTextContent('5');
    expect(saveButton()).toBeEnabled();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);

    // A separate follow-up save is possible and uses the ACCEPTED revision.
    await setPair('6', '12');
    await saveWith('follow-up');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
  });

  test("R8 the write's OWN response is never an external change — no phantom reconciliation after a save", async () => {
    stubVenue({
      // The GET fixture keeps returning the PRE-SAVE revision, so any path that
      // treats the write's own published snapshot as a read observation shows
      // up immediately as a phantom "changed elsewhere".
      get: () => HttpResponse.json(snapshot()),
      put: () => HttpResponse.json(snapshot({
        revision: REV_B,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('raising task slots');
    await screen.findByText('Saved for next restart. Running limits are unchanged.');

    // The provider labels the observation as a WRITE, and the view must not
    // read it as an external change: the cache write inside the mutation's
    // onSuccess notifies this query's observer while the editor is still
    // dirty, which is exactly the ordering that manufactured a phantom
    // reconciliation panel in a real browser.
    expect(capacityObservation(SLUG)?.origin).toBe('write');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_B);
    expect(screen.queryByText('Configuration changed elsewhere.')).not.toBeInTheDocument();
    expect(screen.queryByText('Currently saved')).not.toBeInTheDocument();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(savedCell()).toHaveTextContent('5');
    expect(gets()).toHaveLength(1);
    expect(puts()).toHaveLength(1);
  });

  test('R8 a GENUINELY NEWER read settling AFTER the accepted write is still protected', async () => {
    stubVenue({
      get: (i) => HttpResponse.json(i === 0 ? snapshot() : snapshot({
        revision: REV_C,
        persisted_yaml: { queue_workers: 9, host_global_session_cap: 9 },
        next_start: { queue_workers: 9, host_global_session_cap: 9 },
      })),
      put: () => HttpResponse.json(snapshot({
        revision: REV_B,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Saved for next restart/);

    // A read issued AFTER the write settles is NOT obsolete: it publishes, and
    // because the form is clean it is adopted rather than discarded.
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(savedCell()).toHaveTextContent('9'));
    expect(workers()).toHaveValue('9');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);

    await setPair('4', '9');
    await saveWith('after newer read');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
  });
});

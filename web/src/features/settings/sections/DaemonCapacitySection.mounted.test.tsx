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
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { capacityObservation, capacityQueryKey } from '@/design-system/providers/_capacity-ordering';
import { server } from '@/test/server';
import { renderGuarded } from './capacityTestMount';

const SLUG = 'alpha';
const CAPACITY = `/api/v1/orgs/${SLUG}/settings/daemon-capacity`;
const REV_A = `sha256:${'a'.repeat(64)}`;
const REV_B = `sha256:${'b'.repeat(64)}`;
const REV_C = `sha256:${'c'.repeat(64)}`;

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
    stubVenue();
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
    await screen.findByText(/^Saved(\.| for next restart\.)/);
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
    const second = renderGuarded(<AppRoutes />, {
      client: view.client,
      entries: [`/orgs/${SLUG}/settings/daemon-capacity`],
      resetOrdering: false,
    });
    await waitFor(() => expect(gets().length).toBeGreaterThan(afterProviderAttempt));
    second.unmount();

    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
    expect(undeclared).toEqual([]);
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
  async function expectLaterRecovery(receiptAfterSave: number | null) {
    server.use(
      http.get(CAPACITY, async ({ request }) => {
        captured.push({ method: 'GET', ifMatch: request.headers.get('if-match'), rawBody: '' });
        return HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 5, host_global_session_cap: 12 },
          restart_pending: true, revision: REV_C,
        }));
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C));
    // Ordering safety was not bought by refusing later reads forever.
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect((capacityObservation(SLUG)?.receiptAt ?? 0)).toBeGreaterThanOrEqual(receiptAfterSave ?? 0);
    expect(capacityObservation(SLUG)?.receiptAt).not.toBe(receiptAfterSave);

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
    // Drain the final transition rather than leaving a response in flight.
    await waitFor(() => expect(savedCell()).toHaveTextContent('6'));
    expect(nextCell()).toHaveTextContent('6');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
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
    await expectLaterRecovery(receiptAfterSave);
  });

  test('2.16 recovery CONTINUES the 2.14 stale-success predecessor to a genuine receipt advance and a separate manual save', async () => {
    const { view, receiptAfterSave, renderedReceipt } = await duringPutOrdering(
      () => HttpResponse.json(snapshot({ revision: REV_A })),
    );
    await expectAcceptedPostSaveState(view, receiptAfterSave, renderedReceipt);
    await expectLaterRecovery(receiptAfterSave);
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
    await screen.findByText(/^Saved\. No restart is pending for these values\./);
    expect(document.body).toHaveTextContent(/Saved value overridden: Task session slots is set by the environment/);
    expect(document.body).toHaveTextContent(/Expected next start: Task session slots 3, Host session admission limit 14/);
    expect(document.body).not.toHaveTextContent(/Applied|Apply now|Restart daemon/);
    expect(savedCell()).toHaveTextContent('5');
    expect(nextCell()).toHaveTextContent('3');
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
    stubVenue({ get: (i) => HttpResponse.json(shadow(i === 0 ? 3 : 4)) });
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

    // No PUT until renewed confirmation.
    await user.click(saveButton());
    expect(puts()).toHaveLength(0);

    await user.click(screen.getByRole('checkbox'));
    await user.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_A}"`);
    const body = JSON.parse(puts()[0].rawBody);
    expect(body.confirm_environment_shadow).toBe(true);
    expect(body.queue_workers).toBe(5);
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

  /** A raw JSON body with ONE slot replaced by an unsafe integer token. */
  function rawWithUnsafe(slot: string, overrides: Record<string, unknown> = {}): Response {
    const replacement = slot.replace(/:\d+$/, `:${UNSAFE}`);
    const text = JSON.stringify(snapshot(overrides)).replace(slot, replacement);
    expect(text).toContain(`${UNSAFE}`);
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

  test('15.4 a raw GET carrying an unsafe token is withheld, never seeded and never rendered rounded', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : rawWithUnsafe('"queue_workers":3', { revision: REV_B })),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));

    await screen.findByText(/Latest capacity values are outside the range this editor can represent exactly/);
    expect(document.body).not.toHaveTextContent(String(ROUNDED));
    // Last known is retained and labelled; the editor is untouched.
    expect(screen.getByText('Last known')).toBeVisible();
    expect(savedCell()).toHaveTextContent('3');
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('why');
    // The observation is NOT usable and no rebase is offered against it.
    expect(capacityObservation(SLUG)?.outcome).toBe('unusable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBeNull();
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
    await waitFor(() => expect(view.client.isMutating()).toBe(0));
    expect(puts()).toHaveLength(0);
  });

  test('R7 (F9) 15.5 a raw PUT SUCCESS carrying an unsafe token never enters accepted cache or base as usable', async () => {
    stubVenue({
      put: () => rawWithUnsafe('"effective_admission_cap":10', { revision: REV_B }),
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

  test('15.5 a raw 409 LATEST carrying an unsafe token offers no rebase and fabricates no number', async () => {
    const latestText = JSON.stringify({
      detail: { code: 'stale_revision', latest: snapshot({ revision: REV_B }) },
    }).replace('"queue_workers":3', `"queue_workers":${UNSAFE}`);
    expect(latestText).toContain(UNSAFE);
    stubVenue({
      put: () => new HttpResponse(latestText, {
        status: 409, headers: { 'content-type': 'application/json' },
      }),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Latest saved values are outside the range this editor can represent exactly/);
    expect(document.body).not.toHaveTextContent(String(ROUNDED));
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(workers()).toHaveValue('5');
    expect(savedCell()).toHaveTextContent('3');
    expect(puts()).toHaveLength(1);
  });

  test('15.5 a raw UNCERTAIN-REREAD carrying an unsafe token is not a usable observation', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : rawWithUnsafe('"producer_envelope":10', { revision: REV_B })),
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

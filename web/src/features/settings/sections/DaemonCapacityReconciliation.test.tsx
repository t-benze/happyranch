/**
 * L4 / L5 — reconciliation, ordering and provenance, second batch.
 *
 * Same venue as `DaemonCapacitySection.mounted.test.tsx`: production component
 * + provider + React Query + `client.ts` + HTTP under a data router, with a
 * request-capturing fail-closed handler. Split out purely to keep each file
 * readable; the venue and its guarantees are identical.
 *
 * This file carries the conflict / uncertain / lost-response reconciliation
 * chains through to their FINAL separate manual save, and asserts that save's
 * actual `If-Match` header and raw request body rather than visible text.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

interface CapturedRequest { method: string; ifMatch: string | null; rawBody: string }

let captured: CapturedRequest[] = [];
let undeclared: string[] = [];
const gets = () => captured.filter((r) => r.method === 'GET');
const puts = () => captured.filter((r) => r.method === 'PUT');

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
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
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({
      counts: {}, recent_tasks: [], escalations: [], agents: [],
    })),
    http.get(`/api/v1/orgs/${SLUG}/health`, () => HttpResponse.json({ checks: [], status: 'ok' })),
    http.get(CAPACITY, async () => {
      captured.push({ method: 'GET', ifMatch: null, rawBody: '' });
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

const mount = (path = `/orgs/${SLUG}/settings/daemon-capacity`) =>
  renderGuarded(<AppRoutes />, { entries: [path] });

const workers = () => screen.getByLabelText(/Task session slots/);
const cap = () => screen.getByLabelText(/Host session admission limit/);
const reasonBox = () => screen.getByLabelText('Reason for change');
const saveButton = () => screen.getByRole('button', { name: /Save for next restart|Saving/ });
const rebaseButton = () => screen.getByRole('button', { name: /Keep my draft, rebase onto latest/ });
const acceptButton = () => screen.getByRole('button', { name: /Discard draft, accept latest/ });
const workersRow = () => within(screen.getByRole('table')).getAllByRole('row')[1] as HTMLTableRowElement;
const savedCell = () => workersRow().cells[2];

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

const conflict = (revision: string, w: number, h: number) => HttpResponse.json({
  detail: {
    code: 'stale_revision',
    latest: snapshot({ revision, persisted_yaml: { queue_workers: w, host_global_session_cap: h } }),
  },
}, { status: 409 });

const uncertain = () => HttpResponse.json(
  { detail: { code: 'config_publication_uncertain', artifact_state: 'absent' } }, { status: 503 },
);

beforeEach(() => { captured = []; undeclared = []; });

// ---------------------------------------------------------------------------
describe('2 — read ordering that no hook-mocked test can see', () => {
  test('2.4 a read ISSUED BEFORE the PUT but settling after it is dropped', async () => {
    const putGate = deferred<Response>();
    const getGate = deferred<Response>();
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : getGate.promise),
      put: () => putGate.promise,
    });
    const view = mount();
    await ready();

    // Issue the read FIRST, while no write is in flight…
    const early = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBe(2));

    // …then save. The PUT settles first.
    await setPair('5', '12');
    await saveWith();
    putGate.resolve(HttpResponse.json(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true, revision: REV_B,
    })));
    await screen.findByText(/Saved for next restart/);

    // The pre-save read now settles with the stale snapshot.
    getGate.resolve(HttpResponse.json(snapshot({ revision: REV_A })));
    await early;

    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(screen.getByText('Restart pending')).toBeVisible();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
  });

  test('2.5 Refresh is an OPERATOR path only: disabled during pending, re-enabled after', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    mount();
    await ready();
    const before = gets().length;
    await setPair('5', '12');
    await saveWith();

    const refresh = screen.getByRole('button', { name: /Refresh running state/ });
    await waitFor(() => expect(refresh).toBeDisabled());
    // Proved at the HTTP boundary too, not only by the attribute.
    expect(gets()).toHaveLength(before);

    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
    await waitFor(() => expect(screen.getByRole('button', { name: /Refresh running state/ })).toBeEnabled());
  });

  test('2.7 read/read: an older GET settling after a newer one is DROPPED, not merged', async () => {
    const firstGate = deferred<Response>();
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        if (i === 1) return firstGate.promise;                        // GET#1, slow
        return HttpResponse.json(snapshot({                            // GET#2, fast
          revision: REV_B,
          persisted_yaml: { queue_workers: 4, host_global_session_cap: 11 },
          next_start: { queue_workers: 4, host_global_session_cap: 11 },
        }));
      },
      // A COHERENT accepted response for the 6/13 submission: the same pair the
      // operator sent, a new revision, and restart_pending true because the
      // persisted/next-start pair genuinely differs from running 3/10.
      put: () => HttpResponse.json(snapshot({
        revision: REV_C,
        persisted_yaml: { queue_workers: 6, host_global_session_cap: 13 },
        next_start: { queue_workers: 6, host_global_session_cap: 13 },
        restart_pending: true,
      })),
    });
    const view = mount();
    await ready();

    // BOTH competing reads go through the PRODUCTION queryFn. Installing a raw
    // `fetch` queryFn via `fetchQuery` would replace the very ordering logic
    // under test and prove nothing about the shipped provider.
    const g1 = view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await waitFor(() => expect(gets()).toHaveLength(2));
    const g2 = view.client.refetchQueries({ queryKey: capacityQueryKey(SLUG) });
    await waitFor(() => expect(gets()).toHaveLength(3));
    await g2;
    // The OLDER read settles last, carrying the pre-competition snapshot.
    firstGate.resolve(HttpResponse.json(snapshot({ revision: REV_A })));
    await g1;

    // GET#1 is DROPPED, not merged: display, cache and provider observation
    // all hold GET#2's snapshot exactly.
    await waitFor(() => expect(savedCell()).toHaveTextContent('4'));
    expect(workers()).toHaveValue('4');
    expect(cap()).toHaveValue('11');
    expect(
      view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision,
    ).toBe(REV_B);
    expect(
      view.client.getQueryData<{ persisted_yaml: { queue_workers: number } }>(
        capacityQueryKey(SLUG),
      )?.persisted_yaml.queue_workers,
    ).toBe(4);

    // The manual save carries EXACTLY B, and settles coherently.
    await setPair('6', '13');
    await saveWith('after competing reads');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 6,
      host_global_session_cap: 13,
      rationale: 'after competing reads',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    // FINAL coherent settlement: the returned pair/revision is displayed and
    // cached, the draft/reason are clean, the lock is gone and the guard is
    // disarmed. Asserting only a banner would accept an incoherent body.
    await waitFor(() => expect(savedCell()).toHaveTextContent('6'));
    expect(workersRow().cells[3]).toHaveTextContent('6');
    expect(workers()).toHaveValue('6');
    expect(cap()).toHaveValue('13');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_C);
    expect(
      view.client.getQueryData<{ persisted_yaml: { queue_workers: number } }>(
        capacityQueryKey(SLUG),
      )?.persisted_yaml.queue_workers,
    ).toBe(6);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Reconcile the saved values/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('2.8 an obsolete FAILURE never downgrades a state a newer usable read recovered', async () => {
    const failGate = deferred<Response>();
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        if (i === 1) return failGate.promise;                     // old, fails LATE
        return HttpResponse.json(snapshot({ revision: REV_B }));  // newer, usable
      },
    });
    const view = mount();
    await ready();

    const old = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBe(2));
    const fresh = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBe(3));
    await fresh;

    failGate.resolve(new HttpResponse(null, { status: 500 }));
    await old.catch(() => undefined);

    // No error banner returns; the surface stays recovered.
    expect(screen.queryByText(/Could not load daemon capacity/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No values are displayed/)).not.toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
  });

  test('2.11 a post-conflict read is an observation only: base unchanged, write still blocked', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({ revision: REV_C, persisted_yaml: { queue_workers: 1, host_global_session_cap: 8 } }))),
      put: () => conflict(REV_B, 2, 9),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saved settings changed elsewhere. Your draft is preserved.');

    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    // base is untouched and no PUT may be issued without an explicit choice.
    const before = puts().length;
    await userEvent.click(saveButton());
    expect(puts()).toHaveLength(before);
    expect(await screen.findByText(/Reconcile the saved values before saving again/)).toBeInTheDocument();
    expect(workers()).toHaveValue('5');
  });
});

// ---------------------------------------------------------------------------
describe('3 / 4 — override coverage', () => {
  test('3.3 both keys shadowed: expected next start is the environment pair, both named', async () => {
    stubVenue({
      get: () => HttpResponse.json(snapshot({
        environment_shadowed: ['queue_workers', 'host_global_session_cap'],
        environment_warning: 'Environment overrides win.',
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
      })),
      put: () => HttpResponse.json(snapshot({
        environment_shadowed: ['queue_workers', 'host_global_session_cap'],
        environment_warning: 'Environment overrides win.',
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        revision: REV_B,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.click(screen.getByRole('checkbox'));
    await saveWith('both shadowed');

    await screen.findByText(/^Saved(\.| for next restart\.)/);
    expect(document.body).toHaveTextContent(/Saved value overridden/);
    // `capacityModel` sorts the shadowed keys by their raw key name, so
    // `host_global_session_cap` is named before `queue_workers`. Assert that
    // deterministic order, and that BOTH keys are named.
    expect(document.body).toHaveTextContent(/Host session admission limit and Task session slots are set by the environment/);
    expect(document.body).toHaveTextContent(/Expected next start: Task session slots 3, Host session admission limit 10/);
    expect(document.body).not.toHaveTextContent(/Applied/);
  });

  test('4.1c mirrored override: a changed resolved H at the same revision clears the ack', async () => {
    stubVenue({
      get: (i) => HttpResponse.json(snapshot({
        environment_shadowed: ['host_global_session_cap'],
        environment_warning: 'Environment overrides win.',
        next_start: { queue_workers: 3, host_global_session_cap: i === 0 ? 10 : 11 },
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    const user = userEvent.setup();
    await user.type(reasonBox(), 'mirrored');
    await user.click(screen.getByRole('checkbox'));

    await user.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('The environment override changed; confirm it again before saving.');
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('mirrored');
    expect(document.body).toHaveTextContent(/Host session admission limit 11/);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    // Accepted 4.1b/4.1c: Save is DISABLED until the override is confirmed again.
    expect(saveButton()).toBeDisabled();

    await user.click(saveButton());
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
    await screen.findByText('Confirm the environment override before saving.');
    expect(puts()).toHaveLength(0);

    await user.click(screen.getByRole('checkbox'));
    expect(saveButton()).toBeEnabled();
    await user.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    // The renewed confirmation, the full pair and the header are all asserted.
    expect(puts()[0].ifMatch).toBe(`"${REV_A}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'mirrored',
      confirm_environment_shadow: true,
    });
  });

  test('4.2 a GROWN shadowed key set clears the ack and preserves base/draft/reason', async () => {
    stubVenue({
      get: (i) => HttpResponse.json(snapshot({
        environment_shadowed: i === 0 ? ['queue_workers'] : ['queue_workers', 'host_global_session_cap'],
        environment_warning: 'Environment overrides win.',
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    const user = userEvent.setup();
    await user.type(reasonBox(), 'grown set');
    await user.click(screen.getByRole('checkbox'));

    await user.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('The environment override changed; confirm it again before saving.');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('grown set');
    expect(savedCell()).toHaveTextContent('3');
    // Both shadowed keys are named in the override panel.
    expect(document.body).toHaveTextContent(/Host session admission limit and Task session slots are set by the environment/);
    // Save is DISABLED, and the handler refuses too.
    expect(saveButton()).toBeDisabled();
    await user.click(saveButton());
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
    await screen.findByText('Confirm the environment override before saving.');
    expect(puts()).toHaveLength(0);

    // Renewed acknowledgment -> the complete request is observed at the wire.
    await user.click(screen.getByRole('checkbox'));
    expect(saveButton()).toBeEnabled();
    await user.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_A}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'grown set',
      confirm_environment_shadow: true,
    });
  });
});

// ---------------------------------------------------------------------------
describe('7 — dirty refresh and explicit reconciliation', () => {
  /**
   * Establish the changed-revision comparison state. `putSuccess` must return a
   * snapshot COHERENT with the pair each test submits — a fixture that returns
   * an unrelated pair (or leaves `restart_pending` false beside a changed
   * next-start) would let a wrong final state pass.
   */
  async function dirtyThenChangedRevision(putSuccess: () => Response) {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }))),
      put: putSuccess,
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'raising slots');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('Configuration changed elsewhere.');
  }

  const unusedPut = () => HttpResponse.json(snapshot({ revision: REV_C }));

  test('7.2 a changed-revision refresh shows a three-way comparison and NEVER advances base', async () => {
    await dirtyThenChangedRevision(unusedPut);
    expect(document.body).toHaveTextContent(/Accepted base/);
    expect(document.body).toHaveTextContent(/Your draft/);
    expect(document.body).toHaveTextContent(/Currently saved/);
    // Draft, reason and ack are intact; base did not move.
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('raising slots');
    expect(screen.getByText('Accepted base').parentElement?.textContent)
      .toMatch(/Task session slots 3/);
  });

  test('7.3 rebase keeps the draft verbatim and the manual save carries the LATEST revision', async () => {
    await dirtyThenChangedRevision(() => HttpResponse.json(snapshot({
      revision: REV_C,
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
    })));
    await userEvent.click(rebaseButton());
    expect(puts()).toHaveLength(0);          // reconciliation sends nothing
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('raising slots');

    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'raising slots',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_C);
    // The guard disarms once the save is accepted.
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  test('7.4 accept-latest resets the form and the subsequent save carries the latest revision', async () => {
    await dirtyThenChangedRevision(() => HttpResponse.json(snapshot({
      revision: REV_C,
      persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
      next_start: { queue_workers: 2, host_global_session_cap: 9 },
      restart_pending: true,
    })));
    await userEvent.click(acceptButton());
    expect(puts()).toHaveLength(0);
    await waitFor(() => expect(workers()).toHaveValue('2'));
    expect(cap()).toHaveValue('9');
    expect(reasonBox()).toHaveValue('');

    // Accepted 7.4: the subsequent PUT carries the ACCEPTED-LATEST pair 2/9
    // with a FRESH reason — not a newly invented pair.
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    await saveWith('fresh reason');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[0].rawBody)).toEqual({
      queue_workers: 2,
      host_global_session_cap: 9,
      rationale: 'fresh reason',
      confirm_environment_shadow: false,
    });
    // And it settles coherently with the SAME pair the operator sent.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('2'));
    expect(reasonBox()).toHaveValue('');
    expect(workers()).toHaveValue('2');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('7.5 doing nothing never auto-rebases on a further refresh', async () => {
    await dirtyThenChangedRevision(unusedPut);
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(2));
    expect(workers()).toHaveValue('5');
    expect(screen.getByText('Accepted base').parentElement?.textContent)
      .toMatch(/Task session slots 3/);
    const before = puts().length;
    await userEvent.click(saveButton());
    expect(puts()).toHaveLength(before);
  });
});

// ---------------------------------------------------------------------------
describe('8 / 14 — repeated conflict and unusable latest', () => {
  test('8.5 a SECOND 409 after a rebase reapplies conflict handling against the newer revision', async () => {
    stubVenue({
      put: (i) => (i < 2
        ? conflict(i === 0 ? REV_B : REV_C, i === 0 ? 2 : 1, i === 0 ? 9 : 8)
        : HttpResponse.json(snapshot({ revision: REV_A }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText('Saved settings changed elsewhere. Your draft is preserved.');

    await userEvent.click(rebaseButton());
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);

    // The second conflict reapplies the same handling against REV_C.
    await screen.findByText(/Task session slots 1, Host session admission limit 8/);
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('measured receipts');

    await userEvent.click(rebaseButton());
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(3));
    expect(puts()[2].ifMatch).toBe(`"${REV_C}"`);
  });

  test('14.2 a refresh missing producer_components is unusable wherever it appears', async () => {
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        const broken = snapshot() as Record<string, unknown>;
        delete broken.producer_components;
        return HttpResponse.json(broken);
      },
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));

    await screen.findByText(/Cannot read capacity configuration/);
    // No half-render: base, draft and reason all survive.
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('why');
    expect(saveButton()).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
describe('10 — typed publication-uncertain, carried to its final save', () => {
  test('10.1 the three artifact_state variants are distinct from the lost-response wording', async () => {
    for (const [state, expected] of [
      ['absent', /durability, verification, or cleanup did not complete/],
      ['present', /temporary artifact remains/],
      ['unknown', /artifact state is unknown/],
    ] as const) {
      captured = [];
      server.resetHandlers();
      stubVenue({
        put: () => HttpResponse.json(
          { detail: { code: 'config_publication_uncertain', artifact_state: state } }, { status: 503 },
        ),
      });
      const view = mount();
      await ready();
      await setPair('5', '12');
      await saveWith('uncertain variant');
      await screen.findByText(expected);
      // NOT interchangeable with the unknown-outcome copy of case 11.
      expect(document.body).not.toHaveTextContent(/Save result unknown/);
      view.unmount();
    }
  });

  test('10.5 a reread matching neither record names all three distinctly', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
        }))),
      put: () => uncertain(),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/The saved values still differ from what you submitted/);
    expect(document.body).toHaveTextContent(/You submitted Task session slots 5/);
    expect(screen.getByText('Accepted base').parentElement?.textContent).toMatch(/Task session slots 3/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 7/);
  });

  test('10.6 a failing Check leaves the outcome unresolved and advances nothing', async () => {
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : HttpResponse.error()),
      put: () => uncertain(),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));
    // Still unresolved, submission retained, nothing advanced.
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Check saved values' })).toBeInTheDocument();
    const before = puts().length;
    await userEvent.click(saveButton());
    expect(puts()).toHaveLength(before);
  });

  test('10.7 rebase branch: the lock clears, the submission is released, the save uses REV_C', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
        }))),
      // The accepted response is COHERENT with what was submitted: a fixture
      // that returns the ORIGINAL pair could hide a save that never applied.
      // `restart_pending: true` is required because the persisted/next-start
      // pair differs from running 3/10.
      put: (i) => (i === 0 ? uncertain() : HttpResponse.json(snapshot({
        revision: REV_D,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        restart_pending: true,
      }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(rebaseButton());
    // The explicit choice RELEASES the pinned submission and clears the lock.
    expect(screen.queryByText(/You submitted Task session slots 5/)).not.toBeInTheDocument();
    expect(screen.queryByText(/durability, verification, or cleanup did not complete/)).not.toBeInTheDocument();
    expect(screen.queryByText('Currently saved')).not.toBeInTheDocument();
    // The reconciliation itself sends NO request; the draft is kept verbatim.
    expect(puts()).toHaveLength(1);
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('measured receipts');

    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    // FINAL: the returned base is displayed and CACHED, the receipt names the
    // accepted write, the guard is disarmed and no residual lock or submission
    // remains.
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('10.8 accept-latest branch: clean at 7/14, then a NEW intentional edit saves 8/15 @ REV_C', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
        }))),
      put: (i) => (i === 0 ? uncertain() : HttpResponse.json(snapshot({
        revision: REV_D,
        persisted_yaml: { queue_workers: 8, host_global_session_cap: 15 },
        next_start: { queue_workers: 8, host_global_session_cap: 15 },
        restart_pending: true,
      }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(acceptButton());
    // EXPLICIT clean intermediate state: no request, no submission, no lock,
    // no guard — before the new intentional edit begins.
    expect(puts()).toHaveLength(1);
    await waitFor(() => expect(workers()).toHaveValue('7'));
    expect(cap()).toHaveValue('14');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/durability, verification, or cleanup did not complete/)).not.toBeInTheDocument();
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    await setPair('8', '15');
    await saveWith('new intent');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 8,
      host_global_session_cap: 15,
      rationale: 'new intent',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('8'));
    expect(workers()).toHaveValue('8');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('10.9 a newer draft after settlement is held independently from the pinned submission', async () => {
    stubVenue({ put: () => uncertain() });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await waitFor(() => expect(workers()).toBeEnabled());
    await setPair('7', '14');
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(document.body).toHaveTextContent(/current draft is Task session slots 7/);
    expect(document.body).toHaveTextContent(/held separately from the submitted values/);

    // R9 10.9: carry the NEWER draft through a usable latest, an explicit
    // rebase and a SEPARATE manual save to final settlement.
    server.use(
      http.get(CAPACITY, async () => {
        captured.push({ method: 'GET', ifMatch: null, rawBody: '' });
        return HttpResponse.json(snapshot({
          revision: REV_C, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
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
          persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
          next_start: { queue_workers: 7, host_global_session_cap: 14 },
          restart_pending: true,
        }));
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);
    expect(screen.getByText('Your draft').parentElement?.textContent).toMatch(/Task session slots 7/);

    await userEvent.click(rebaseButton());
    expect(workers()).toHaveValue('7');
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    // The NEWER draft against the LATEST revision — never the pinned 5/12.
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 7,
      host_global_session_cap: 14,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('7'));
    expect(workers()).toHaveValue('7');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });
});

// ---------------------------------------------------------------------------
describe('11 — lost response: every reread relation is named accurately', () => {
  async function lostThen(reread: Record<string, unknown>, newerDraft = false) {
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : HttpResponse.json(snapshot(reread))),
      put: () => HttpResponse.error(),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);
    if (newerDraft) {
      await waitFor(() => expect(workers()).toBeEnabled());
      await setPair('7', '14');
    }
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
  }

  test('11.3 a reread matching the SUBMITTED pair names the submission, not a success', async () => {
    await lostThen({ persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 }, revision: REV_B }, true);
    await screen.findByText(/Saved values now match what you submitted \(5 \/ 12\)/);
    expect(document.body).toHaveTextContent(/does not confirm your request caused it/i);
    // The newer draft is still shown as unsaved and no PUT was issued.
    expect(workers()).toHaveValue('7');
    expect(puts()).toHaveLength(1);
  });

  test('11.4 a reread matching the NEWER DRAFT states BOTH relations and never collapses into "saved"', async () => {
    await lostThen({ persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 }, revision: REV_B }, true);
    // R9 11.4: the required relation is an explicit MATCH to the current draft,
    // stated separately from the difference against the submission. Asserting
    // only "differs from what you submitted" left the draft relation unnamed.
    await screen.findByText(/Saved values now match your current draft \(7 \/ 14\)/);
    expect(document.body).toHaveTextContent(/they differ from what you submitted \(5 \/ 12\)/);
    expect(document.body).toHaveTextContent(
      /Matching your draft is not a saved result and does not confirm your request caused it/,
    );
    // Never a causal save claim, in any wording.
    expect(document.body).not.toHaveTextContent(/your save succeeded|save was applied|save completed/i);
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 7/);
    expect(screen.getByText('Your draft').parentElement?.textContent).toMatch(/Task session slots 7/);
    expect(screen.getByText('Accepted base').parentElement?.textContent).toMatch(/Task session slots 3/);
    // Still unresolved: no automatic PUT, guard still armed.
    expect(puts()).toHaveLength(1);
    expect(document.body).toHaveTextContent(/Save result unknown/);
  });

  test('11.5 a reread matching NEITHER yields a three-way comparison', async () => {
    await lostThen({ persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 }, revision: REV_B });
    await screen.findByText(/The saved values still differ from what you submitted/);
    expect(screen.getByText('Accepted base').parentElement?.textContent).toMatch(/Task session slots 3/);
    expect(screen.getByText('Your draft').parentElement?.textContent).toMatch(/Task session slots 5/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 2/);
  });

  test('11.6 an UNCHANGED reread is still not "your save failed"', async () => {
    await lostThen({});
    await screen.findByText(/The saved values are unchanged from your accepted base/);
    expect(document.body).toHaveTextContent(/outcome of your request is still unknown/i);
    expect(document.body).not.toHaveTextContent(/your save failed|failed safely/i);
  });

  test('11.7 an ABSENT-key reread is not equality with anything', async () => {
    await lostThen({ persisted_yaml: { queue_workers: null, host_global_session_cap: null }, revision: REV_B });
    await screen.findByText(/The saved values are Not set in file\. That is not equality with any of the values below\./);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Not set in file/);
  });

  test('11.9 rebase branch: the write lock clears and the retained draft is saved against REV_B', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({ revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 } }))),
      put: (i) => (i === 0 ? HttpResponse.error() : HttpResponse.json(snapshot({
        revision: REV_D,
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        restart_pending: true,
      }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(rebaseButton());
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Save result unknown/)).not.toBeInTheDocument();
    expect(puts()).toHaveLength(1);
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(workers()).toHaveValue('5');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('11.10 accept-latest branch: a clean intermediate state, then a new edit saves against REV_B', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({ revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 } }))),
      put: (i) => (i === 0 ? HttpResponse.error() : HttpResponse.json(snapshot({
        revision: REV_D,
        persisted_yaml: { queue_workers: 6, host_global_session_cap: 13 },
        next_start: { queue_workers: 6, host_global_session_cap: 13 },
        restart_pending: true,
      }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(acceptButton());
    await waitFor(() => expect(workers()).toHaveValue('2'));
    // Explicit clean intermediate state.
    expect(cap()).toHaveValue('9');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Save result unknown/)).not.toBeInTheDocument();
    expect(puts()).toHaveLength(1);
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    await setPair('6', '13');
    await saveWith('new pair');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 6,
      host_global_session_cap: 13,
      rationale: 'new pair',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved for next restart\. Running limits are unchanged\./);
    await waitFor(() => expect(savedCell()).toHaveTextContent('6'));
    expect(workers()).toHaveValue('6');
    expect(reasonBox()).toHaveValue('');
    expect(view.client.getQueryData<{ revision: string }>(capacityQueryKey(SLUG))?.revision).toBe(REV_D);
    expect(capacityObservation(SLUG)?.outcome).toBe('usable');
    expect(capacityObservation(SLUG)?.sourceRevision).toBe(REV_D);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    const unload2 = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload2);
    expect(unload2.defaultPrevented).toBe(false);
  });

  test('11.11 absent-key reread beside a pinned submission names three records distinctly', async () => {
    await lostThen({ persisted_yaml: { queue_workers: null, host_global_session_cap: null }, revision: REV_B }, true);
    await screen.findByText(/That is not equality with any of the values below/);
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(screen.getByText('Your draft').parentElement?.textContent).toMatch(/Task session slots 7/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Not set in file/);
    expect(puts()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
describe('18 — external-restart reads make no causal claim', () => {
  test('18.4 a changed startup pair reports the new observation, never that a restart occurred', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_B,
          running_at_daemon_start: { queue_workers: 5, host_global_session_cap: 12 },
        }))),
    });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(workersRow().cells[1]).toHaveTextContent('5'));

    const body = document.body.textContent ?? '';
    expect(body).not.toMatch(/restart (occurred|succeeded|completed)/i);
    expect(body).not.toMatch(/\bRestart daemon\b/);
    // The control is a refresh, never a restart.
    expect(screen.getByRole('button', { name: /Refresh running state/ })).toBeInTheDocument();
  });

  test('18.5 an unchanged startup pair equal to the saved pair states equality with no causal claim', async () => {
    stubVenue({ get: () => HttpResponse.json(snapshot()) });
    mount();
    await ready();
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    // Accepted 18.5 requires the equality STATEMENT itself, not merely the
    // existing "No restart pending" badge.
    expect(screen.getByText(/Running configuration matches the expected values/)).toBeVisible();
    expect(document.body).toHaveTextContent(
      /it does not mean a restart happened or that anything on this page caused it/,
    );
    expect(screen.getByText('No restart pending')).toBeVisible();
    expect(document.body.textContent ?? '').not.toMatch(/restart (occurred|succeeded|completed)/i);
    expect(document.body.textContent ?? '').not.toMatch(/\bRestart daemon\b/);
  });

  test('18.5b a DIFFERING startup pair makes no equality statement', async () => {
    stubVenue({
      get: () => HttpResponse.json(snapshot({
        running_at_daemon_start: { queue_workers: 4, host_global_session_cap: 11 },
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
        restart_pending: true,
      })),
    });
    mount();
    await ready();
    expect(screen.queryByText(/Running configuration matches the expected values/)).not.toBeInTheDocument();
    expect(screen.getByText('Restart pending')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
describe('16 / 17 — focus and navigation (L5)', () => {
  test('16.7 focus moves deterministically after a reconciliation choice', async () => {
    stubVenue({ put: () => conflict(REV_B, 2, 9) });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText('Saved settings changed elsewhere. Your draft is preserved.');

    await userEvent.click(rebaseButton());
    await waitFor(() => expect(document.activeElement).not.toBe(document.body));
    expect(document.activeElement).toHaveAttribute('tabindex', '-1');
  });

  test('16.7b after Stay on page, focus returns to the control that had it', async () => {
    stubVenue();
    mount();
    await ready();
    await userEvent.type(reasonBox(), 'unsaved');

    const content = screen.getByTestId('settings-content');
    const link = within(content).getByRole('link', { name: 'Organization' });
    link.focus();
    await userEvent.click(link);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));

    await waitFor(() => expect(document.activeElement).toBe(link));
  });

  test('17.3 browser Back with a dirty draft: Stay keeps every field BYTE-IDENTICAL, and a confirmed departure completes', async () => {
    stubVenue({
      get: () => HttpResponse.json(snapshot({
        environment_shadowed: ['queue_workers'],
        environment_warning: 'Environment overrides win.',
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
      })),
    });
    const view = renderGuarded(<AppRoutes />, {
      entries: [`/orgs/${SLUG}/settings/organization`, `/orgs/${SLUG}/settings/daemon-capacity`],
      index: 1,
    });
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'unsaved');
    await userEvent.click(screen.getByRole('checkbox'));

    // (a) STAY — every retained field is byte-identical and the ack survives.
    view.router.navigate(-1);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));
    await waitFor(() => expect(view.router.state.location.pathname)
      .toBe(`/orgs/${SLUG}/settings/daemon-capacity`));
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('unsaved');
    expect(screen.getByRole('checkbox')).toBeChecked();
    expect(puts()).toHaveLength(0);

    // (b) CONFIRMED DEPARTURE — the navigation actually completes.
    view.router.navigate(-1);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    await waitFor(() => expect(view.router.state.location.pathname)
      .toBe(`/orgs/${SLUG}/settings/organization`));
    expect(screen.queryByLabelText('Reason for change')).not.toBeInTheDocument();
    expect(puts()).toHaveLength(0);
  });

  test('17.4 navigating to Runtime Health is intercepted identically and implies no new panel', async () => {
    stubVenue();
    const view = mount();
    await ready();
    await userEvent.type(reasonBox(), 'unsaved');

    view.router.navigate(`/orgs/${SLUG}/health`);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));
    await waitFor(() => expect(view.router.state.location.pathname)
      .toBe(`/orgs/${SLUG}/settings/daemon-capacity`));
    // Still on capacity, and no admission/residue panel was implied.
    expect(reasonBox()).toHaveValue('unsaved');
    expect(document.body).not.toHaveTextContent(/admission diagnostics|residue/i);

    // CONFIRMED DEPARTURE reaches the ordinary Runtime Health page, which is
    // an existing general-diagnostics route — no new capacity panel there.
    view.router.navigate(`/orgs/${SLUG}/health`);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    await waitFor(() => expect(view.router.state.location.pathname).toBe(`/orgs/${SLUG}/health`));
    expect(screen.queryByLabelText('Reason for change')).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/admission diagnostics|residue|provider ceiling/i);
    expect(puts()).toHaveLength(0);
  });

  test('17.6 rationale-only dirty AND an unresolved unknown outcome both arm the guard', async () => {
    stubVenue({ put: () => HttpResponse.error() });
    const view = mount();
    await ready();

    // Rationale-only dirty.
    await userEvent.type(reasonBox(), 'only the reason');
    view.router.navigate(`/orgs/${SLUG}/settings/organization`);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));
    await waitFor(() => expect(screen.queryByText('Discard unsaved capacity changes?')).not.toBeInTheDocument());

    // Unresolved unknown outcome, with the values back at base.
    await setPair('5', '12');
    await userEvent.click(saveButton());
    await screen.findByText(/Save result unknown/);
    await setPair('3', '10');
    view.router.navigate(`/orgs/${SLUG}/settings/organization`);
    await screen.findByText('Discard unsaved capacity changes?');
  });
});

// ---------------------------------------------------------------------------
/**
 * Independent-review regressions R1-R8.
 *
 * Each case below reproduces a scenario the independent reviewer's scratch
 * probes (F1-F10) drove through this exact production boundary, and asserts the
 * ACCEPTED final behaviour — not the broken observation the probe recorded.
 */
describe('R1-R8 — independent-review boundary regressions', () => {
  test('R2 (F2) repeated REFUSED Save clicks never unlock a second PUT, and the unresolved banner survives every refusal', async () => {
    stubVenue({ put: () => HttpResponse.error() });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Save result unknown/);
    expect(puts()).toHaveLength(1);

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await userEvent.click(saveButton());
      await screen.findByText(/Reconcile the saved values before saving again/);
      // The refusal appears BESIDE the unresolved state, never instead of it:
      // a banner change is not an outcome and must not clear the write lock.
      expect(screen.getByText(/Save result unknown/)).toBeVisible();
      await waitFor(() => expect(view.client.isMutating()).toBe(0));
      expect(puts()).toHaveLength(1);
    }
    // The pinned submission is still the immutable record of what was SENT.
    expect(screen.getByText(/You submitted Task session slots 5, Host session admission limit 12/)).toBeVisible();
  });

  test('R2 (F5) an ordinary Discard resets the EDITOR only; the unresolved publication, guard and lock all survive until an explicit reconciliation', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }))),
      put: (i) => (i === 0 ? HttpResponse.error() : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);

    await userEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    // The editor IS reset.
    expect(workers()).toHaveValue('3');
    expect(cap()).toHaveValue('10');
    expect(reasonBox()).toHaveValue('');
    // The publication outcome is NOT resolved by resetting fields.
    expect(screen.getByText(/Save result unknown/)).toBeVisible();
    expect(screen.getByText(/You submitted Task session slots 5, Host session admission limit 12/)).toBeVisible();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    await userEvent.click(saveButton());
    await screen.findByText(/Reconcile the saved values before saving again/);
    await waitFor(() => expect(view.client.isMutating()).toBe(0));
    expect(puts()).toHaveLength(1);

    // Only an explicit reconciliation clears it — and it sends no request.
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/The saved values still differ from what you submitted/);
    expect(puts()).toHaveLength(1);
    await userEvent.click(acceptButton());
    await waitFor(() => expect(workers()).toHaveValue('2'));
    expect(screen.queryByText(/Save result unknown/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(puts()).toHaveLength(1);

    // A separate, independent manual save now works and carries REV_B.
    await setPair('4', '11');
    await saveWith('after reconciliation');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 4,
      host_global_session_cap: 11,
      rationale: 'after reconciliation',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved\./);
  });

  test('R3 (F3) a FAILED Check never promotes cached values into a reconciliation target, and a later usable reread does', async () => {
    stubVenue({
      get: (i) => {
        if (i === 0) return HttpResponse.json(snapshot());
        if (i === 1) return HttpResponse.error();
        return HttpResponse.json(snapshot({
          revision: REV_B, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
        }));
      },
      put: (i) => (i === 0 ? uncertain() : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await waitFor(() => expect(
      view.client.getQueryState(capacityQueryKey(SLUG))?.status,
    ).toBe('error'));

    // `refetch()` resolved with the PREVIOUS cached data beside an error. That
    // is not a successful reread: nothing may be offered for reconciliation.
    expect(screen.queryByRole('button', { name: 'Keep my draft, rebase onto latest' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Discard draft, accept latest' })).not.toBeInTheDocument();
    await screen.findByText(/Could not refresh\. Current state unverified\./);
    // Still unresolved, nothing advanced, retry offered.
    expect(screen.getByText(/durability, verification, or cleanup did not complete/)).toBeVisible();
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(savedCell()).toHaveTextContent('3');
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement);
    await waitFor(() => expect(view.client.isMutating()).toBe(0));
    expect(puts()).toHaveLength(1);

    // A genuinely successful, usable reread DOES become the reconciliation
    // target, and only then can a write be built.
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/The saved values still differ from what you submitted/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 7/);
    await userEvent.click(rebaseButton());
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
  });

  test('R4 (F6) a NEWER successful check SUPERSEDES an older conflict snapshot — rebase branch', async () => {
    stubVenue({
      get: (i) => HttpResponse.json(i === 0 ? snapshot() : snapshot({
        revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
      })),
      put: (i) => (i === 0
        ? conflict(REV_B, 2, 9)
        : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Saved settings changed elsewhere/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 2/);

    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);
    // The newer observation replaces the stale 409 body by OBSERVATION ORDER,
    // not by origin slot: a real server would reject REV_B's If-Match again.
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 7/);
    expect(screen.getByText('Currently saved').parentElement?.textContent).not.toMatch(/Task session slots 2/);

    await userEvent.click(rebaseButton());
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    await screen.findByText(/^Saved\./);
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
  });

  test('R4 (F6) the same supersession holds for DISCARD-AND-ACCEPT-LATEST', async () => {
    stubVenue({
      get: (i) => HttpResponse.json(i === 0 ? snapshot() : snapshot({
        revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
      })),
      put: (i) => (i === 0
        ? conflict(REV_B, 2, 9)
        : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Saved settings changed elsewhere/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(acceptButton());
    // Accept-latest must restore the NEWEST observation, never the obsolete B.
    await waitFor(() => expect(workers()).toHaveValue('7'));
    expect(cap()).toHaveValue('14');
    expect(reasonBox()).toHaveValue('');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    expect(puts()).toHaveLength(1);

    await setPair('8', '15');
    await saveWith('fresh reason');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody)).toEqual({
      queue_workers: 8,
      host_global_session_cap: 15,
      rationale: 'fresh reason',
      confirm_environment_shadow: false,
    });
  });

  test.each([
    ['an unsafe integer', '9007199254740993'],
    ['non-numeric text', 'abc'],
    ['a blank field', ''],
  ])('R5 (F4) %s with NO rationale is still unsaved work across unload, in-app navigation and a changed-revision read', async (_label, text) => {
    stubVenue({
      get: (i) => HttpResponse.json(i === 0 ? snapshot() : snapshot({
        revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
      })),
    });
    const view = mount();
    await ready();
    const user = userEvent.setup();
    await user.clear(workers());
    if (text.length > 0) await user.type(workers(), text);
    expect(workers()).toHaveValue(text);
    expect(reasonBox()).toHaveValue('');

    // 17.1 — the reload warning is installed for raw unsaved text.
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    expect(document.body).toHaveTextContent(/Unsaved changes/);

    // 14.x — a changed-revision read must NOT treat the editor as clean and
    // overwrite the exact text the operator entered.
    await user.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('Configuration changed elsewhere.');
    expect(workers()).toHaveValue(text);

    // 17.2 — an in-app route change is intercepted, and Stay keeps the text.
    act(() => { void view.router.navigate(`/orgs/${SLUG}/settings`); });
    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Stay on page' }));
    // Radix dialog teardown is flaky to assert by DOM removal under jsdom, so
    // the BEHAVIOURAL contract is asserted instead: still on the capacity
    // route, with the exact entered text byte-identical.
    await waitFor(() => expect(view.router.state.location.pathname)
      .toBe(`/orgs/${SLUG}/settings/daemon-capacity`));
    expect(workers()).toHaveValue(text);

    // An explicit correction/discard is the only thing that clears it.
    await user.click(screen.getByRole('button', { name: 'Discard draft' }));
    expect(workers()).toHaveValue('3');
  });
});

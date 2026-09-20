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
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
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
    });
    const view = mount();
    await ready();

    const slow = view.client.fetchQuery({
      queryKey: ['daemon-capacity', SLUG],
      queryFn: () => fetch(`/api/v1${CAPACITY.replace('/api/v1', '')}`).then((r) => r.json()),
    }).catch(() => undefined);
    void slow;

    // Drive two reads through the provider, newest settling first.
    const g1 = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBeGreaterThanOrEqual(2));
    const g2 = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBeGreaterThanOrEqual(3));
    await g2;
    firstGate.resolve(HttpResponse.json(snapshot({ revision: REV_A })));
    await g1;

    // The NEWER read's revision is what a later manual save uses.
    await setPair('6', '13');
    await saveWith('after competing reads');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).not.toBe(`"${REV_A}"`);
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

    await user.click(saveButton());
    expect(puts()).toHaveLength(0);
    await user.click(screen.getByRole('checkbox'));
    await user.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0].rawBody).confirm_environment_shadow).toBe(true);
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
    await user.click(saveButton());
    expect(puts()).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
describe('7 — dirty refresh and explicit reconciliation', () => {
  async function dirtyThenChangedRevision() {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }))),
      put: () => HttpResponse.json(snapshot({ revision: REV_C })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'raising slots');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await screen.findByText('Configuration changed elsewhere.');
  }

  test('7.2 a changed-revision refresh shows a three-way comparison and NEVER advances base', async () => {
    await dirtyThenChangedRevision();
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
    await dirtyThenChangedRevision();
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
    await screen.findByText(/^Saved(\.| for next restart\.)/);
    // The guard disarms once the save is accepted.
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  test('7.4 accept-latest resets the form and the subsequent save carries the latest revision', async () => {
    await dirtyThenChangedRevision();
    await userEvent.click(acceptButton());
    expect(puts()).toHaveLength(0);
    await waitFor(() => expect(workers()).toHaveValue('2'));
    expect(cap()).toHaveValue('9');
    expect(reasonBox()).toHaveValue('');

    await setPair('4', '11');
    await saveWith('fresh reason');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[0].rawBody).queue_workers).toBe(4);
  });

  test('7.5 doing nothing never auto-rebases on a further refresh', async () => {
    await dirtyThenChangedRevision();
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
      put: (i) => (i === 0 ? uncertain() : HttpResponse.json(snapshot({ revision: REV_A }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(rebaseButton());
    expect(screen.queryByText(/You submitted Task session slots 5/)).not.toBeInTheDocument();

    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody).queue_workers).toBe(5);
    await screen.findByText(/^Saved(\.| for next restart\.)/);
  });

  test('10.8 accept-latest branch: clean at 7/14, then a NEW intentional edit saves 8/15 @ REV_C', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          revision: REV_C, persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 },
        }))),
      put: (i) => (i === 0 ? uncertain() : HttpResponse.json(snapshot({ revision: REV_A }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(acceptButton());
    expect(puts()).toHaveLength(1);
    await waitFor(() => expect(workers()).toHaveValue('7'));
    expect(cap()).toHaveValue('14');
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();

    await setPair('8', '15');
    await saveWith('new intent');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
    expect(JSON.parse(puts()[1].rawBody).queue_workers).toBe(8);
    expect(JSON.parse(puts()[1].rawBody).rationale).toBe('new intent');
  });

  test('10.9 a newer draft after settlement is held independently from the pinned submission', async () => {
    stubVenue({ put: () => uncertain() });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/durability, verification, or cleanup did not complete/);

    await waitFor(() => expect(workers()).toBeEnabled());
    await setPair('7', '14');
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(document.body).toHaveTextContent(/current draft is Task session slots 7/);
    expect(document.body).toHaveTextContent(/held separately from the submitted values/);
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

  test('11.4 a reread matching the NEWER DRAFT does not collapse into "saved"', async () => {
    await lostThen({ persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 }, revision: REV_B }, true);
    await screen.findByText(/The saved values still differ from what you submitted/);
    expect(screen.getByText(/You submitted Task session slots 5/)).toBeVisible();
    expect(screen.getByText('Currently saved').parentElement?.textContent).toMatch(/Task session slots 7/);
    expect(document.body).not.toHaveTextContent(/your save succeeded/i);
    expect(puts()).toHaveLength(1);
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
      put: (i) => (i === 0 ? HttpResponse.error() : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(rebaseButton());
    expect(screen.queryByText(/You submitted/)).not.toBeInTheDocument();
    await userEvent.click(saveButton());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody).queue_workers).toBe(5);
  });

  test('11.10 accept-latest branch: a clean intermediate state, then a new edit saves against REV_B', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({ revision: REV_B, persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 } }))),
      put: (i) => (i === 0 ? HttpResponse.error() : HttpResponse.json(snapshot({ revision: REV_C }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith('measured receipts');
    await screen.findByText(/Save result unknown/);
    await userEvent.click(screen.getByRole('button', { name: 'Check saved values' }));
    await screen.findByText(/still differ from what you submitted/);

    await userEvent.click(acceptButton());
    await waitFor(() => expect(workers()).toHaveValue('2'));
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();

    await setPair('6', '13');
    await saveWith('new pair');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_B}"`);
    expect(JSON.parse(puts()[1].rawBody).queue_workers).toBe(6);
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

    // running == saved == next, so the badge is the honest equality statement.
    expect(screen.getByText('No restart pending')).toBeVisible();
    expect(document.body.textContent ?? '').not.toMatch(/restart (occurred|succeeded)/i);
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

  test('17.3 browser Back with a dirty draft is intercepted the same way', async () => {
    stubVenue();
    const view = renderGuarded(<AppRoutes />, {
      entries: [`/orgs/${SLUG}/settings/organization`, `/orgs/${SLUG}/settings/daemon-capacity`],
      index: 1,
    });
    await ready();
    await userEvent.type(reasonBox(), 'unsaved');

    view.router.navigate(-1);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));
    await waitFor(() => expect(screen.queryByText('Discard unsaved capacity changes?')).not.toBeInTheDocument());
    expect(reasonBox()).toHaveValue('unsaved');
  });

  test('17.4 navigating to Runtime Health is intercepted identically and implies no new panel', async () => {
    stubVenue();
    const view = mount();
    await ready();
    await userEvent.type(reasonBox(), 'unsaved');

    view.router.navigate(`/orgs/${SLUG}/health`);
    await screen.findByText('Discard unsaved capacity changes?');
    await userEvent.click(screen.getByRole('button', { name: 'Stay on page' }));
    await waitFor(() => expect(screen.queryByText('Discard unsaved capacity changes?')).not.toBeInTheDocument());
    // Still on capacity, and no admission/residue panel was implied.
    expect(reasonBox()).toHaveValue('unsaved');
    expect(document.body).not.toHaveTextContent(/admission diagnostics|residue/i);
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

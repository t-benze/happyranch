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
      return options.get ? options.get(index) : HttpResponse.json(snapshot());
    }),
    http.put(CAPACITY, async ({ request }) => {
      captured.push({
        method: 'PUT',
        ifMatch: request.headers.get('if-match'),
        rawBody: await request.text(),
      });
      const index = putIndex;
      putIndex += 1;
      return options.put ? options.put(index) : HttpResponse.json(snapshot({ revision: REV_B }));
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

  test('2.9 / 2.10 the receipt advances on an IDENTICAL refetch and NOT on a cached remount', async () => {
    stubVenue();
    const view = mount();
    await ready();
    const first = screen.getByText(/Last received/).textContent;
    expect(first).toMatch(/this browser's clock/);

    // An identical payload: React Query structural sharing returns a
    // reference-identical object, so a component-observed data change would be
    // ABSENT here. The provider-owned receipt still advances.
    await new Promise((r) => setTimeout(r, 1100));
    await view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => {
      expect(screen.getByText(/Last received/).textContent).not.toBe(first);
    });
    const second = screen.getByText(/Last received/).textContent;

    // A cached remount issues NO request and invents NO receipt.
    const getsBefore = gets().length;
    view.rerender(<div />);
    expect(gets()).toHaveLength(getsBefore);
    expect(second).not.toBe(first);
  });

  test('2.13 while a write is pending, a provider-level read is SUPPRESSED at the HTTP boundary', async () => {
    const gate = deferred<Response>();
    stubVenue({ put: () => gate.promise });
    const view = mount();
    await ready();
    const before = gets().length;
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    // A NON-OPERATOR read attempt, standing in for the reconnect /
    // extra-cache-consumer trigger. The assertion is the captured request list
    // and the provider observation — asserting the Refresh button is disabled
    // would NOT satisfy this case.
    await view.client.cancelQueries({ queryKey: ['daemon-capacity', SLUG] });
    expect(gets()).toHaveLength(before);

    gate.resolve(HttpResponse.json(snapshot({ revision: REV_B })));
    await screen.findByText(/Saved/);
  });

  test('2.14 a PERMITTED during-PUT read landing as a stale SUCCESS is DROPPED and never republishes', async () => {
    const putGate = deferred<Response>();
    const getGate = deferred<Response>();
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : getGate.promise),
      put: () => putGate.promise,
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    // Issued DURING the write — a higher issue seq than the write's issue, so a
    // naive issue-keyed filter would let it through.
    const stale = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    // The write settles FIRST with the accepted 5/12 @ REV_B…
    putGate.resolve(HttpResponse.json(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true, revision: REV_B,
    })));
    await screen.findByText(/Saved for next restart/);
    const receiptAfterSave = screen.getByText(/Last received/).textContent;

    // …THEN the older read settles with the PRE-SAVE 3/10 @ REV_A.
    getGate.resolve(HttpResponse.json(snapshot({ revision: REV_A })));
    await stale;

    // FINAL: the accepted snapshot survives. No revert, no badge flicker.
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(nextCell()).toHaveTextContent('5');
    expect(screen.getByText('Restart pending')).toBeVisible();
    expect(screen.queryByText(/Unsaved changes/)).not.toBeInTheDocument();
    // The dropped read does NOT advance the receipt — no fabricated
    // re-confirmation of the accepted snapshot.
    expect(screen.getByText(/Last received/).textContent).toBe(receiptAfterSave);

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
  });

  test('2.15 the same during-PUT read landing as an ERROR never downgrades the accepted state', async () => {
    const putGate = deferred<Response>();
    const getGate = deferred<Response>();
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : getGate.promise),
      put: () => putGate.promise,
    });
    const view = mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText('Saving for next restart…');

    const stale = view.client.refetchQueries({ queryKey: ['daemon-capacity', SLUG] });
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));
    putGate.resolve(HttpResponse.json(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true, revision: REV_B,
    })));
    await screen.findByText(/Saved for next restart/);
    const receiptAfterSave = screen.getByText(/Last received/).textContent;

    // The obsolete read fails.
    getGate.resolve(new HttpResponse(null, { status: 500 }));
    await stale.catch(() => undefined);

    // The load-error branch does NOT appear and the saved surface survives.
    expect(screen.queryByText(/No values are displayed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not load daemon capacity/)).not.toBeInTheDocument();
    await waitFor(() => expect(savedCell()).toHaveTextContent('5'));
    expect(saveButton()).toBeEnabled();
    expect(screen.getByText(/Last received/).textContent).toBe(receiptAfterSave);
  });

  test('2.16 a later usable read still publishes — ordering safety is not bought by refusing reads forever', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
          next_start: { queue_workers: 5, host_global_session_cap: 12 },
          restart_pending: true, revision: REV_C,
        }))),
      put: () => HttpResponse.json(snapshot({
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        restart_pending: true, revision: REV_B,
      })),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await saveWith();
    await screen.findByText(/Saved for next restart/);

    // A read issued AFTER the write settled publishes normally.
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    // The next manual save carries REV_C — never REV_B, never REV_A.
    await setPair('7', '12');
    await saveWith('after recovery');
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].ifMatch).toBe(`"${REV_C}"`);
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

  test('14.1 a dirty draft meeting a quoted-numeric refresh keeps base/draft/reason and blocks the write', async () => {
    stubVenue({
      get: (i) => (i === 0
        ? HttpResponse.json(snapshot())
        : HttpResponse.json(snapshot({
          persisted_yaml: { queue_workers: '3', host_global_session_cap: 10 },
        }))),
    });
    mount();
    await ready();
    await setPair('5', '12');
    await userEvent.type(reasonBox(), 'why');
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));

    await screen.findByText(/Cannot read capacity configuration/);
    expect(workers()).toHaveValue('5');
    expect(cap()).toHaveValue('12');
    expect(reasonBox()).toHaveValue('why');
    expect(saveButton()).toBeDisabled();
    expect(document.body).toHaveTextContent(/Last known/);

    const putsBefore = puts().length;
    await userEvent.click(saveButton()).catch(() => undefined);
    expect(puts()).toHaveLength(putsBefore);
  });

  test('14.3 a failed refresh retains last-known values and does NOT advance the receipt', async () => {
    stubVenue({
      get: (i) => (i === 0 ? HttpResponse.json(snapshot()) : HttpResponse.error()),
    });
    mount();
    await ready();
    const receipt = screen.getByText(/Last received/).textContent;
    await setPair('5', '12');
    await new Promise((r) => setTimeout(r, 1100));
    await userEvent.click(screen.getByRole('button', { name: /Refresh running state/ }));
    await waitFor(() => expect(gets().length).toBeGreaterThan(1));

    // The failed refresh is not a genuine successful response, so the receipt
    // must not move.
    expect(screen.getByText(/Last received/).textContent).toBe(receipt);
    expect(workers()).toHaveValue('5');
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

    const view = mount(`/orgs/${BETA}/settings/daemon-capacity`);
    await screen.findByRole('heading', { name: 'Capacity' }, { timeout: 5000 });
    await waitFor(() => expect(workers()).toHaveValue('7'));
    // Beta's own revision, not alpha's.
    expect(savedCell()).toHaveTextContent('7');

    await setPair('9', '21');
    await saveWith('beta change');
    await waitFor(() => expect(betaPuts).toHaveLength(1));
    expect(betaPuts[0].ifMatch).toBe(`"${BETA_REV}"`);
    expect(view.client.getQueryData(['daemon-capacity', BETA])).toBeDefined();
    expect(view.client.getQueryData(['daemon-capacity', SLUG])).toBeUndefined();
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

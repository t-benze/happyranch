/**
 * L3 — raw request and response TEXT through the REAL `client.ts`.
 *
 * `./client` is deliberately NOT mocked here. This is the only level that can
 * observe what actually goes on the wire (the `If-Match` value, the request
 * body bytes) and what `JSON.parse` hands the caller back — including the
 * places where it silently destroys information.
 *
 * Unsafe numerics MUST be delivered as raw JSON strings. An object fixture is
 * rounded by `JSON.stringify` BEFORE it ever reaches the transport
 * (`JSON.stringify({ q: 9007199254740993 })` is `{"q":9007199254740992}`), so
 * an object-fixture "unsafe numeric" test is vacuous at exactly the level that
 * matters.
 */
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { ApiError } from './client';
import { getDaemonCapacity, putDaemonCapacity } from './settings';
import { server } from '@/test/server';

const SLUG = 'alpha';
const PATH = `/api/v1/orgs/${SLUG}/settings/daemon-capacity`;
const REV_A = `sha256:${'a'.repeat(64)}`;

interface Captured {
  headers: Record<string, string>;
  rawBody: string;
}

let captured: Captured[] = [];
let attempts = 0;

beforeEach(() => {
  captured = [];
  attempts = 0;
  sessionStorage.setItem('happyranch.token', 'tok');
});
afterEach(() => sessionStorage.clear());

/**
 * `client.ts` clears the cached bearer on a 401 and re-bootstraps once before
 * retrying. That re-bootstrap is a real request, so the fail-closed MSW posture
 * (`onUnhandledRequest: 'error'`) requires it to be declared explicitly rather
 * than left to fall through.
 */
function stubBootstrap() {
  server.use(
    http.get('/api/v1/auth/bootstrap', () =>
      HttpResponse.json({ token: 'tok-2' })),
  );
}

function capturePut(respond: () => Response | Promise<Response>) {
  stubBootstrap();
  server.use(
    http.put(PATH, async ({ request }) => {
      attempts += 1;
      captured.push({
        headers: Object.fromEntries(request.headers.entries()),
        rawBody: await request.text(),
      });
      return respond();
    }),
  );
}

function rawJson(text: string, init: ResponseInit = {}): Response {
  return new HttpResponse(text, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
}

/** A complete, contract-shaped snapshot rendered as raw JSON TEXT. */
function rawSnapshot(overrides: Record<string, string> = {}): string {
  const members: Record<string, string> = {
    running_at_daemon_start: '{"queue_workers":3,"host_global_session_cap":10}',
    running_provenance: '"Resolved when the HappyRanch service started"',
    persisted_yaml: '{"queue_workers":3,"host_global_session_cap":10}',
    next_start: '{"queue_workers":3,"host_global_session_cap":10}',
    environment_shadowed: '[]',
    environment_warning: 'null',
    producer_envelope: '10',
    producer_components: '{"task_workers":3,"thread_workers":4,"dream_workers":1,"wake_workers":1,"schedule_workers":1}',
    effective_admission_cap: '10',
    effective_admission_reason: '"startup policy"',
    warnings: '[]',
    revision: `"${REV_A}"`,
    restart_required: 'false',
    restart_pending: 'false',
    guidance: '{"queue_workers":"g","host_global_session_cap":"g","enforced":false}',
    authorization: '"daemon bearer required"',
    ...overrides,
  };
  return `{${Object.entries(members).map(([k, v]) => `"${k}":${v}`).join(',')}}`;
}

// ---------------------------------------------------------------------------
describe('L3 — the exact bytes on the wire', () => {
  test('15.2 the PUT body carries MAX_SAFE_INTEGER verbatim, with no rounding or exponent form', async () => {
    capturePut(() => rawJson(rawSnapshot()));
    await putDaemonCapacity(SLUG, {
      revision: REV_A,
      queue_workers: 9007199254740991,
      host_global_session_cap: 12,
      rationale: 'boundary',
      confirm_environment_shadow: false,
    });
    expect(captured).toHaveLength(1);
    expect(captured[0].rawBody).toContain('"queue_workers":9007199254740991');
    expect(captured[0].rawBody).not.toContain('e+');
    expect(captured[0].rawBody).not.toContain('9007199254740992');
  });

  test('the revision is moved to a quoted If-Match header and is absent from the body', async () => {
    capturePut(() => rawJson(rawSnapshot()));
    await putDaemonCapacity(SLUG, {
      revision: REV_A,
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    const header = captured[0].headers['if-match'];
    // `routes/settings.py` accepts EXACTLY `"` + `sha256:` + 64 lowercase hex + `"`.
    expect(header).toBe(`"${REV_A}"`);
    expect(header).toHaveLength(73);
    expect(header).toMatch(/^"sha256:[0-9a-f]{64}"$/);

    const body = JSON.parse(captured[0].rawBody);
    expect(body).toEqual({
      queue_workers: 5,
      host_global_session_cap: 12,
      rationale: 'measured receipts',
      confirm_environment_shadow: false,
    });
    expect('revision' in body).toBe(false);
  });
});

// ---------------------------------------------------------------------------
describe('L3 — numeric honesty at the READ boundary (Q8 is NOT solved)', () => {
  test('15.4 an unsafe raw token is destroyed by JSON.parse before any caller sees it', async () => {
    const rawText = rawSnapshot({
      persisted_yaml: '{"queue_workers":9007199254740993,"host_global_session_cap":10}',
    });
    // The RAW body genuinely carries …993.
    expect(rawText).toContain('9007199254740993');
    server.use(http.get(PATH, () => rawJson(rawText)));

    const snapshot = await getDaemonCapacity(SLUG);
    const parsed = snapshot.persisted_yaml.queue_workers as number;

    // …and the caller receives …992. The true digits were NOT recovered, and
    // cannot be: `client.ts` discards the response text after JSON.parse.
    expect(parsed).toBe(9007199254740992);
    expect(String(parsed)).not.toBe('9007199254740993');
    // The guard therefore fires because isSafeInteger FAILS — not because the
    // original token was recovered.
    expect(Number.isSafeInteger(parsed)).toBe(false);
  });

  test('15.5 every entry point is the same transport, so the same loss applies to a PUT success', async () => {
    capturePut(() => rawJson(rawSnapshot({ effective_admission_cap: '9007199254740993' })));
    const result = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12,
      rationale: 'r', confirm_environment_shadow: false,
    });
    expect(result.effective_admission_cap).toBe(9007199254740992);
    expect(Number.isSafeInteger(result.effective_admission_cap as number)).toBe(false);
  });

  test('9.3 a 409 latest carrying an unsafe token loses it identically', async () => {
    const rawText = `{"detail":{"code":"stale_revision","latest":${rawSnapshot({
      persisted_yaml: '{"queue_workers":9007199254740993,"host_global_session_cap":10}',
    })}}}`;
    expect(rawText).toContain('9007199254740993');
    capturePut(() => rawJson(rawText, { status: 409 }));

    const error = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12,
      rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    const detail = (error as ApiError).detail as { latest: { persisted_yaml: { queue_workers: number } } };
    expect(detail.latest.persisted_yaml.queue_workers).toBe(9007199254740992);
  });

  test('15.6 L3 every required RAW NUMERIC FORM is delivered through the real transport and survives exactly', async () => {
    // The transport is the same at every entry point, so the forms are asserted
    // for a non-nullable slot (producer_envelope) AND a documented-nullable slot
    // (persisted_yaml.queue_workers). No token is built as a JS number before
    // stringify — every form is raw TEXT.
    const forms: [string, string, unknown][] = [
      ['a quoted numeric string', '"5.5"', '5.5'],
      ['an unquoted fraction 5.5', '5.5', 5.5],
      ['a boolean', 'true', true],
      ['null', 'null', null],
      ['an array', '[1,2]', [1, 2]],
      ['a safe integer', '5', 5],
    ];
    for (const [slot, build] of [
      ['producer_envelope', (token: string) => rawSnapshot({ producer_envelope: token })],
      ['persisted_yaml.queue_workers', (token: string) =>
        rawSnapshot({ persisted_yaml: `{"queue_workers":${token},"host_global_session_cap":10}` })],
    ] as [string, (token: string) => string][]) {
      for (const [label, rawToken, expected] of forms) {
        server.resetHandlers();
        const rawText = build(rawToken);
        expect(rawText, `${slot} / ${label}`).toContain(rawToken);
        server.use(http.get(PATH, () => rawJson(rawText)));
        const snapshot = await getDaemonCapacity(SLUG);
        const received = slot === 'producer_envelope'
          ? snapshot.producer_envelope
          : snapshot.persisted_yaml.queue_workers;
        expect(received, `${slot} / ${label}`).toEqual(expected);
        if (label.includes('fraction')) {
          // 5.5 must never arrive as 5 at the transport boundary.
          expect(String(received), `${slot} / ${label}`).toBe('5.5');
          expect(received, `${slot} / ${label}`).not.toBe(5);
        }
      }
    }
  });

  test('15.4 L3 raw unsafe tokens at the remaining published members and a 409 latest H lose their digits identically', async () => {
    server.resetHandlers();
    server.use(http.get(PATH, () => rawJson(rawSnapshot({
      running_at_daemon_start: '{"queue_workers":9007199254740993,"host_global_session_cap":10}',
    }))));
    let snap = await getDaemonCapacity(SLUG);
    expect(snap.running_at_daemon_start.queue_workers).toBe(9007199254740992);

    server.resetHandlers();
    server.use(http.get(PATH, () => rawJson(rawSnapshot({
      running_at_daemon_start: '{"queue_workers":3,"host_global_session_cap":9007199254740993}',
    }))));
    snap = await getDaemonCapacity(SLUG);
    expect(snap.running_at_daemon_start.host_global_session_cap).toBe(9007199254740992);

    server.resetHandlers();
    server.use(http.get(PATH, () => rawJson(rawSnapshot({
      next_start: '{"queue_workers":9007199254740993,"host_global_session_cap":10}',
    }))));
    snap = await getDaemonCapacity(SLUG);
    expect(snap.next_start.queue_workers).toBe(9007199254740992);

    server.resetHandlers();
    server.use(http.get(PATH, () => rawJson(rawSnapshot({
      persisted_yaml: '{"queue_workers":3,"host_global_session_cap":9007199254740993}',
    }))));
    snap = await getDaemonCapacity(SLUG);
    expect(snap.persisted_yaml.host_global_session_cap).toBe(9007199254740992);
    expect(Number.isSafeInteger(snap.persisted_yaml.host_global_session_cap)).toBe(false);

    // 409 latest.persisted_yaml.host_global_session_cap — same transport.
    server.resetHandlers();
    const latest = rawSnapshot({
      persisted_yaml: '{"queue_workers":3,"host_global_session_cap":9007199254740993}',
    });
    capturePut(() => rawJson(`{"detail":{"code":"stale_revision","latest":${latest}}}`, { status: 409 }));
    const error = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12,
      rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e) as ApiError;
    const detail = error.detail as { latest: { persisted_yaml: { host_global_session_cap: number } } };
    expect(detail.latest.persisted_yaml.host_global_session_cap).toBe(9007199254740992);
    expect(Number.isSafeInteger(detail.latest.persisted_yaml.host_global_session_cap)).toBe(false);
  });

  test('15.11 CHARACTERIZED FALSE ACCEPT — a parsed safe integer cannot prove an exact or integral raw token', async () => {
    // Two raw FRACTIONAL tokens that JSON.parse rounds into SAFE INTEGERS.
    // Both PASS the guard. This is a documented, unfixed limitation of the
    // narrow frontend scope: the round-trip test that would catch them needs
    // the raw token, which exists at the WRITE boundary (the operator's input
    // text, where it IS checked) and is DESTROYED at the READ boundary by
    // `client.ts`'s `JSON.parse(await res.text())`.
    //
    // Q8 is NOT solved. Closing the read site needs a raw-text/BigInt-aware
    // parse in the shared client or a new API representation — both excluded
    // from this radius and owed to a separate founder decision.
    for (const [rawToken, parsedValue] of [
      ['9007199254740990.5', 9007199254740990],
      ['1.0000000000000001', 1],
    ] as const) {
      server.resetHandlers();
      const rawText = rawSnapshot({ producer_envelope: rawToken });
      expect(rawText).toContain(rawToken);
      server.use(http.get(PATH, () => rawJson(rawText)));

      const snapshot = await getDaemonCapacity(SLUG);
      expect(snapshot.producer_envelope).toBe(parsedValue);
      // The FALSE ACCEPT, asserted as observed behaviour:
      expect(Number.isSafeInteger(snapshot.producer_envelope)).toBe(true);
      expect(String(snapshot.producer_envelope)).not.toBe(rawToken);
    }
  });
});

// ---------------------------------------------------------------------------
describe('L3 — real error envelopes through the real parseError', () => {
  test.each([
    [401, '{"detail":"Not authenticated"}', null],
    [409, `{"detail":{"code":"stale_revision"}}`, 'stale_revision'],
    [428, `{"detail":{"code":"if_match_required"}}`, 'if_match_required'],
    [400, `{"detail":{"code":"if_match_invalid"}}`, 'if_match_invalid'],
    [422, '{"detail":[{"loc":["body"],"msg":"x"}]}', null],
    [503, `{"detail":{"code":"audit_failed"}}`, 'audit_failed'],
    [503, `{"detail":{"code":"config_write_failed","artifact_state":"present"}}`, 'config_write_failed'],
    [503, `{"detail":{"code":"config_publication_uncertain","artifact_state":"unknown"}}`, 'config_publication_uncertain'],
  ])('12.9 status %i produces an ApiError whose status/code match the wire', async (status, body, code) => {
    capturePut(() => rawJson(body, { status }));
    const error = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12,
      rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(status);
    expect((error as ApiError).code).toBe(code);
  });

  test('12.10 an empty 500 body and a 409 with no detail both yield a code-less ApiError', async () => {
    capturePut(() => new HttpResponse(null, { status: 500 }));
    const first = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12, rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);
    expect(first).toBeInstanceOf(ApiError);
    expect((first as ApiError).status).toBe(500);
    expect((first as ApiError).code).toBeNull();
    expect((first as ApiError).detail).toBeNull();

    server.resetHandlers();
    capturePut(() => rawJson('{}', { status: 409 }));
    const second = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12, rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);
    expect((second as ApiError).code).toBeNull();
  });

  test('12.11 a malformed error body reaches the caller as the RAW STRING, never a parsed object', async () => {
    capturePut(() => new HttpResponse('not json', { status: 409 }));
    const error = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12, rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBeNull();
    // `client.ts` falls back to the raw text, so `detail` IS the string.
    expect((error as ApiError).detail).toBe('not json');
  });

  test('2.6 a 200 whose body is not JSON resolves to the RAW STRING, not an object', async () => {
    capturePut(() => new HttpResponse('not json', { status: 200 }));
    const result = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12, rationale: 'r', confirm_environment_shadow: false,
    });
    expect(result).toBe('not json' as unknown);
  });

  test('12.12 OBSERVED retry behaviour: a 401 triggers exactly one master-bearer retry of the PUT', async () => {
    // Reported as observed, not asserted as desirable: `client.ts` is excluded
    // from this radius. The write is attempted TWICE on a 401 because the
    // rebootstrap path re-issues the same request after clearing the token.
    capturePut(() => rawJson('{"detail":"Not authenticated"}', { status: 401 }));
    const error = await putDaemonCapacity(SLUG, {
      revision: REV_A, queue_workers: 5, host_global_session_cap: 12, rationale: 'r', confirm_environment_shadow: false,
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    // FINDING, carried to the reviewer: the shared client retries a WRITE.
    // Both attempts carry the identical If-Match, so a server that already
    // applied the first would reject the second with 409 rather than
    // double-applying — but the duplicate write attempt is real.
    expect(attempts).toBe(2);
    expect(captured[0].rawBody).toBe(captured[1].rawBody);
    expect(captured[0].headers['if-match']).toBe(captured[1].headers['if-match']);
    // No success is claimed from a retried failure.
  });
});

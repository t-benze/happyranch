/**
 * L6b — desktop capacity states from the BUILT app over loopback.
 *
 * Fail-closed venue. Two rules, both enforced and both reported:
 *
 *   1. `harness.mjs` defaults an unmatched `/api/` route to `{}` with status
 *      200 — a SILENT PASS that would make this evidence worthless. An
 *      explicit catch-all handler overrides it: any `/api/` path this script
 *      did not declare is RECORDED and marks the run FAILED.
 *   2. A Playwright context-level route aborts every request whose origin is
 *      not the task-owned loopback server. "Zero mock violations" measured at
 *      the server is server-local; this proves the browser could not reach
 *      anywhere else either.
 *
 * No request may fall through to a live daemon. Every response here is
 * synthetic injection and is NEVER evidence of backend persistence, audit,
 * restart or deployment.
 *
 * Accepted-case coverage produced here:
 *   19.1  the full desktop state matrix at 1440x1000 and 1280x900, light+dark,
 *         with the saving / saved / conflict / unknown states driven by ACTUAL
 *         synthetic PUT transitions rather than initial GET fixtures.
 *   16.8  computed visibility (scrolled into view, collapsed-container and
 *         ancestor-clip aware) of every important warning and helper string.
 *   16.9  colour independence — every important state carries words, not hue.
 *   16.10 computed contrast for EVERY capacity helper, error, warning, ack,
 *         reconciliation, status and control string — a shared token's origin
 *         does not put a capacity requirement out of scope — plus the
 *         :focus-visible ring. There are now ZERO exemptions: the last one,
 *         `shared-button-primitive-accent-label`, was removed once the primary
 *         action's tone was corrected capacity-locally, so every measured
 *         sample is gated. The declaration machinery is kept, empty, so a newly
 *         introduced exemption id still FAILS the run instead of quietly
 *         widening the gate. The ENABLED primary action is additionally
 *         measured in its real resting / hover / active states by driving a
 *         real pointer (`primaryActionTone`), because the variant's hover step
 *         is a different colour from its resting one and a resting-only
 *         reading would not see it. The PORTALLED leave dialog is sampled and
 *         tone-measured while it is actually open, in its own isolated context
 *         so a measurement can never confirm a departure.
 *   16.11 the 16.6 tab order walked by real keystrokes at both widths in both
 *         themes, with the ring measured where it actually paints.
 *   16.6/16.7 the finite set of required keyboard OPERATIONS, declared in
 *         `REQUIRED_OPERATIONS` independently of what a run records and gated
 *         per operation/scenario/viewport/theme, so an operation that never
 *         happened fails the run instead of contributing nothing. Where an
 *         operation has a pure verdict the gate RECOMPUTES it from the
 *         recorded evidence.
 *   2.9/14.3 the C1 retained receipt: a usable read, a byte-identical
 *         successful 200 and then a failed read, under a pinned browser clock,
 *         with the served body hashes and the GET order captured.
 *   19.2  NOT retired. Its assertion — a REAL-BROWSER KEYBOARD PASS AT
 *         1440x1000 — stays mapped and enforced here. It SHARES 16.11's
 *         evidence, because 16.11's four walks are a strict superset (both
 *         widths x both themes), so the two 1440x1000 walks in `tabOrders`
 *         ARE 19.2's result. The sharing is only legitimate while that
 *         mapping holds, so the acceptance gate below asserts the 1440x1000
 *         walks exist, are non-empty, and carry a visible, >=3:1 focus ring
 *         on every control — a missing 1440x1000 walk FAILS the run rather
 *         than silently retiring the case.
 *   19.5  evidence packaging: this committed script writes one PNG per
 *         state/viewport/theme under `--out`, names each file for its state,
 *         records every sha256 in MANIFEST.json, and carries the synthetic
 *         disclosure in that manifest.
 *
 *   node scripts/screenshot-harness/capacity-states.mjs --out <dir>
 *
 * Gate self-test (no browser): feeds a full-run MANIFEST.json, unchanged and
 * then with finite deletions/corruptions, through the SAME `evaluateGate` the
 * run itself uses, and exits non-zero unless every negative control fails the
 * gate and the unchanged manifest passes it.
 *
 *   node scripts/screenshot-harness/capacity-states.mjs --gate-selftest <MANIFEST.json>
 */
import { mkdirSync, writeFileSync, readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, resolve } from 'node:path';
import { createServer, findDist } from './harness.mjs';

const args = process.argv.slice(2);
const SELFTEST_MANIFEST = args.includes('--gate-selftest')
  ? resolve(args[args.indexOf('--gate-selftest') + 1])
  : null;

const PLAYWRIGHT = process.env.HR_PLAYWRIGHT_ROOT
  ?? '/tmp/task-6530-playwright/node_modules/playwright/index.js';
// Playwright ships CommonJS, so an ESM `import()` hands it back under
// `default`. Accept either shape rather than assuming one. The gate self-test
// never opens a browser, so it does not load it.
const playwrightModule = SELFTEST_MANIFEST ? null : await import(PLAYWRIGHT);
const { chromium } = playwrightModule ? (playwrightModule.default ?? playwrightModule) : {};

const outDir = resolve(args[args.indexOf('--out') + 1] ?? './capacity-shots');
if (!SELFTEST_MANIFEST) mkdirSync(outDir, { recursive: true });

const SLUG = 'alpha';
const REV_A = `sha256:${'a'.repeat(64)}`;
const REV_B = `sha256:${'b'.repeat(64)}`;
const REV_C = `sha256:${'c'.repeat(64)}`;

/** Synthetic example data. Not a real daemon observation. */
function snapshot(overrides = {}) {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'startup-resolved settings snapshot',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [],
    environment_warning: null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: {
      queue_workers: 'Starting guidance 4-6.',
      host_global_session_cap: 'Starting guidance 11-13.',
      enforced: false,
    },
    authorization: 'daemon bearer required',
    ...overrides,
  };
}

const SETTINGS = {
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

/**
 * Per-state request control.
 *
 * `get(index)` and `put(index)` return a response descriptor, so a state can
 * drive a REAL synthetic PUT transition (saving / saved / conflict / unknown)
 * and a REAL second GET (stale-failed-refresh / malformed-after-draft) instead
 * of standing in an initial GET fixture for a state the app only ever reaches
 * through a transition.
 *
 * `{ hold: true }` never answers, which is how the genuine loading frame and
 * the genuine in-flight saving frame are captured.
 */
const OK = (json) => ({ status: 200, json });
const FAIL = (status, json = {}) => ({ status, json });

/**
 * C1 retained-receipt sequence. Every context runs in UTC so the app's own
 * `formatReceipt` text is deterministic, and each step pins the browser clock
 * before the read that step issues.
 */
const C1_T0 = Date.UTC(2027, 0, 15, 16, 0, 0);
const C1_TIMES = { t0: C1_T0, t1: C1_T0 + 60_000, t2: C1_T0 + 120_000, t3: C1_T0 + 180_000 };
/** The app's receipt copy (`formatReceipt` in capacityModel.ts) for a UTC instant. */
const receiptText = (ms) => {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `Last received ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} (this browser's clock)`;
};
/** GET #0 and #1 are the SAME bytes, #2 fails, #3 (recovery) is usable again. */
const C1_GET = (index) => (index === 2 ? FAIL(503, {}) : OK(snapshot()));

async function runC1Sequence(page, { recovery }) {
  const read = () => page.evaluate(() => {
    const text = document.body.innerText;
    const banner = [...document.querySelectorAll('p[role="alert"]')]
      .find((p) => p.innerText.includes('Could not refresh')) ?? null;
    return {
      receipts: text.match(/Last received \d\d:\d\d:\d\d \(this browser's clock\)/g) ?? [],
      bannerText: banner ? banner.innerText : null,
      lastKnownLabel: text.includes('the last values this browser received'),
      runningNowLabel: text.includes('observed from the daemon; not changed by saving'),
    };
  });
  const refreshAt = async (ms) => {
    await page.clock.setFixedTime(ms);
    await page.click('button:has-text("Refresh running state")');
  };
  const steps = { step0: await read() };
  await refreshAt(C1_TIMES.t1);
  await page.waitForFunction((want) => document.body.innerText.includes(want), receiptText(C1_TIMES.t1), { timeout: 15000 })
    .catch(() => {});
  await page.waitForTimeout(200);
  steps.step1 = await read();
  await refreshAt(C1_TIMES.t2);
  await page.waitForSelector('text=Could not refresh. Current state unverified.', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(200);
  steps.step2 = await read();
  if (recovery) {
    await refreshAt(C1_TIMES.t3);
    await page.waitForFunction(
      (want) => document.body.innerText.includes(want)
        && !document.body.innerText.includes('Could not refresh'),
      receiptText(C1_TIMES.t3),
      { timeout: 15000 },
    ).catch(() => {});
    await page.waitForTimeout(200);
    steps.step3 = await read();
  }
  return { c1: { recovery, steps } };
}

/**
 * Pure C1 verdict over the captured GET order and the rendered receipts. The
 * acceptance gate recomputes it from the recorded evidence, so a corrupted
 * record cannot pass on a stale `ok`.
 */
function c1Verdict({ recovery, steps = {}, requests = [] }) {
  const fail = [];
  const gets = requests.filter((r) => r.method === 'GET');
  const want = recovery ? [200, 200, 503, 200] : [200, 200, 503];
  if (gets.length !== want.length || gets.some((g, i) => g.status !== want[i])) {
    fail.push(`GET order ${JSON.stringify(gets.map((g) => g.status))}, expected ${JSON.stringify(want)}`);
  }
  if (!(typeof gets[0]?.bodySha === 'string' && gets[0].bodySha === gets[1]?.bodySha)) {
    fail.push('the t1 response is not byte-identical to the t0 response');
  }
  const only = (step, ms, label) => {
    const got = step?.receipts ?? [];
    if (got.length === 0 || got.some((r) => r !== receiptText(ms))) {
      fail.push(`${label}: rendered ${JSON.stringify(got)}, expected only "${receiptText(ms)}"`);
    }
  };
  only(steps.step0, C1_TIMES.t0, 't0 initial read');
  only(steps.step1, C1_TIMES.t1, 't1 identical 200');
  if (!steps.step1?.runningNowLabel || steps.step1?.bannerText !== null) fail.push('t1 is not a current usable read');
  // The retained record: t1's receipt, under the "Last known" label, in both
  // the banner and the footer — never t0's and never the failed t2 attempt's.
  only(steps.step2, C1_TIMES.t1, 't2 failed read');
  if (!(steps.step2?.bannerText ?? '').includes(receiptText(C1_TIMES.t1))) fail.push('t2 banner does not name the t1 receipt');
  if (!steps.step2?.lastKnownLabel) fail.push('t2 retained values are not labelled "Last known"');
  if ((steps.step2?.receipts ?? []).length < 2) fail.push('t2 retained receipt not rendered in both banner and footer');
  if (recovery) {
    only(steps.step3, C1_TIMES.t3, 't3 usable recovery');
    if (steps.step3?.bannerText !== null || !steps.step3?.runningNowLabel) fail.push('t3 did not recover to a current usable read');
  }
  return { ok: fail.length === 0, fail };
}

/**
 * The accepted 19.1 desktop state matrix. Every entry is captured at BOTH
 * widths in BOTH themes.
 */
const STATES = [
  {
    name: 'loading',
    settle: 'loading',
    get: () => ({ hold: true }),
  },
  // `primaryAction`: this state's Save control is ENABLED, so it carries the
  // real resting/hover/active tone measurement for accepted 16.10.
  { name: 'ordinary', get: () => OK(snapshot()), primaryAction: 'button[type="submit"]' },
  {
    name: 'dirty-consequence',
    get: () => OK(snapshot()),
    primaryAction: 'button[type="submit"]',
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Queue delay grew after adding the second team.');
    },
  },
  {
    // A GENUINE in-flight write: the PUT is issued and never answered, so the
    // pending frame is the app's own transition, not a fixture.
    name: 'saving-pending',
    get: () => OK(snapshot()),
    put: () => ({ hold: true }),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Raising task slots for the new team.');
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Saving for next restart…', { timeout: 15000 });
    },
  },
  {
    // Driven by an ACTUAL PUT, not by an initial GET fixture.
    name: 'saved-restart-pending',
    get: () => OK(snapshot()),
    put: () => OK(snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
      revision: REV_B,
    })),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Raising task slots for the new team.');
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Saved for next restart. Running limits are unchanged.', { timeout: 15000 });
    },
  },
  {
    name: 'conflict-reconciliation',
    get: () => OK(snapshot()),
    put: () => FAIL(409, {
      detail: {
        code: 'stale_revision',
        latest: snapshot({
          revision: REV_B,
          persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }),
      },
    }),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Raising task slots for the new team.');
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Keep my draft, rebase onto latest', { timeout: 15000 });
    },
  },
  {
    name: 'unknown-submitted',
    get: () => OK(snapshot()),
    put: () => FAIL(500, {}),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Raising task slots for the new team.');
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Save result unknown.', { timeout: 15000 });
    },
  },
  {
    name: 'stale-failed-refresh',
    // The subject of this capture is the RETAINED-RECEIPT banner, which sits at
    // the top of the panel while `prep` clicks a control near the bottom.
    frame: { sel: 'p[role="alert"]', label: 'refresh-failed retained-receipt banner' },
    get: (index) => (index === 0 ? OK(snapshot()) : FAIL(503, {})),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-reason', 'Checking the running state before saving.');
      await page.click('button:has-text("Refresh running state")');
      await page.waitForSelector('text=Could not refresh. Current state unverified.', { timeout: 15000 });
    },
  },
  {
    name: 'malformed-after-draft',
    frame: { sel: 'p[role="alert"]', label: 'unusable-read retained-receipt banner' },
    get: (index) => (index === 0
      ? OK(snapshot())
      : OK(snapshot({ persisted_yaml: { queue_workers: '3', host_global_session_cap: 10 } }))),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-reason', 'Checking the running state before saving.');
      await page.click('button:has-text("Refresh running state")');
      await page.waitForSelector('text=Cannot read capacity configuration.', { timeout: 15000 });
    },
  },
  {
    name: 'denied',
    settle: 'denied',
    get: () => FAIL(401, { detail: { code: 'unauthorized', message: 'daemon bearer required' } }),
  },
  {
    name: 'fallback-cap',
    get: () => OK(snapshot({
      effective_admission_cap: 4,
      effective_admission_reason: 'Capability fallback binds because enforcement is unavailable.',
      warnings: ['Enforcement is unavailable on this platform; the conservative fallback cap binds.'],
    })),
  },
  {
    name: 'partial-override',
    get: () => OK(snapshot({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'The environment sets this value; a restart alone will not make the saved file win.',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    })),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '14');
    },
  },
  {
    name: 'runtime-unavailable',
    get: () => OK(snapshot({
      effective_admission_cap: null,
      effective_admission_reason: 'No supervisor snapshot',
    })),
  },
  {
    name: 'not-set-in-file',
    get: () => OK(snapshot({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } })),
  },
  {
    name: 'representation-unavailable',
    frame: { sel: 'p[role="alert"]', label: 'unrepresentable-value retained-receipt banner' },
    get: () => OK(snapshot({
      persisted_yaml: { queue_workers: 9007199254740992, host_global_session_cap: 10 },
    })),
  },
  {
    name: 'invalid-numeric',
    get: () => OK(snapshot()),
    prep: async (page) => {
      await page.fill('#capacity-workers', '9007199254740993');
      await page.fill('#capacity-reason', 'attempting an unrepresentable value');
      await page.click('button[type="submit"]');
      await page.waitForSelector('#capacity-workers-error');
    },
  },
  {
    name: 'details-expanded',
    get: () => OK(snapshot()),
    prep: async (page) => {
      await page.click('summary:has-text("Capacity details")');
    },
  },
  // C1 (accepted 2.9 receipt rule composed with 14.3 retention): a usable read
  // at t0, a BYTE-IDENTICAL usable 200 at t1, then a failed read at t2 must
  // retain and render t1 — not t0, and never t2. The browser clock is pinned
  // per step (`page.clock.setFixedTime`), and the GET order and response body
  // hashes are captured server-side, so an unchanged value alone is never read
  // as proof of an identical receipt.
  {
    name: 'identical-refresh-retained',
    c1: { recovery: false },
    frame: { sel: 'p[role="alert"]', label: 'identical-200 then failed-read retained-receipt banner' },
    get: C1_GET,
    prep: (page) => runC1Sequence(page, { recovery: false }),
  },
  {
    // ...then a genuinely usable read at t3 recovers, and the receipt advances.
    name: 'identical-refresh-recovered',
    c1: { recovery: true },
    frame: { sel: 'p:has-text("Last received")', label: 'recovered current-read receipt' },
    get: C1_GET,
    prep: (page) => runC1Sequence(page, { recovery: true }),
  },
];

/**
 * Accepted 16.6 / 16.7 / 16.11 — the STATEFUL keyboard matrix.
 *
 * 16.11 requires the 16.6 control set in a real browser at both widths in both
 * themes, and 16.6 names Rebase, Accept latest, Check saved values, the
 * acknowledgment and the details disclosure explicitly. The previous pass
 * walked seven controls on the ordinary form only, so five of those were never
 * reached: each one exists only in a state the ordinary form is not in.
 *
 * Every scenario therefore reaches its controls the way an operator would —
 * through an actual allowed synthetic GET/PUT response and the app's own
 * transition — rather than by seeding a fixture that starts at the end state.
 * `operate` then drives the control from the KEYBOARD and asserts the state
 * change it is supposed to cause; an operation that does not happen fails the
 * run, so a control cannot pass by being merely focusable.
 *
 * `case19.2`'s 1440 keyboard coverage is subsumed here, not retired: the
 * `ordinary` scenario is its result and the manifest still names it.
 */
const DIRTY_DRAFT = async (page) => {
  await page.fill('#capacity-workers', '5');
  await page.fill('#capacity-cap', '12');
  await page.fill('#capacity-reason', 'Raising task slots for the new team.');
};

/** The accepted override fixture: the environment shadows `queue_workers`. */
const OVERRIDE_SNAPSHOT = () => snapshot({
  environment_shadowed: ['queue_workers'],
  environment_warning: 'The environment sets this value; a restart alone will not make the saved file win.',
  next_start: { queue_workers: 3, host_global_session_cap: 12 },
});

/** A coherent server-shaped snapshot after a save of `w`/`h` at `revision`. */
const SAVED_SNAPSHOT = (w, h, revision) => snapshot({
  persisted_yaml: { queue_workers: w, host_global_session_cap: h },
  next_start: { queue_workers: w, host_global_session_cap: h },
  restart_pending: true,
  revision,
});

const W_LABEL = 'Task session slots';
const H_LABEL = 'Host session admission limit';
const pairText = (w, h) => `${W_LABEL} ${w}, ${H_LABEL} ${h}`;
const UNKNOWN_COPY = 'Save result unknown. Your draft is retained. Reconnect and check saved values before trying again.';
const RECONCILE_FIRST = 'Reconcile the saved values before saving again. Choose to rebase onto the latest saved values or to discard your draft and accept them.';
const LEAVE_DIALOG = '[role="dialog"][aria-label="discard capacity draft confirmation"]';

const countRequests = (method) => requestLog.filter((r) => r.method === method).length;

/** Wait (bounded) until the venue has answered more than `n` requests of `method`. */
async function waitForRequests(page, method, n, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (countRequests(method) <= n && Date.now() < deadline) await page.waitForTimeout(100);
}

/**
 * Keyboard-focus a control from the START of the document. Operations run after
 * other operations have moved focus, and a forward-only walk from an arbitrary
 * point can run out of steps before it wraps.
 */
async function tabToFromStart(page, predicate) {
  await page.evaluate(() => document.body.focus());
  await page.keyboard.press('Tab');
  return tabTo(page, predicate, 140);
}

/** The editor's observable state, read the way an operator would see it. */
const READ_EDITOR = () => {
  const text = document.body.innerText;
  const outcome = document.querySelector('#capacity-outcome');
  const comparison = outcome
    ? [...outcome.querySelectorAll(':scope > div')].find((d) => d.querySelector('dl')) ?? null
    : null;
  // `textContent`, not `innerText`: the <dt> labels are CSS-uppercased.
  const dd = (label) => {
    const dt = [...(comparison?.querySelectorAll('dt') ?? [])]
      .find((x) => x.textContent.trim().toLowerCase() === label.toLowerCase());
    return dt?.nextElementSibling?.textContent.replace(/\s+/g, ' ').trim() ?? null;
  };
  const para = (prefix) => [...(outcome?.querySelectorAll('p') ?? [])]
    .map((p) => p.textContent.replace(/\s+/g, ' ').trim())
    .find((t) => t.startsWith(prefix)) ?? null;
  const button = (label) => [...document.querySelectorAll('button')]
    .some((b) => b.textContent.trim() === label);
  return {
    headline: comparison?.querySelector('p')?.textContent.replace(/\s+/g, ' ').trim() ?? null,
    acceptedBase: dd('Accepted base'),
    yourDraft: dd('Your draft'),
    currentlySaved: dd('Currently saved'),
    submitted: para('You submitted'),
    newerDraft: para('Your current draft is'),
    alerts: [...(outcome?.querySelectorAll('p[role="alert"]') ?? [])].map((p) => p.textContent.trim()),
    refreshFailed: text.includes('Could not refresh'),
    savedClaim: text.includes('Saved for next restart') || text.includes('Saved. No restart is pending'),
    changedElsewhere: text.includes('Configuration changed elsewhere.'),
    rebaseOffered: button('Keep my draft, rebase onto latest'),
    acceptOffered: button('Discard draft, accept latest'),
    checkOffered: button('Check saved values'),
    dirty: text.includes('Unsaved changes.'),
    fields: {
      workers: document.querySelector('#capacity-workers')?.value ?? null,
      cap: document.querySelector('#capacity-cap')?.value ?? null,
      reason: document.querySelector('#capacity-reason')?.value ?? null,
      ack: document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null,
    },
    focusedId: document.activeElement?.id || null,
    focusedTag: document.activeElement?.tagName ?? null,
    dialogOpen: document.querySelector('[role="dialog"][aria-label="discard capacity draft confirmation"]') !== null,
    url: location.pathname,
  };
};

/**
 * Accepted 10.x/11.x "Check saved values" after an ACTUAL uncertain PUT.
 *
 * The scenario submitted 5/12 against REV_A, the PUT's outcome is unknown, and
 * the operator has since typed a NEWER draft 7/14. The check's reread returns
 * 5/12 at REV_B. Evidence is collected here and judged by the pure
 * `checkUsableVerdict`, which the acceptance gate recomputes.
 */
const CHECK_REASON = 'Raising task slots for the new team.';
const CHECK_EXPECTED_HEADLINE = 'Saved values now match what you submitted (5 / 12). '
  + 'This does not confirm your request caused it.';

async function collectCheckEvidence(page) {
  const hit = await tabToFromStart(page, { text: 'Check saved values' });
  if (!hit) return { reached: false };
  const start = requestLog.length;
  const getsBefore = countRequests('GET');
  await page.keyboard.press('Enter');
  await waitForRequests(page, 'GET', getsBefore);
  await page.waitForTimeout(600);
  const ui = await page.evaluate(READ_EDITOR);
  const checkRequests = requestLog.slice(start);
  // A SEPARATE, deliberate Save attempt while the outcome is still unresolved:
  // it must be refused with the reconcile-first reason and send nothing.
  const saveStart = requestLog.length;
  const save = await tabToFromStart(page, { text: 'Save for next restart' });
  let afterSave = null;
  if (save) {
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    afterSave = await page.evaluate(READ_EDITOR);
  }
  return {
    reached: true,
    checkRequests,
    ui,
    saveReached: Boolean(save),
    afterSave,
    saveAttemptRequests: requestLog.slice(saveStart),
  };
}

function checkUsableVerdict(ev) {
  if (!ev?.reached) return { ok: false, fail: ['Check saved values was never reached'] };
  const fail = [];
  const gets = (ev.checkRequests ?? []).filter((r) => r.method === 'GET');
  if ((ev.checkRequests ?? []).some((r) => r.method === 'PUT')) fail.push('Check sent a PUT');
  if (gets.length !== 1 || gets[0].status !== 200 || gets[0].revision !== REV_B) {
    fail.push(`Check did not settle exactly one successful GET of the expected latest: ${JSON.stringify(gets)}`);
  }
  const ui = ev.ui ?? {};
  if (ui.headline !== CHECK_EXPECTED_HEADLINE) fail.push(`comparison headline ${JSON.stringify(ui.headline)}`);
  if (ui.currentlySaved !== pairText(5, 12)) fail.push(`currently saved ${JSON.stringify(ui.currentlySaved)}`);
  if (ui.acceptedBase !== pairText(3, 10)) fail.push(`accepted base moved: ${JSON.stringify(ui.acceptedBase)}`);
  if (ui.yourDraft !== pairText(7, 14)) fail.push(`draft cell ${JSON.stringify(ui.yourDraft)}`);
  if (ui.submitted !== `You submitted ${pairText(5, 12)} against revision ${REV_A}.`) {
    fail.push(`submitted record ${JSON.stringify(ui.submitted)}`);
  }
  if (!(ui.newerDraft ?? '').startsWith(`Your current draft is ${pairText(7, 14)} and is still unsaved.`)) {
    fail.push(`newer draft ${JSON.stringify(ui.newerDraft)}`);
  }
  if (!(ui.alerts ?? []).includes(UNKNOWN_COPY)) fail.push('Check cleared the unknown outcome');
  if (ui.refreshFailed) fail.push('the reread is shown as failed');
  if (ui.savedClaim) fail.push('Check claimed a save');
  if (!ui.rebaseOffered || !ui.acceptOffered) fail.push('explicit choice (rebase / accept latest) not offered');
  if (ui.fields?.workers !== '7' || ui.fields?.cap !== '14' || ui.fields?.reason !== CHECK_REASON) {
    fail.push(`newer draft fields not preserved: ${JSON.stringify(ui.fields)}`);
  }
  if (!ui.dirty) fail.push('navigation guard disarmed while unresolved');
  if (!ev.saveReached) fail.push('Save not reachable for the refusal check');
  if (!(ev.afterSave?.alerts ?? []).includes(RECONCILE_FIRST)) fail.push('Save was not refused with the reconcile-first reason');
  if ((ev.saveAttemptRequests ?? []).length !== 0) fail.push('the refused Save sent a request');
  return { ok: fail.length === 0, fail };
}

/** Pure verdicts the acceptance gate RECOMPUTES from recorded evidence. */
const OPERATION_VERDICTS = {
  'check-usable': (record) => checkUsableVerdict(record.evidence),
};

const KEYBOARD_SCENARIOS = [
  {
    // The accepted 16.6 order on the ordinary form:
    // W -> H -> reason -> Save -> Discard -> Refresh -> Capacity details.
    name: 'ordinary',
    get: () => OK(snapshot()),
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'cap', id: 'capacity-cap' },
      { key: 'reason', id: 'capacity-reason' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'discard', text: 'Discard draft' },
      { key: 'refresh', text: 'Refresh running state' },
      { key: 'details', text: 'Capacity details' },
    ],
    operate: async (page) => {
      const hit = await tabTo(page, { text: 'Capacity details' });
      if (!hit) return { op: 'details-open', ok: false, detail: 'never reached' };
      const before = await page.evaluate(() => document.querySelector('details')?.open ?? null);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(200);
      const after = await page.evaluate(() => document.querySelector('details')?.open ?? null);
      return {
        op: 'details-open', before, after, ok: before === false && after === true,
      };
    },
  },
  {
    // The acknowledgment sits between the reason and Save in the accepted
    // order, and only exists while the environment shadows a key.
    name: 'override-ack',
    get: () => OK(OVERRIDE_SNAPSHOT()),
    // `saveDisabled` includes `(shadowed && !ack)` (4.1b), and a DISABLED
    // control is not in the tab ring at all. So in this state Save genuinely
    // does not exist for the keyboard until the acknowledgment is confirmed —
    // that is correct product behaviour, not a gate defect. The accepted 16.6
    // order is W -> H -> reason -> ack -> Save -> ..., so the acknowledgment
    // must be KEYBOARD-operated first and the full order walked afterwards
    // (`operateFirst`). The draft is typed the way an operator would, so Save
    // is walked as a real enabled control rather than a re-enabled stub.
    prep: DIRTY_DRAFT,
    operateFirst: true,
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'cap', id: 'capacity-cap' },
      { key: 'reason', id: 'capacity-reason' },
      { key: 'ack', type: 'checkbox' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'discard', text: 'Discard draft' },
      { key: 'refresh', text: 'Refresh running state' },
      { key: 'details', text: 'Capacity details' },
    ],
    operate: async (page) => {
      const hit = await tabTo(page, { type: 'checkbox' });
      if (!hit) return { op: 'ack-enables-save', ok: false, detail: 'never reached' };
      const read = () => page.evaluate(() => ({
        checked: document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null,
        saveDisabled: document.querySelector('button[type="submit"]')?.disabled ?? null,
      }));
      const before = await read();
      await page.keyboard.press('Space');
      await page.waitForTimeout(200);
      const after = await read();
      return {
        op: 'ack-enables-save',
        before,
        after,
        // The acknowledgment is what ENABLES Save (4.1b): checked false -> true
        // and Save disabled -> enabled, by the keyboard alone.
        ok: before.checked === false && after.checked === true
          && before.saveDisabled === true && after.saveDisabled === false,
      };
    },
  },
  {
    // "Check saved values" is only offered after a genuinely UNCERTAIN write
    // outcome, so it is reached through a real refused PUT, not a fixture. The
    // operator then types a NEWER draft, so the check has a submitted pair AND
    // a newer draft to keep apart (accepted 10.9 / 11.2 / 11.3 / 11.8 / 11.9).
    name: 'unknown-check',
    get: (index) => {
      if (index === 0) return OK(snapshot());
      if (index === 1) return OK(SAVED_SNAPSHOT(5, 12, REV_B));
      return OK(SAVED_SNAPSHOT(7, 14, REV_C));
    },
    put: (index) => (index === 0 ? FAIL(500, {}) : OK(SAVED_SNAPSHOT(7, 14, REV_C))),
    prep: async (page) => {
      await DIRTY_DRAFT(page);
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Save result unknown.', { timeout: 15000 });
      await page.fill('#capacity-workers', '7');
      await page.fill('#capacity-cap', '14');
    },
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'refresh', text: 'Refresh running state' },
      { key: 'check', text: 'Check saved values' },
    ],
    operate: async (page) => {
      const evidence = await collectCheckEvidence(page);
      const usable = checkUsableVerdict(evidence);
      const records = [{ op: 'check-usable', evidence, verdict: usable, ok: usable.ok }];
      // The deliberate choice and the SEPARATE manual save accepted 11.9 /
      // 10.9 require: rebase keeps the NEWER draft, and only then does a
      // keyboard Save send it against the latest revision.
      const rebase = await tabToFromStart(page, { text: 'Keep my draft, rebase onto latest' });
      if (!rebase) {
        records.push({ op: 'check-rebase-manual-save', ok: false, detail: 'rebase never reached' });
        return records;
      }
      const putsBefore = countRequests('PUT');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      const afterChoice = await page.evaluate(READ_EDITOR);
      const putsAfterChoice = countRequests('PUT') - putsBefore;
      const start = requestLog.length;
      const save = await tabToFromStart(page, { text: 'Save for next restart' });
      if (save) {
        await page.keyboard.press('Enter');
        await page.waitForSelector('text=Saved for next restart. Running limits are unchanged.', { timeout: 15000 })
          .catch(() => {});
        await page.waitForTimeout(300);
      }
      const put = requestLog.slice(start).find((r) => r.method === 'PUT') ?? null;
      const final = await page.evaluate(READ_EDITOR);
      records.push({
        op: 'check-rebase-manual-save',
        afterChoice,
        putsAfterChoice,
        put,
        // The whole request sequence, so the manual save's wire identity is
        // reconcilable against everything that preceded it.
        requests: [...requestLog],
        final,
        ok: afterChoice.focusedId === 'capacity-outcome'
          && putsAfterChoice === 0
          && afterChoice.fields.workers === '7' && afterChoice.fields.cap === '14'
          && afterChoice.fields.reason === CHECK_REASON
          && afterChoice.submitted === null && !afterChoice.alerts.includes(UNKNOWN_COPY)
          && Boolean(save)
          && put !== null && put.status === 200 && put.ifMatch === `"${REV_B}"`
          && JSON.stringify(put.body) === JSON.stringify({
            queue_workers: 7, host_global_session_cap: 14,
            rationale: CHECK_REASON, confirm_environment_shadow: false,
          })
          && final.savedClaim && !final.dirty && final.submitted === null
          && !final.alerts.includes(UNKNOWN_COPY)
          && final.fields.workers === '7' && final.fields.cap === '14' && final.fields.reason === '',
      });
      return records;
    },
  },
  {
    // Rebase / Accept latest exist only after a real 409, and 16.7 requires
    // focus to land deterministically on the reconciled region once a choice
    // is made.
    name: 'conflict-rebase',
    get: () => OK(snapshot()),
    put: () => FAIL(409, {
      detail: {
        code: 'stale_revision',
        latest: snapshot({
          revision: REV_B,
          persisted_yaml: { queue_workers: 2, host_global_session_cap: 9 },
        }),
      },
    }),
    prep: async (page) => {
      await DIRTY_DRAFT(page);
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Keep my draft, rebase onto latest', { timeout: 15000 });
    },
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'rebase', text: 'Keep my draft, rebase onto latest' },
      { key: 'accept', text: 'Discard draft, accept latest' },
    ],
    operate: async (page) => {
      const hit = await tabTo(page, { text: 'Keep my draft, rebase onto latest' });
      if (!hit) return { op: 'rebase', ok: false, detail: 'never reached' };
      const putsBefore = countRequests('PUT');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(600);
      const state = await page.evaluate(READ_EDITOR);
      const putsAfter = countRequests('PUT') - putsBefore;
      return {
        op: 'rebase',
        ...state,
        putsAfter,
        // 16.7: focus moves to the reconciled region, is never lost to <body>,
        // and the choice actually clears the reconciliation offer. A rebase
        // PRESERVES the draft verbatim — values AND reason — and sends nothing;
        // saving it stays a separate, manual action.
        ok: state.focusedId === 'capacity-outcome'
          && state.focusedTag !== 'BODY'
          && state.rebaseOffered === false
          && state.fields.workers === '5' && state.fields.cap === '12'
          && state.fields.reason === 'Raising task slots for the new team.'
          && state.dirty === true
          && putsAfter === 0,
      };
    },
  },
  {
    // A FRESH real 409, separate from the rebase branch: the operator discards
    // the draft and accepts the latest saved values (accepted 8.3 / 16.7), then
    // makes a new deliberate edit whose manual save must carry the accepted
    // latest revision (10.8 / 11.10).
    name: 'conflict-accept',
    get: (index) => (index === 0 ? OK(snapshot()) : OK(SAVED_SNAPSHOT(4, 9, REV_C))),
    put: (index) => (index === 0
      ? FAIL(409, { detail: { code: 'stale_revision', latest: SAVED_SNAPSHOT(2, 9, REV_B) } })
      : OK(SAVED_SNAPSHOT(4, 9, REV_C))),
    prep: async (page) => {
      await DIRTY_DRAFT(page);
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Discard draft, accept latest', { timeout: 15000 });
    },
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'rebase', text: 'Keep my draft, rebase onto latest' },
      { key: 'accept', text: 'Discard draft, accept latest' },
    ],
    operate: async (page) => {
      const records = [];
      const conflictPut = requestLog.find((r) => r.method === 'PUT') ?? null;
      const hit = await tabToFromStart(page, { text: 'Discard draft, accept latest' });
      if (!hit) return [{ op: 'accept-latest', ok: false, detail: 'never reached' }];
      const putsBefore = countRequests('PUT');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(600);
      const state = await page.evaluate(READ_EDITOR);
      const putsAfter = countRequests('PUT') - putsBefore;
      records.push({
        op: 'accept-latest',
        // Which response and which choice produced this state.
        conflictResponse: conflictPut,
        ...state,
        putsAfter,
        ok: conflictPut !== null && conflictPut.status === 409 && conflictPut.latestRevision === REV_B
          // The latest pair replaces the draft, and the draft's reason goes.
          && state.fields.workers === '2' && state.fields.cap === '9' && state.fields.reason === ''
          // Reconciliation, submission and lock are all resolved.
          && state.rebaseOffered === false && state.acceptOffered === false
          && state.submitted === null && state.changedElsewhere === false
          && state.alerts.length === 0
          // 16.7: focus lands on the reconciled region, never on <body>.
          && state.focusedId === 'capacity-outcome'
          // Clean: `dirty` is the exact boolean the navigation guard uses.
          && state.dirty === false
          // The choice itself sends nothing.
          && putsAfter === 0,
      });
      // A NEW deliberate edit, typed from the keyboard, then a manual Save.
      const start = requestLog.length;
      const workers = await tabToFromStart(page, { id: 'capacity-workers' });
      if (workers) {
        await page.keyboard.press('Control+A');
        await page.keyboard.type('4');
      }
      const reason = await tabTo(page, { id: 'capacity-reason' });
      if (reason) await page.keyboard.type('Fresh intent after accepting latest.');
      const save = await tabTo(page, { text: 'Save for next restart' });
      if (save) {
        await page.keyboard.press('Enter');
        await page.waitForSelector('text=Saved for next restart. Running limits are unchanged.', { timeout: 15000 })
          .catch(() => {});
        await page.waitForTimeout(300);
      }
      const put = requestLog.slice(start).find((r) => r.method === 'PUT') ?? null;
      const final = await page.evaluate(READ_EDITOR);
      // The guard is clean after the save: a real keyboard navigation departs
      // without the leave dialog.
      const link = page.getByRole('link', { name: 'Organization', exact: true });
      await link.focus();
      await page.keyboard.press('Enter');
      let departed = false;
      try {
        await page.waitForURL('**/settings/organization', { timeout: 10000 });
        departed = true;
      } catch { departed = false; }
      const dialogAfter = await page.evaluate(
        (sel) => document.querySelector(sel) !== null,
        LEAVE_DIALOG,
      );
      records.push({
        op: 'accept-latest-then-manual-save',
        put,
        final,
        departed,
        dialogAfter,
        ok: Boolean(workers && reason && save)
          && put !== null && put.status === 200
          // The accepted latest revision is the new base.
          && put.ifMatch === `"${REV_B}"`
          && JSON.stringify(put.body) === JSON.stringify({
            queue_workers: 4, host_global_session_cap: 9,
            rationale: 'Fresh intent after accepting latest.', confirm_environment_shadow: false,
          })
          && final.savedClaim && !final.dirty
          && departed && !dialogAfter,
      });
      return records;
    },
  },
  {
    // The PORTALLED leave-confirmation dialog. It is a capacity-owned surface
    // that lives outside the panel wrapper, which is exactly why its focus
    // rings were never in these rollups. Reached the way an operator reaches
    // it: a real draft — values, reason AND a checked acknowledgment — then a
    // real keyboard activation of a real navigation control.
    name: 'leave-dialog',
    get: () => OK(OVERRIDE_SNAPSHOT()),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'retained exact draft');
      await page.check('#capacity-override input[type=checkbox]');
      const link = page.getByRole('link', { name: 'Organization', exact: true });
      await link.focus();
      await page.keyboard.press('Enter');
      await page.locator(LEAVE_DIALOG).waitFor({ timeout: 15000 });
      await page.waitForTimeout(250);
    },
    start: `${LEAVE_DIALOG} button`,
    required: [
      { key: 'stay', text: 'Stay on page' },
      { key: 'discard-continue', text: 'Discard and continue' },
      { key: 'dialog-close', ariaLabel: 'Close' },
    ],
    screenshot: true,
    operate: async (page) => {
      // Stay: route unchanged, draft retained EXACTLY, focus returned to the
      // navigation trigger that opened the dialog (16.7).
      const records = [];
      const urlBefore = page.url();
      const stay = await tabTo(page, { text: 'Stay on page' });
      if (!stay) {
        records.push({ op: 'stay', ok: false, detail: 'Stay never reached' });
      } else {
        await page.keyboard.press('Enter');
        await page.waitForTimeout(400);
        // Scoped by accessible name on purpose: the always-mounted assistant
        // dock also carries `role="dialog"`, so a bare `[role="dialog"]` query
        // is never false and would report the leave dialog as still open.
        const afterStay = await page.evaluate(() => ({
          activeTag: document.activeElement?.tagName ?? null,
          activeText: (document.activeElement?.textContent ?? '').trim(),
          activeHref: document.activeElement?.getAttribute('href') ?? null,
          workers: document.querySelector('#capacity-workers')?.value ?? null,
          cap: document.querySelector('#capacity-cap')?.value ?? null,
          reason: document.querySelector('#capacity-reason')?.value ?? null,
          ack: document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null,
          dialogOpen: document.querySelector('[role="dialog"][aria-label="discard capacity draft confirmation"]') !== null,
          url: location.href,
        }));
        records.push({
          op: 'stay',
          urlBefore,
          ...afterStay,
          ok: afterStay.dialogOpen === false
            // Route unchanged by Stay.
            && urlBefore.endsWith('/settings/daemon-capacity')
            && afterStay.url.endsWith('/settings/daemon-capacity')
            // Draft retained byte-identically, acknowledgment included.
            && afterStay.workers === '5'
            && afterStay.cap === '12'
            && afterStay.reason === 'retained exact draft'
            && afterStay.ack === true
            // Focus returned to the navigation trigger, not lost to <body>.
            && afterStay.activeTag === 'A'
            && afterStay.activeText === 'Organization',
        });
      }
      // Reopen and confirm departure really completes.
      const link = page.getByRole('link', { name: 'Organization', exact: true });
      await link.focus();
      await page.keyboard.press('Enter');
      await page.locator(LEAVE_DIALOG).waitFor({ timeout: 15000 });
      await page.waitForTimeout(200);
      const leave = await tabTo(page, { text: 'Discard and continue' });
      if (!leave) {
        records.push({ op: 'confirmed-departure', ok: false, detail: 'Discard and continue never reached' });
        return records;
      }
      await page.keyboard.press('Enter');
      let departed = false;
      try {
        await page.waitForURL('**/settings/organization', { timeout: 15000 });
        departed = true;
      } catch { departed = false; }
      records.push({
        op: 'confirmed-departure', urlAfter: page.url(), departed, ok: departed,
      });
      return records;
    },
  },
];

/**
 * The FINITE required-operation inventory, declared independently of the
 * scenarios and of anything recorded. Every entry is required at every
 * viewport and theme; the gate fails a MISSING entry exactly like a failed
 * one, so deleting a scenario or its `operate` callback cannot quietly delete
 * its expectation too.
 */
const REQUIRED_OPERATIONS = [
  { scenario: 'ordinary', op: 'details-open' },
  { scenario: 'override-ack', op: 'ack-enables-save' },
  { scenario: 'unknown-check', op: 'check-usable' },
  { scenario: 'unknown-check', op: 'check-rebase-manual-save' },
  { scenario: 'conflict-rebase', op: 'rebase' },
  { scenario: 'conflict-accept', op: 'accept-latest' },
  { scenario: 'conflict-accept', op: 'accept-latest-then-manual-save' },
  { scenario: 'leave-dialog', op: 'stay' },
  { scenario: 'leave-dialog', op: 'confirmed-departure' },
];
/** Walks required at every viewport and theme — also independent of the scenario list. */
const REQUIRED_WALKS = [
  'ordinary', 'override-ack', 'unknown-check', 'conflict-rebase', 'conflict-accept', 'leave-dialog',
];
/** The leave dialog's text that MUST be contrast-sampled while it is open. */
const REQUIRED_DIALOG_SAMPLES = [
  { key: 'title', match: (t) => t === 'Discard unsaved capacity changes?' },
  { key: 'description', match: (t) => t.startsWith('Your draft has not been saved.') },
  { key: 'stay', match: (t) => t === 'Stay on page', control: true },
  { key: 'discard', match: (t) => t === 'Discard and continue', control: true },
];
const TONE_PHASES = ['rest', 'hover', 'active'];

/**
 * THE acceptance gate, as a pure function of the manifest.
 *
 * It is the one the run executes and the one `--gate-selftest` executes, so a
 * negative control is exercised against the shipped predicate rather than a
 * re-implementation of it. Two properties matter:
 *
 *   1. Coverage is computed from the DECLARED inventories
 *      (`REQUIRED_OPERATIONS`, `REQUIRED_WALKS`, `REQUIRED_DIALOG_SAMPLES`)
 *      crossed with the viewport/theme matrix — never from what happens to be
 *      recorded. An operation that was never attempted therefore fails the run
 *      instead of contributing nothing, which is exactly how twenty recorded
 *      operations used to stand in for a complete control set.
 *   2. Where an operation has a pure verdict, the gate RECOMPUTES it from the
 *      recorded evidence, so a corrupted or weakened record cannot pass on a
 *      stale `ok`.
 */
function evaluateGate(manifest, scope = {}) {
  const partialRun = Boolean(scope.partialRun ?? manifest.partialRun);
  const viewports = scope.viewports ?? ALL_VIEWPORTS.map((v) => v.name);
  const themes = scope.themes ?? ALL_THEMES;
  const scenarios = scope.scenarios ?? KEYBOARD_SCENARIOS.map((sc) => sc.name);
  const combos = viewports.flatMap((viewport) => themes.map((theme) => ({ viewport, theme })));

  // --- required operations -------------------------------------------------
  const operations = manifest.keyboardOperations ?? [];
  const expectedOperations = REQUIRED_OPERATIONS
    .filter((e) => scenarios.includes(e.scenario))
    .flatMap((e) => combos.map((c) => ({ ...e, ...c })));
  const recorded = (e) => operations.find((o) => o.scenario === e.scenario && o.op === e.op
    && o.viewport === e.viewport && o.theme === e.theme) ?? null;
  const requiredOperationsMissing = expectedOperations.filter((e) => recorded(e) === null);
  const operationFailures = [];
  for (const e of expectedOperations) {
    const record = recorded(e);
    if (record === null) continue;
    const verdict = OPERATION_VERDICTS[e.op] ? OPERATION_VERDICTS[e.op](record) : null;
    if (record.ok !== true || (verdict !== null && verdict.ok !== true)) {
      operationFailures.push({
        scenario: e.scenario,
        op: e.op,
        viewport: e.viewport,
        theme: e.theme,
        recordedOk: record.ok ?? null,
        detail: record.detail ?? null,
        recomputed: verdict?.fail ?? null,
      });
    }
  }
  // A recorded operation the inventory does not declare — a renamed operation
  // or a thrown one — is reported by exact identity rather than ignored.
  const undeclaredOperations = operations
    .filter((o) => !REQUIRED_OPERATIONS.some((e) => e.scenario === o.scenario && e.op === o.op))
    .map((o) => ({ scenario: o.scenario, op: o.op, viewport: o.viewport, theme: o.theme, detail: o.detail ?? null }));

  // --- walks ---------------------------------------------------------------
  const tabOrders = manifest.tabOrders ?? [];
  const keyboardCoverageGaps = REQUIRED_WALKS
    .filter((name) => scenarios.includes(name))
    .flatMap((name) => combos.map((c) => {
      const walk = tabOrders.find((t) => t.scenario === name
        && t.viewport === c.viewport && t.theme === c.theme);
      return (walk && (walk.order ?? []).length > 0)
        ? null
        : { scenario: name, ...c, reason: 'no keyboard walk recorded' };
    }))
    .filter(Boolean);
  const keyboardControlGaps = tabOrders
    .filter((t) => (t.missing ?? []).length > 0)
    .map((t) => ({ scenario: t.scenario, viewport: t.viewport, theme: t.theme, missing: t.missing }));
  const case192 = tabOrders.filter((t) => t.scenario === 'ordinary' && t.viewport === '1440x1000'
    && (t.order ?? []).length > 0 && (t.missing ?? []).length === 0);

  // --- the ACTUAL dialog: text samples and the enabled sibling's tone -------
  const dialogMeasurements = manifest.dialogMeasurements ?? [];
  const dialogSampleGaps = [];
  const dialogContrastFindings = [];
  const dialogToneFindings = [];
  const dialogProbeSideEffects = [];
  for (const c of combos) {
    const m = dialogMeasurements.find((d) => d.viewport === c.viewport && d.theme === c.theme);
    if (!m) {
      dialogSampleGaps.push({ ...c, reason: 'the leave dialog was never measured' });
      continue;
    }
    for (const want of REQUIRED_DIALOG_SAMPLES) {
      const hits = (m.samples ?? []).filter((s) => s.present && s.inDialog
        && typeof s.text === 'string' && want.match(s.text.trim()));
      if (hits.length === 0) {
        dialogSampleGaps.push({ ...c, sample: want.key, reason: 'not sampled while the dialog was open' });
        continue;
      }
      for (const hit of hits) {
        if (hit.gated !== true || hit.unmeasurable !== undefined || hit.passesAA !== true) {
          dialogContrastFindings.push({
            ...c, sample: want.key, text: hit.text, gated: hit.gated ?? null,
            ratio: hit.ratio ?? null, threshold: hit.threshold ?? null,
            unmeasurable: hit.unmeasurable ?? null,
          });
        }
      }
    }
    const phases = m.tone?.phases ?? [];
    for (const phase of TONE_PHASES) {
      const got = phases.find((ph) => ph.phase === phase) ?? null;
      if (got === null) {
        dialogSampleGaps.push({ ...c, sample: `tone:${phase}`, reason: 'phase not measured' });
        continue;
      }
      // The measured sibling must be the ENABLED "Discard and continue" — an
      // inactive or mis-selected control is not evidence for it.
      if (got.present !== true || got.disabled !== false
        || got.text !== 'Discard and continue'
        || got.unmeasurable !== undefined || got.passesAA !== true
        || typeof got.ratio !== 'number') {
        dialogToneFindings.push({
          ...c, phase, present: got.present ?? null, disabled: got.disabled ?? null,
          text: got.text ?? null, ratio: got.ratio ?? null, threshold: got.threshold ?? null,
        });
      }
    }
    if ((m.requestsDuringProbe ?? 0) !== 0 || m.urlBefore !== m.urlAfter
      || m.dialogStillOpen !== true) {
      dialogProbeSideEffects.push({
        ...c, requestsDuringProbe: m.requestsDuringProbe ?? null,
        urlBefore: m.urlBefore ?? null, urlAfter: m.urlAfter ?? null,
        dialogStillOpen: m.dialogStillOpen ?? null,
      });
    }
  }

  // --- Check semantics: the failed-reread negative control ------------------
  const checkControls = manifest.checkControls ?? [];
  const checkControlFailures = [
    ...(checkControls.length === 0
      ? [{ control: 'failed-reread', reason: 'the negative control was not run' }]
      : []),
    ...checkControls.filter((c) => c.ok !== true).map((c) => ({
      control: c.control, viewport: c.viewport, theme: c.theme,
      expectUsable: c.expectUsable ?? null, verdict: c.verdict ?? null,
    })),
  ];

  // --- C1: identical successful read, then a failed one --------------------
  const c1Sequences = manifest.c1Sequences ?? [];
  const c1States = ['identical-refresh-retained', 'identical-refresh-recovered']
    .filter((name) => !partialRun || (manifest.statesCaptured ?? []).includes(name));
  const c1Gaps = [];
  const c1Failures = [];
  for (const state of c1States) {
    for (const c of combos) {
      const record = c1Sequences.find((r) => r.state === state
        && r.viewport === c.viewport && r.theme === c.theme) ?? null;
      if (record === null) {
        c1Gaps.push({ state, ...c, reason: 'no C1 retained-receipt sequence recorded' });
        continue;
      }
      const verdict = c1Verdict(record);
      if (!verdict.ok) c1Failures.push({ state, ...c, fail: verdict.fail });
    }
  }

  // --- 16.10 primary action, derived gate inputs ---------------------------
  const primaryActionTone = manifest.primaryActionTone ?? [];
  const primaryToneFindings = primaryActionTone.flatMap((t) => (t.present
    ? (t.phases ?? [])
      .filter((ph) => ph.present !== true || ph.unmeasurable !== undefined
        || ph.disabled === true || ph.passesAA !== true)
      .map((ph) => ({
        state: t.state, viewport: t.viewport, theme: t.theme, phase: ph.phase,
        disabled: ph.disabled ?? null, color: ph.color ?? null,
        background: ph.backgroundColor ?? null, ratio: ph.ratio ?? null,
        threshold: ph.threshold ?? null, unmeasurable: ph.unmeasurable ?? null,
      }))
    : [{ state: t.state, viewport: t.viewport, theme: t.theme, phase: 'all', missing: t.selector }]));
  const primaryToneCoverageGaps = partialRun ? [] : combos.map((c) => {
    const got = primaryActionTone.filter((t) => t.viewport === c.viewport && t.theme === c.theme
      && t.present && (t.phases ?? []).length === 3
      && t.phases.every((ph) => ph.present === true && ph.disabled === false
        && typeof ph.ratio === 'number'));
    return got.length === 0
      ? { ...c, reason: 'no enabled 3-phase primary-action tone reading' }
      : null;
  }).filter(Boolean);
  const primaryToneProbeWrites = primaryActionTone
    .filter((t) => (t.requestsDuringProbe ?? 0) !== 0)
    .map((t) => ({
      state: t.state, viewport: t.viewport, theme: t.theme,
      requestsDuringProbe: t.requestsDuringProbe,
    }));

  return [
    ['undeclared /api/ paths (fail-closed venue)', manifest.undeclaredApiPaths ?? []],
    ['horizontal overflow', manifest.horizontalOverflowStates ?? []],
    ['low-contrast text (16.10)', manifest.lowContrastFindings ?? []],
    ['undeclared contrast exemptions', manifest.undeclaredContrastExemptions ?? []],
    // The exemption list is empty now; a non-empty one would mean a sample was
    // moved out of the gated set, which this leg is not allowed to do.
    ['declared contrast exemptions (must be none)', DECLARED_CONTRAST_EXEMPTIONS],
    ['low-contrast primary-action states (16.10)', primaryToneFindings],
    ['primary-action tone coverage gaps (16.10)', primaryToneCoverageGaps],
    ['primary-action tone probe wrote through the surface', primaryToneProbeWrites],
    // 19.2 is asserted by its ABSENCE of a result, so the gate entry is
    // inverted: a missing 1440x1000 keyboard pass is the failure.
    ['19.2 keyboard pass at 1440x1000 missing',
      (!partialRun && case192.length === 0) ? ['no 1440x1000 tab-order walk'] : []],
    ['controls without a visible focus ring (16.11)', manifest.focusRingsMissing ?? []],
    ['low-contrast focus rings (16.10)', manifest.lowContrastFocusRings ?? []],
    ['required keyboard controls never reached (16.6/16.11)', keyboardControlGaps],
    ['keyboard scenario coverage gaps (16.11)', keyboardCoverageGaps],
    ['required keyboard operations never performed (16.6/16.7)', requiredOperationsMissing],
    ['keyboard operations that did not take effect (16.6/16.7)', operationFailures],
    ['keyboard operations outside the declared inventory', undeclaredOperations],
    ['leave-dialog samples missing (16.10)', dialogSampleGaps],
    ['low-contrast leave-dialog text (16.10)', dialogContrastFindings],
    ['enabled dialog primary-action tone (16.10)', dialogToneFindings],
    ['the dialog tone probe wrote through or navigated the surface', dialogProbeSideEffects],
    ['Check-semantics negative control (10.x/11.x)', checkControlFailures],
    ['C1 retained-receipt sequences missing (2.9/14.3)', c1Gaps],
    ['C1 retained receipt is not the identical response’s own (2.9/14.3)', c1Failures],
    ['clipping-predicate control failures (16.8)', manifest.clippingControlFailures ?? []],
    ['computed visibility failures (16.8)', manifest.visibilityFailures ?? []],
    ['declared capture subjects outside the captured frame (19.1/14.1-14.3)',
      manifest.subjectsNotFramed ?? []],
    ['incoherent post-save surface (R8)', manifest.incoherentPostSaveStates ?? []],
  ].filter(([, findings]) => findings.length > 0);
}

/**
 * `--gate-selftest <MANIFEST.json>` — prove the gate above, not a copy of it.
 *
 * The unchanged manifest of a full run must PASS. Each finite corruption below
 * must FAIL it, with the named criterion among the failures. This is how a
 * missing required operation, a failed one, a missing walk, a missing dialog
 * sample and a weakened Check assertion are demonstrated to be fatal rather
 * than merely reported.
 */
function runGateSelftest(path) {
  const original = JSON.parse(readFileSync(path, 'utf8'));
  const clone = () => JSON.parse(JSON.stringify(original));
  const drop = (list, pred) => list.filter((x) => !pred(x));
  const cases = [
    { name: 'original (must PASS)', expect: null, mutate: (m) => m },
    {
      name: 'missing ALL operations',
      expect: 'required keyboard operations never performed',
      mutate: (m) => { m.keyboardOperations = []; return m; },
    },
    {
      name: 'missing ONE required operation (accept-latest @ 1280x900/dark)',
      expect: 'required keyboard operations never performed',
      mutate: (m) => {
        m.keyboardOperations = drop(m.keyboardOperations, (o) => o.op === 'accept-latest'
          && o.viewport === '1280x900' && o.theme === 'dark');
        return m;
      },
    },
    {
      name: 'a scenario’s operate callback removed (conflict-accept)',
      expect: 'required keyboard operations never performed',
      mutate: (m) => {
        m.keyboardOperations = drop(m.keyboardOperations, (o) => o.scenario === 'conflict-accept');
        return m;
      },
    },
    {
      name: 'a failed operation',
      expect: 'keyboard operations that did not take effect',
      mutate: (m) => { m.keyboardOperations[0].ok = false; return m; },
    },
    {
      name: 'a weakened Check assertion (the error string changed)',
      expect: 'keyboard operations that did not take effect',
      mutate: (m) => {
        const op = m.keyboardOperations.find((o) => o.op === 'check-usable');
        op.evidence.ui.headline = 'Could not refresh. Current state unverified.';
        return m;
      },
    },
    {
      name: 'a missing walk (ordinary @ 1440x1000/light)',
      expect: 'keyboard scenario coverage gaps',
      mutate: (m) => {
        m.tabOrders = drop(m.tabOrders, (t) => t.scenario === 'ordinary'
          && t.viewport === '1440x1000' && t.theme === 'light');
        return m;
      },
    },
    {
      name: 'a missing dialog text sample (Discard and continue)',
      expect: 'leave-dialog samples missing',
      mutate: (m) => {
        m.dialogMeasurements[0].samples = drop(
          m.dialogMeasurements[0].samples,
          (s) => (s.text ?? '').trim() === 'Discard and continue',
        );
        return m;
      },
    },
    {
      name: 'a missing dialog tone phase (hover)',
      expect: 'leave-dialog samples missing',
      mutate: (m) => {
        m.dialogMeasurements[0].tone.phases = drop(
          m.dialogMeasurements[0].tone.phases, (ph) => ph.phase === 'hover',
        );
        return m;
      },
    },
    {
      name: 'a failed dialog text sample',
      expect: 'low-contrast leave-dialog text',
      mutate: (m) => {
        const sample = m.dialogMeasurements[0].samples
          .find((s) => (s.text ?? '').trim() === 'Stay on page');
        sample.passesAA = false;
        return m;
      },
    },
    {
      name: 'the tone probe navigated away',
      expect: 'the dialog tone probe wrote through or navigated',
      mutate: (m) => { m.dialogMeasurements[0].dialogStillOpen = false; return m; },
    },
    {
      name: 'the failed-reread Check control is missing',
      expect: 'Check-semantics negative control',
      mutate: (m) => { m.checkControls = []; return m; },
    },
    {
      name: 'C1 retains the OLD (t0) receipt after the failed read',
      expect: 'C1 retained receipt is not the identical response',
      mutate: (m) => {
        const rec = m.c1Sequences[0];
        rec.steps.step2.receipts = [receiptText(C1_TIMES.t0), receiptText(C1_TIMES.t0)];
        rec.steps.step2.bannerText = `Could not refresh. Current state unverified. ${receiptText(C1_TIMES.t0)}`;
        return m;
      },
    },
    {
      name: 'a C1 sequence is missing',
      expect: 'C1 retained-receipt sequences missing',
      mutate: (m) => { m.c1Sequences = drop(m.c1Sequences, (r) => r.viewport === '1280x900'); return m; },
    },
  ];
  let bad = 0;
  for (const c of cases) {
    const failures = evaluateGate(c.mutate(clone()), { partialRun: false });
    const labels = failures.map(([label]) => label);
    const ok = c.expect === null
      ? failures.length === 0
      : labels.some((label) => label.includes(c.expect));
    if (!ok) bad += 1;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}`);
    console.log(`      gate: ${failures.length === 0 ? 'clean' : labels.map((l, i) => `${l} (${failures[i][1].length})`).join('; ')}`);
  }
  // Pure-predicate controls for the two recomputed verdicts, so a weakened
  // assertion is caught even where the gate has no record to corrupt.
  const checkRecord = (original.keyboardOperations ?? []).find((o) => o.op === 'check-usable');
  if (checkRecord) {
    const weakened = JSON.parse(JSON.stringify(checkRecord));
    weakened.evidence.ui.savedClaim = true;
    const verdict = checkUsableVerdict(weakened.evidence);
    const ok = verdict.ok === false;
    if (!ok) bad += 1;
    console.log(`${ok ? 'PASS' : 'FAIL'}  checkUsableVerdict rejects a "saved" claim`);
  } else {
    bad += 1;
    console.log('FAIL  no check-usable record to exercise the verdict against');
  }
  console.log(bad === 0
    ? 'GATE SELF-TEST PASS: the shipped gate fails every negative control and passes the original.'
    : `GATE SELF-TEST FAIL: ${bad} control(s) did not behave as required.`);
  return bad === 0 ? 0 : 1;
}

/**
 * 16.10 — computed contrast in a REAL browser.
 *
 * WCAG 2.x relative luminance and contrast ratio, measured against the element's
 * EFFECTIVE background (the nearest ancestor with a non-transparent
 * background-color), not an assumed page colour. Reported per state, viewport
 * and theme; nothing here is asserted from a design token or a DOM presence.
 */
/**
 * The ONE computed-visibility predicate, installed on the page.
 *
 * It used to be defined inside the per-state receipt's `evaluate` closure,
 * which meant a self-test could only ever check a COPY of it. It is installed
 * here instead, and both the per-state receipt and the clipping controls below
 * read it off `window`, so the controls exercise the exact function the
 * acceptance gate calls. A missing install throws rather than silently
 * degrading to "everything is visible".
 */
const INSTALL_VISIBILITY = () => {
  /**
   * Computed visibility INCLUDING ancestor clipping.
   *
   * A `<details>` drawer, an `overflow:hidden` container or a
   * zero-height ancestor keeps its children in the DOM and in the box
   * model while hiding them. Each ancestor's own clip rect is
   * intersected with the node's, so an element clipped out of view
   * reports invisible rather than "present".
   */
  const visible = (node) => {
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (!(rect.width > 0 && rect.height > 0)) return false;
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (!(Number(style.opacity) > 0)) return false;
    let el = node.parentElement;
    while (el) {
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none') return false;
      if (Number(s.opacity) === 0) return false;
      if (el.tagName === 'DETAILS' && !el.open) return false;
      // A GENUINE clip only. `overflow:auto|scroll` content that is
      // merely below the current scroll position is reachable and is
      // NOT a visibility failure; treating it as one turns every page
      // longer than the viewport into a false finding.
      //
      // `hidden` and `clip` ARE the genuine clips, and the clip-rect
      // test below has to run no matter how much content overflows.
      // This predicate used to add `scrollHeight <= clientHeight + 1`
      // to each condition, i.e. it only looked for a clip when nothing
      // was overflowing — which is precisely when there is nothing to
      // clip. Overflowing clipped content skipped the check entirely
      // and was reported VISIBLE. Exact counterexample, reproduced in
      // Chrome by `clipping-controls.mjs` alongside this file:
      // `display:flow-root; overflow:clip; height:1px`, warning at
      // y38-74, ancestor at y8-9, scrollHeight 66 / clientHeight 1 —
      // reportedVisible true while entirely outside the clip.
      //
      // Content that is genuinely REACHABLE is still protected, by
      // `visibleInView` below: it calls `scrollIntoView` first, which
      // scrolls an `overflow:hidden` container to reveal its content,
      // so the only thing that fails here is a node still lying
      // entirely outside its clipping ancestor's box afterwards.
      // `overflow:clip` cannot be scrolled at all, which is why the
      // counterexample above stays outside.
      const clipsY = s.overflowY === 'hidden' || s.overflowY === 'clip';
      const clipsX = s.overflowX === 'hidden' || s.overflowX === 'clip';
      if ((clipsX || clipsY) && (el.clientWidth === 0 || el.clientHeight === 0)) return false;
      const clip = el.getBoundingClientRect();
      if (clipsY && !(rect.bottom > clip.top && rect.top < clip.bottom)) return false;
      if (clipsX && !(rect.right > clip.left && rect.left < clip.right)) return false;
      el = el.parentElement;
    }
    return true;
  };
  /**
   * Measure visibility with the element SCROLLED INTO VIEW.
   *
   * The shell's content area scrolls, so copy below the current scroll
   * position is reachable, not hidden. Measuring it where it happens to
   * sit turns every page longer than the viewport into a false
   * "clipped" finding. Scrolling first leaves only the failures the
   * accepted criterion is about: a COLLAPSED container (a closed
   * `<details>`), a zero-size box, `display`/`visibility`/`opacity`
   * hiding, or content a non-scrollable `overflow:hidden` ancestor
   * genuinely clips away.
   */
  const visibleInView = (node) => {
    if (!node) return null;
    node.scrollIntoView({ block: 'center', inline: 'nearest' });
    return visible(node);
  };

  window.__capacityVisible = visible;
  window.__capacityVisibleInView = visibleInView;
};

const CONTRAST_FN = () => {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  // This app's theme is authored in `oklch()`, and Chrome returns the computed
  // value in that space — an `rgba(...)` regex parses NOTHING here. Resolve any
  // CSS colour through a canvas so the measurement is on real sRGB pixels.
  const ctx2d = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  ctx2d.globalCompositeOperation = 'copy';
  const parse = (value) => {
    if (!value || value === 'none') return null;
    ctx2d.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx2d.fillStyle = value;
    ctx2d.fillRect(0, 0, 1, 1);
    const d = ctx2d.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const effectiveBackground = (node) => {
    let el = node;
    while (el) {
      const bg = parse(getComputedStyle(el).backgroundColor);
      if (bg && bg.a > 0) return bg;
      el = el.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) ?? { r: 255, g: 255, b: 255, a: 1 };
  };
  // EVERY text-bearing element inside the capacity panel — helper, guidance,
  // error, warning, acknowledgment, reconciliation and result copy alike —
  // rather than a hand-picked selector list that can silently omit the very
  // state under test.
  //
  // The leave-confirmation dialog is a PORTAL: Radix renders it as a sibling
  // of the app root, not inside the capacity wrapper, so scoping to the panel
  // alone silently dropped every string and control in it out of the rollups.
  // That is exactly why its focus failure was invisible to this gate. It is a
  // capacity-owned surface, so it is measured with the rest of the panel.
  const roots = [
    document.querySelector('#capacity-panel-heading')?.closest('div'),
    document.querySelector('[role="dialog"][aria-label="discard capacity draft confirmation"]'),
  ].filter((node) => node !== null && node !== undefined);
  const candidates = roots.flatMap((panel) => [...panel.querySelectorAll('*')].filter((node) => {
    if (node.matches('script, style, svg, path, canvas')) return false;
    const own = [...node.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent ?? '')
      .join('')
      .trim();
    if (own.length === 0) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }));

  /**
   * Accepted 16.10, as extended by the review AND by the manager's TASK-8562
   * correction: EVERY capacity helper, error, warning, acknowledgment,
   * reconciliation, status and control string is gated. A shared token's
   * ORIGIN does not put a capacity requirement out of scope — capacity-local
   * class use of an existing darker token is authorized and is how BOTH the
   * muted copy and, in TASK-8564, the primary-action label were repaired.
   *
   * The list is now EMPTY. The one exception that used to live here,
   * `shared-button-primitive-accent-label`, claimed that the white-on-accent
   * button label could only reach AA by editing the shared primitive or the
   * shared token definitions. That was wrong: the call site can select darker
   * EXISTING accent tokens through `className`, which is what
   * `PRIMARY_TONE` in `DaemonCapacitySection.tsx` now does. With no exemption
   * left, every measured sample below is gated.
   *
   * The machinery is deliberately kept rather than deleted: an id emitted by
   * this function that the module-level declaration does not carry still FAILS
   * the run, so a future exemption cannot be introduced silently.
   */
  const CONTRAST_EXEMPTIONS = [];
  const exemptionFor = (el) => CONTRAST_EXEMPTIONS.find(
    (e) => el.matches(e.match) || el.closest(e.match) !== null,
  ) ?? null;

  /**
   * Honest active/inactive classification.
   *
   * A disabled control is painted through `disabled:opacity-50`, so what a
   * person actually sees is dimmer than the authored colour measured here.
   * WCAG 1.4.3 places no contrast requirement on an inactive component, and
   * this run does NOT relax anything for one — every sample stays gated at its
   * authored colour. The flag exists so an inactive, dimmed label can never be
   * read as evidence that the ENABLED label passes: the enabled label's real
   * resting / hover / active evidence is `primaryActionTone`, measured with a
   * real pointer.
   */
  const INACTIVE_HOSTS = 'button[disabled], input[disabled], select[disabled], '
    + 'textarea[disabled], fieldset[disabled], [aria-disabled="true"]';
  const cumulativeOpacity = (el) => {
    let o = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const v = Number(getComputedStyle(n).opacity);
      if (Number.isFinite(v)) o *= v;
    }
    return Number(o.toFixed(3));
  };

  const out = [];
  for (const el of candidates) {
    const selector = el.id
      ? `#${el.id}`
      : `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(/\s+/).slice(0, 2).join('.')}`;
    const style = getComputedStyle(el);
    const exempt = exemptionFor(el);
    const inactiveHost = el.closest(INACTIVE_HOSTS);
    const isControlLabel = el.closest('button, [role="button"]') !== null;
    // Which capacity-owned root this sample belongs to. The leave dialog is
    // PORTALLED, so its samples need their own identity in the rollups rather
    // than being indistinguishable from panel text.
    const inDialog = el.closest('[role="dialog"][aria-label="discard capacity draft confirmation"]') !== null;
    const fg = parse(style.color);
    const bg = effectiveBackground(el);
    if (!fg || !bg) {
      out.push({
        selector, present: true, unmeasurable: style.color, gated: exempt === null,
        inactive: inactiveHost !== null, isControlLabel, inDialog,
        text: (el.textContent ?? '').trim().slice(0, 60),
      });
      continue;
    }
    // A fully transparent foreground paints nothing and is not a contrast
    // finding; opacity-reduced text IS composited before measuring.
    if (fg.a === 0) continue;
    const composited = fg.a < 1
      ? { r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 }
      : fg;
    const px = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    // WCAG "large text": >=24px, or >=18.66px when bold.
    const large = px >= 24 || (bold && px >= 18.66);
    out.push({
      selector,
      gated: exempt === null,
      exemptionId: exempt?.id ?? null,
      exemptionReason: exempt?.reason ?? null,
      // Honest classification, NOT a relaxation — see INACTIVE_HOSTS above.
      // `ratio` below is always the AUTHORED colour; `cumulativeOpacity`
      // records how much of it actually reaches the screen.
      inactive: inactiveHost !== null,
      inactiveHost: inactiveHost
        ? `${inactiveHost.tagName.toLowerCase()}${inactiveHost.id ? `#${inactiveHost.id}` : ''}`
        : null,
      cumulativeOpacity: cumulativeOpacity(el),
      isControlLabel,
      inDialog,
      present: true,
      text: (el.textContent ?? '').trim().slice(0, 60),
      color: style.color,
      background: `rgb(${bg.r}, ${bg.g}, ${bg.b})`,
      fontSizePx: px,
      large,
      ratio: Number(ratio(composited, bg).toFixed(2)),
      threshold: large ? 3 : 4.5,
      passesAA: ratio(composited, bg) >= (large ? 3 : 4.5),
    });
  }
  return out;
};

/**
 * The exception inventory the RUN must be judged against, declared OUTSIDE the
 * page so it is visible in the manifest even when an exception is not observed
 * in a particular state. `CONTRAST_FN` carries the enforcing copy; this list is
 * reconciled against the ids it actually emitted, and a drift between the two
 * FAILS the run rather than quietly widening the waiver.
 *
 * EMPTY as of TASK-8564. `shared-button-primitive-accent-label` was removed
 * because the control label it covered now meets the criterion through a
 * capacity-local existing-token override; nothing replaced it, and no sample
 * moved into another reported-but-ungated bucket.
 */
const DECLARED_CONTRAST_EXEMPTIONS = [];

/**
 * 16.10 for the ENABLED primary action, in its REAL interaction states.
 *
 * A resting-only reading is not enough here. The Button variant's hover and
 * active steps are a DIFFERENT colour from its resting one, and the shared
 * default (`hover:bg-primary/90`) composites lighter than the resting fill, so
 * the hover state can fail a criterion the resting state passes. This probe
 * therefore drives a real pointer and reads the computed colours back out of
 * the browser in each phase.
 *
 * Three things keep it from being vacuous:
 *   - it records `disabled`, so an inactive control cannot be reported as
 *     enabled-label evidence, and the gate requires a real ENABLED reading at
 *     every viewport/theme;
 *   - `transition-colors` animates the fill, so each phase settles before it
 *     is read — a mid-transition sample would report an interpolated colour;
 *   - the button is released OFF-target and the request log is compared before
 *     and after, so the probe cannot fire the form submit it is hovering.
 */
const TONE_SAMPLE = (selector) => {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const ctx2d = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  ctx2d.globalCompositeOperation = 'copy';
  const parse = (value) => {
    if (!value || value === 'none') return null;
    ctx2d.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx2d.fillStyle = value;
    ctx2d.fillRect(0, 0, 1, 1);
    const d = ctx2d.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const el = document.querySelector(selector);
  if (!el) return { present: false };
  const style = getComputedStyle(el);
  const fg = parse(style.color);
  let bg = parse(style.backgroundColor);
  // A variant whose fill carries alpha (the shared `bg-primary/90` hover) is
  // composited over what is actually behind it, not over an assumed canvas.
  if (bg && bg.a < 1) {
    let behind = null;
    for (let n = el.parentElement; n && !behind; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) behind = c;
    }
    behind = behind ?? parse(getComputedStyle(document.body).backgroundColor)
      ?? { r: 255, g: 255, b: 255, a: 1 };
    bg = {
      r: bg.r * bg.a + behind.r * (1 - bg.a),
      g: bg.g * bg.a + behind.g * (1 - bg.a),
      b: bg.b * bg.a + behind.b * (1 - bg.a),
      a: 1,
    };
  }
  if (!fg || !bg) return { present: true, unmeasurable: `${style.color} / ${style.backgroundColor}` };
  const px = parseFloat(style.fontSize);
  const bold = Number(style.fontWeight) >= 700;
  const large = px >= 24 || (bold && px >= 18.66);
  return {
    present: true,
    disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
    text: (el.textContent ?? '').trim().slice(0, 60),
    classList: [...el.classList].join(' '),
    color: style.color,
    backgroundColor: style.backgroundColor,
    fontSizePx: px,
    large,
    ratio: Number(ratio(fg, bg).toFixed(2)),
    threshold: large ? 3 : 4.5,
    passesAA: ratio(fg, bg) >= (large ? 3 : 4.5),
  };
};

/** `transition-colors` is 150ms in this system; settle well past it. */
const TONE_SETTLE_MS = 450;

async function measurePrimaryActionTone(page, selector) {
  const handle = await page.$(selector);
  if (handle === null) return { selector, present: false, phases: [] };
  const read = async (phase) => ({ phase, ...(await page.evaluate(TONE_SAMPLE, selector)) });
  // Park the pointer somewhere harmless first, so "rest" really is rest.
  await page.mouse.move(2, 2);
  await page.waitForTimeout(TONE_SETTLE_MS);
  const rest = await read('rest');
  await handle.hover();
  await page.waitForTimeout(TONE_SETTLE_MS);
  const hover = await read('hover');
  await page.mouse.down();
  await page.waitForTimeout(TONE_SETTLE_MS);
  const active = await read('active');
  // Release AWAY from the control: a mouseup over the button would be a click,
  // which on a submit button would fire a write this state never declared.
  await page.mouse.move(2, 2);
  await page.mouse.up();
  await page.waitForTimeout(TONE_SETTLE_MS);
  return { selector, present: true, phases: [rest, hover, active] };
}

/**
 * 16.11 — walk the 16.6 tab order and measure the REAL focus ring.
 *
 * Two corrections a first pass got wrong, both of which produced a vacuous
 * "every control has a ring":
 *
 *   1. `page.focus()` is PROGRAMMATIC focus. Chrome does not match
 *      `:focus-visible` for it, so the ring variables stay at their
 *      transparent defaults and nothing is measured. The walk therefore starts
 *      at `document.body` and Tabs in, so every reading is keyboard-driven.
 *   2. This design system draws the ring with `box-shadow`, and an UNFOCUSED
 *      control still reports `box-shadow: rgba(0,0,0,0) 0 0 0 0`. Treating
 *      `boxShadow !== 'none'` as a visible ring scores the absent ring as
 *      present. A layer counts only when it has non-zero alpha AND non-zero
 *      spread or blur.
 */
const RING_PROBE = () => {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const ctx2d = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  ctx2d.globalCompositeOperation = 'copy';
  const parse = (value) => {
    if (!value || value === 'none') return null;
    ctx2d.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx2d.fillStyle = value;
    ctx2d.fillRect(0, 0, 1, 1);
    const d = ctx2d.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  /** Composite a translucent ring over what is actually behind it. */
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  // Split a computed box-shadow into layers without breaking on rgb() commas.
  const layers = (value) => {
    if (!value || value === 'none') return [];
    const out = [];
    let depth = 0; let cur = '';
    for (const ch of value) {
      if (ch === '(') depth += 1;
      if (ch === ')') depth -= 1;
      if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  };

  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const style = getComputedStyle(el);
  // Is this control part of the bounded capacity surface?
  //
  // The tab ring continues into the app shell (theme toggle, sidebar links).
  // Those are PRE-EXISTING shared chrome this bounded leg does not own and may
  // not restyle, so gating them here would be scope expansion, not coverage.
  // They are still recorded and reported — just in the non-gated bucket, the
  // same split the contrast rollups already use. The capacity panel and its
  // PORTALLED dialog are both in scope.
  const capacityRoot = document.querySelector('#capacity-panel-heading')?.closest('div') ?? null;
  const dialogRoot = document.querySelector('[role="dialog"][aria-label="discard capacity draft confirmation"]');
  const inScope = Boolean((capacityRoot && capacityRoot.contains(el))
    || (dialogRoot && dialogRoot.contains(el)));
  let surround = el.parentElement;
  while (surround) {
    const bg = parse(getComputedStyle(surround).backgroundColor);
    if (bg && bg.a > 0) break;
    surround = surround.parentElement;
  }
  const surroundBg = parse(getComputedStyle(surround ?? document.body).backgroundColor)
    ?? { r: 255, g: 255, b: 255, a: 1 };

  const outlineWidth = parseFloat(style.outlineWidth) || 0;
  const outlineReal = outlineWidth > 0 && style.outlineStyle !== 'none';

  // A ring layer must actually paint: visible alpha AND a non-zero spread/blur.
  //
  // The colour is whatever remains once the lengths and `inset` are removed.
  // A regex that only knows `rgb()`/`#hex` silently fails on this theme's
  // `oklch()` values, scores the real ring as "not painting", and then reports
  // the decorative white offset ring as THE ring — a measurement that looks
  // like a finding but is an artefact of the probe.
  const ringLayers = layers(style.boxShadow).map((layer) => {
    const lengths = (layer.match(/-?[\d.]+px/g) ?? []).map(parseFloat);
    const colourText = layer.replace(/-?[\d.]+px/g, ' ').replace(/\binset\b/g, ' ').trim();
    const colour = parse(colourText);
    const paints = !!colour && colour.a > 0.05 && lengths.some((n) => Math.abs(n) > 0);
    return { layer, colour, colourText, paints };
  }).filter((l) => l.paints);

  const ringSource = ringLayers.length > 0
    ? ringLayers[ringLayers.length - 1].colour
    : (outlineReal ? parse(style.outlineColor) : null);

  // `opacity` composites the WHOLE element — its box-shadow ring included —
  // over whatever is behind it. A ring authored at full alpha on a control
  // carrying `opacity-70` reaches the screen at 0.7, and reading the authored
  // colour alone would score a ring nobody can actually see as passing. The
  // dialog's primitive-owned Close is exactly that case.
  let cumulativeOpacity = Number(style.opacity);
  for (let a = el.parentElement; a; a = a.parentElement) {
    cumulativeOpacity *= Number(getComputedStyle(a).opacity);
  }
  const painted = ringSource
    ? { ...ringSource, a: ringSource.a * cumulativeOpacity }
    : null;

  return {
    tag: el.tagName,
    inScope,
    id: el.id || null,
    // The acknowledgment checkbox has no id, no aria-label and no text of its
    // own (its label text is a sibling span), so the rollups need this to name
    // it at all. The dialog's Close is identified by its aria-label.
    type: el.getAttribute('type'),
    ariaLabel: el.getAttribute('aria-label'),
    text: (el.textContent ?? '').trim().slice(0, 40) || null,
    focusVisible: el.matches(':focus-visible'),
    outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
    boxShadow: style.boxShadow,
    paintedRingLayers: ringLayers.map((l) => l.layer),
    // The honest predicate: keyboard focus is actually matched AND something
    // actually paints.
    focusRingVisible: el.matches(':focus-visible') && (outlineReal || ringLayers.length > 0),
    cumulativeOpacity: Number(cumulativeOpacity.toFixed(4)),
    // The translucent ring composited over what is behind it, against that
    // same background — what a person actually has to see, AFTER the element's
    // own cumulative opacity has been applied.
    ringContrast: painted
      ? Number(ratio(over(painted, surroundBg), surroundBg).toFixed(2))
      : null,
    // The same measurement ignoring cumulative opacity, kept so a reviewer can
    // see how much of the authored ring the opacity is eating.
    ringContrastAuthored: ringSource
      ? Number(ratio(over(ringSource, surroundBg), surroundBg).toFixed(2))
      : null,
  };
};

/**
 * Let the focused control's transitions settle before the ring is read.
 *
 * The dialog's Close carries `transition-opacity`, so the frame immediately
 * after `Tab` still reports its RESTING opacity — the focus treatment is
 * mid-flight. Reading there scores the ring at the opacity it is leaving
 * rather than the one it lands on. This is the same reason `TONE_SETTLE_MS`
 * exists for the primary action's colour transition.
 */
const FOCUS_SETTLE_MS = 250;

/** Does a RING_PROBE reading identify the control this requirement names? */
function matchesControl(probe, want) {
  if (!probe) return false;
  if (want.id) return probe.id === want.id;
  if (want.ariaLabel) return probe.ariaLabel === want.ariaLabel;
  if (want.type) return probe.tag === 'INPUT' && probe.type === want.type;
  return (probe.text ?? '').startsWith(want.text);
}

/** How a control appears in a finding, whatever identifies it. */
const controlName = (probe) => probe.id
  ?? probe.ariaLabel
  ?? (probe.text || null)
  ?? (probe.type ? `${probe.tag.toLowerCase()}[type=${probe.type}]` : probe.tag);

/**
 * Tab in from the document start so focus is keyboard-DRIVEN (a programmatic
 * `.focus()` does not match `:focus-visible`, and a ring measured that way is
 * not evidence), stop on the named first control, then record a reading for
 * every control the tab ring reaches.
 *
 * Accepted 16.6/16.11 are about a CONTROL SET, not a fixed step count. The
 * previous version walked exactly seven steps from `#capacity-workers`, which
 * could only ever see the ordinary form: the acknowledgment, Check saved
 * values, Rebase, Accept latest and the dialog's own controls were never
 * reached, so their rings were never measured. The walk now runs until every
 * required control for the scenario has been seen, and reports by name any it
 * never reached — a MISSING control fails the run rather than shortening it.
 */
async function walkControls(page, { start, required, maxSteps = 40 }) {
  await page.evaluate(() => document.body.focus());
  let reached = false;
  for (let i = 0; i < 120 && !reached; i += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(
      (sel) => document.activeElement?.matches(sel) ?? false,
      start,
    );
  }
  if (!reached) throw new Error(`never tabbed to ${start}`);
  const order = [];
  const seen = new Set();
  for (let i = 0; i < maxSteps; i += 1) {
    await page.waitForTimeout(FOCUS_SETTLE_MS);
    const probe = await page.evaluate(RING_PROBE);
    if (probe) {
      order.push(probe);
      for (const want of required) {
        if (matchesControl(probe, want)) seen.add(want.key);
      }
    }
    if (seen.size === required.length) break;
    await page.keyboard.press('Tab');
  }
  return {
    order,
    missing: required.filter((want) => !seen.has(want.key)).map((want) => want.key),
  };
}

/** Keyboard-focus a control by walking the tab ring to it — never `.focus()`. */
async function tabTo(page, predicate, limit = 40) {
  for (let i = 0; i < limit; i += 1) {
    await page.waitForTimeout(60);
    const probe = await page.evaluate(RING_PROBE);
    if (matchesControl(probe, predicate)) return probe;
    await page.keyboard.press('Tab');
  }
  return null;
}

/**
 * Full matrix by default. The three `HR_ONLY_*` filters exist so a single
 * state can be re-measured in seconds while diagnosing; a filtered run is
 * explicitly marked in the manifest and is NEVER acceptance evidence.
 */
const ALL_VIEWPORTS = [
  { name: '1440x1000', width: 1440, height: 1000 },
  { name: '1280x900', width: 1280, height: 900 },
];
const ALL_THEMES = ['light', 'dark'];
const ONLY_STATE = process.env.HR_ONLY_STATE ?? null;
const ONLY_VIEWPORT = process.env.HR_ONLY_VIEWPORT ?? null;
const ONLY_THEME = process.env.HR_ONLY_THEME ?? null;
const ONLY_SCENARIO = process.env.HR_ONLY_SCENARIO ?? null;
/**
 * A declared negative control: withhold ONE scenario's `operate` callback so a
 * real run demonstrates that the expectation does not disappear with it. It
 * marks the run partial, exactly like the other filters, so it can never be
 * mistaken for acceptance evidence.
 */
const DROP_OPERATE = process.env.HR_DROP_OPERATE ?? null;
const VIEWPORTS = ALL_VIEWPORTS.filter((v) => !ONLY_VIEWPORT || v.name === ONLY_VIEWPORT);
const THEMES = ALL_THEMES.filter((t) => !ONLY_THEME || t === ONLY_THEME);
const SELECTED_STATES = STATES.filter((st) => !ONLY_STATE || st.name === ONLY_STATE);
const SELECTED_SCENARIOS = KEYBOARD_SCENARIOS.filter(
  (sc) => !ONLY_SCENARIO || sc.name === ONLY_SCENARIO,
);
const PARTIAL_RUN = Boolean(ONLY_STATE || ONLY_VIEWPORT || ONLY_THEME || ONLY_SCENARIO
  || DROP_OPERATE);

/**
 * Run a scenario operation. A throwing operation records nothing, which the
 * required-operation gate reports as the MISSING required entry it is — the
 * throw itself is also recorded so the failure names its own cause.
 */
async function runOperate(operate, page) {
  try {
    return await operate(page);
  } catch (error) {
    return { op: '__operation_threw__', ok: false, detail: String(error).slice(0, 300) };
  }
}

// ---------------------------------------------------------------------------

const undeclared = [];
const blockedExternal = [];
const CAPACITY_PATH = `/api/v1/orgs/${SLUG}/settings/daemon-capacity`;

/** The state currently being captured, and its per-request counters. */
let active = { get: () => ({ status: 200, json: snapshot() }) };
let getIndex = 0;
let putIndex = 0;
/** Deliberately unanswered responses, destroyed in the finally block. */
const held = [];

/** Every capacity request this state actually made, in order. */
let requestLog = [];

function answer(res, spec) {
  if (!spec || spec.hold) { held.push(res); return; }
  res.writeHead(spec.status ?? 200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(spec.json ?? {}));
}

const api = [
  { path: '/api/v1/auth/bootstrap', json: { token: 'synthetic' } },
  { path: '/api/v1/orgs', json: { orgs: [{ slug: SLUG, root: '/synthetic' }] } },
  { path: `/api/v1/orgs/${SLUG}/settings`, json: () => SETTINGS },
  {
    method: 'GET',
    path: CAPACITY_PATH,
    handler: (req, res) => {
      const index = getIndex;
      getIndex += 1;
      const spec = active.get ? active.get(index) : { status: 200, json: snapshot() };
      requestLog.push({
        method: 'GET',
        index,
        status: spec.hold ? 'held' : (spec.status ?? 200),
        revision: spec.json?.revision ?? null,
        // C1: two successful reads are only "byte-identical" if their bodies
        // really are. The hash is taken of the exact served bytes.
        bodySha: spec.hold ? null : createHash('sha256')
          .update(JSON.stringify(spec.json ?? {})).digest('hex'),
      });
      answer(res, spec);
    },
  },
  {
    method: 'PUT',
    path: CAPACITY_PATH,
    handler: async (req, res) => {
      // The wire body and `If-Match` are the only proof of WHICH revision and
      // which pair a keyboard operation actually submitted.
      const raw = await new Promise((done) => {
        let buf = '';
        req.on('data', (chunk) => { buf += chunk; });
        req.on('end', () => done(buf));
      });
      let body = null;
      try { body = JSON.parse(raw); } catch { body = raw; }
      const ifMatch = req.headers['if-match'] ?? null;
      const index = putIndex;
      putIndex += 1;
      // A state that never declared a PUT must not silently succeed: an
      // undeclared write is a fail-closed receipt, exactly like an undeclared
      // path.
      if (!active.put) {
        undeclared.push(`PUT ${CAPACITY_PATH} (state "${active.name}" declared no write)`);
        answer(res, { status: 599, json: { error: 'undeclared write' } });
        return;
      }
      const spec = active.put(index);
      requestLog.push({
        method: 'PUT',
        index,
        status: spec.hold ? 'held' : (spec.status ?? 200),
        revision: spec.json?.revision ?? null,
        // Which response a reconciliation choice was made against.
        latestRevision: spec.json?.detail?.latest?.revision ?? null,
        ifMatch,
        body,
      });
      answer(res, spec);
    },
  },
  { path: `/api/v1/orgs/${SLUG}/dashboard/summary`, json: { counts: {}, recent_tasks: [], escalations: [], agents: [] } },
  // The leave-dialog keyboard scenario CONFIRMS a departure, so the Settings
  // Organization panel really mounts and really reads its roster. Declaring it
  // keeps the venue fail-closed and explicit rather than letting a genuine
  // app request land in the undeclared bucket.
  { path: `/api/v1/orgs/${SLUG}/agents`, json: { agents: [] } },
  // REQUIRED OVERRIDE of harness.mjs's fail-OPEN unmatched-/api/ default.
  {
    path: /^\/api\//,
    handler: (req, res) => {
      undeclared.push(req.url);
      res.writeHead(599, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'undeclared api path — this venue is fail-closed' }));
    },
  },
];

// The gate self-test needs no venue, no browser and no dist build.
if (SELFTEST_MANIFEST) {
  process.exit(runGateSelftest(SELFTEST_MANIFEST));
}

const root = findDist();
const server = await createServer({ root, api });
const origin = new URL(server.url).origin;

// The inherited task TMPDIR is long enough to abort Chrome on its singleton
// socket, so the browser profile gets a short task-owned one.
const shortTmp = '/tmp/hr8553';
mkdirSync(shortTmp, { recursive: true });
process.env.TMPDIR = shortTmp;

// Use the INSTALLED Chrome rather than a downloaded browser: this host's
// playwright build and its browser cache are on different revisions, and
// nothing here may install a dependency.
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.HR_CHROME_PATH ?? '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
const results = [];
/**
 * 19.1 framing receipts. The app shell scrolls INTERNALLY, so a `fullPage`
 * screenshot is still only the viewport, and a `prep` that clicks a control
 * near the bottom silently scrolls the panel's top — and its retained-receipt
 * / error / recovery banner — out of the captured frame. A state may therefore
 * declare the subject its capture is EVIDENCE OF; the subject is scrolled into
 * view before the shot and its rect is asserted to lie inside the viewport.
 * A declared subject that cannot be framed FAILS the run rather than shipping
 * a screenshot that does not show what it claims to show.
 */
const framedSubjects = [];

try {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
        deviceScaleFactor: 1,
        // Pinned so the app's own receipt copy is deterministic across hosts.
        timezoneId: 'UTC',
      });
      // Context-level fail-closed egress guard: nothing but the task-owned
      // loopback origin may be requested, by the app or by anything else.
      await context.route('**/*', (route) => {
        const url = route.request().url();
        if (url.startsWith(origin)) return route.continue();
        blockedExternal.push(url);
        return route.abort();
      });

      for (const state of SELECTED_STATES) {
        active = state;
        getIndex = 0;
        putIndex = 0;
        requestLog = [];
        const page = await context.newPage();
        await page.addInitScript(INSTALL_VISIBILITY);
        // A C1 state pins the browser clock before its FIRST read, and moves it
        // forward per step inside `prep`.
        if (state.c1) await page.clock.setFixedTime(C1_TIMES.t0);
        await page.addInitScript((t) => {
          window.sessionStorage.setItem('happyranch.token', 'synthetic');
          window.localStorage.setItem('happyranch.theme', t);
        }, theme);
        // A state whose GET never answers can never reach `networkidle`; that
        // is the POINT of the loading capture, so it settles on DOM content.
        await page.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, {
          waitUntil: state.settle === 'loading' ? 'domcontentloaded' : 'networkidle',
        });
        try {
          await page.waitForSelector('#capacity-panel-heading', { timeout: 15000 });
        } catch (error) {
          const body = (await page.textContent('body')) ?? '';
          console.error(`state ${state.name} never rendered the panel. body head: ${body.slice(0, 500)}`);
          throw error;
        }
        if (state.settle === 'loading') {
          // The GENUINE loading frame, proven present rather than waited out.
          await page.waitForSelector('text=Loading daemon capacity…', { timeout: 15000 });
        } else if (state.settle === 'denied') {
          await page.waitForSelector('text=Could not load daemon capacity.', { timeout: 15000 });
        } else {
          await page.waitForFunction(
            () => !document.body.textContent.includes('Loading daemon capacity'),
            { timeout: 15000 },
          );
        }
        const prepReceipt = state.prep ? await state.prep(page) : null;
        await page.waitForTimeout(250);

        if (state.frame) {
          const subject = page.locator(state.frame.sel).first();
          await subject.scrollIntoViewIfNeeded();
          await page.waitForTimeout(150);
          const box = await subject.boundingBox();
          const inFrame = box !== null
            && box.x >= 0 && box.y >= 0
            && box.x + box.width <= viewport.width
            && box.y + box.height <= viewport.height;
          framedSubjects.push({
            state: state.name,
            viewport: viewport.name,
            theme,
            label: state.frame.label,
            selector: state.frame.sel,
            inFrame,
            box,
          });
        }

        const file = `capacity-${state.name}-${viewport.name}-${theme}.png`;
        await page.screenshot({ path: join(outDir, file), fullPage: true });

        // Computed visibility + contrast receipts for the a11y cases, taken in
        // a REAL browser rather than from DOM presence.
        const receipt = await page.evaluate(() => {
          const visible = window.__capacityVisible;
          const visibleInView = window.__capacityVisibleInView;
          if (!visible || !visibleInView) throw new Error('visibility predicate was not installed');
          const alerts = [...document.querySelectorAll('[role="alert"]')];
          const importantText = [...document.querySelectorAll(
            '[role="alert"], [role="status"], #capacity-workers-help, #capacity-cap-help,'
            + ' #capacity-reason-help, #capacity-workers-guidance, #capacity-cap-guidance,'
            + ' #capacity-override-heading, label[for="capacity-reason"]',
          )];
          const detailsOpen = document.querySelector('details')?.open ?? null;
          const scrollW = document.documentElement.scrollWidth;
          const clientW = document.documentElement.clientWidth;
          const helper = document.querySelector('#capacity-workers-help');
          const helperStyle = helper ? getComputedStyle(helper) : null;
          return {
            alertCount: alerts.length,
            allAlertsVisible: alerts.every(visibleInView),
            importantTextCount: importantText.length,
            hiddenImportantText: importantText.filter((n) => !visibleInView(n))
              .map((n) => (n.id || (n.textContent ?? '').trim().slice(0, 60))),
            detailsOpen,
            horizontalOverflow: scrollW > clientW,
            scrollW,
            clientW,
            helperColor: helperStyle?.color ?? null,
            helperBackground: helperStyle ? getComputedStyle(document.body).backgroundColor : null,
            liveRegions: document.querySelectorAll('[aria-live]').length,
            // Post-transition coherence: these three must never coexist with a
            // success banner (R8).
            savedBanner: document.body.textContent.includes('Saved for next restart')
              || document.body.textContent.includes('Saved. No restart is pending'),
            unsavedChanges: document.body.textContent.includes('Unsaved changes.'),
            changedElsewhere: document.body.textContent.includes('Configuration changed elsewhere.'),
            submittedRecord: document.body.textContent.includes('You submitted'),
            // The reconciliation region's own text, so an incoherent
            // post-transition surface names itself instead of needing a
            // separate investigation.
            outcomeRegionText: document.querySelector('#capacity-outcome')?.innerText ?? null,
          };
        });
        const contrast = await page.evaluate(CONTRAST_FN);
        // Everything above is a still frame. The primary action additionally
        // needs its POINTER states measured, and that has to happen after the
        // screenshot so the capture is not a hover frame.
        let primaryActionTone = null;
        if (state.primaryAction) {
          const requestsBefore = requestLog.length;
          primaryActionTone = await measurePrimaryActionTone(page, state.primaryAction);
          // A probe that fired the control it was measuring would have written
          // through this state. Zero is the passing value and it is gated.
          primaryActionTone.requestsDuringProbe = requestLog.length - requestsBefore;
        }
        results.push({
          state: state.name,
          viewport: viewport.name,
          theme,
          file,
          // The ACTUAL request sequence this capture produced, so a surface can
          // be reconciled against what the app really asked for.
          requests: [...requestLog],
          ...receipt,
          // Whatever the state's own prep recorded (the C1 step receipts).
          ...(prepReceipt ?? {}),
          contrast,
          primaryActionTone,
        });
        await page.close();
      }
      await context.close();
    }
  }

  // 16.11 — keyboard/focus pass in a real browser at BOTH desktop widths and in
  // BOTH themes. A single light/1440 pass could not see a ring that disappears
  // in dark or a tab order that changes when the shell reflows.
  const tabOrders = [];
  const keyboardOperations = [];
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      for (const scenario of SELECTED_SCENARIOS) {
        const kbContext = await browser.newContext({
          viewport: { width: viewport.width, height: viewport.height },
          colorScheme: theme,
          deviceScaleFactor: 1,
          timezoneId: 'UTC',
        });
        await kbContext.route('**/*', (route) => (route.request().url().startsWith(origin)
          ? route.continue()
          : (blockedExternal.push(route.request().url()), route.abort())));
        // The scenario's OWN allowed responses drive it into the state whose
        // controls are being walked. `active` is the same fail-closed fixture
        // seam the capture states use, so an undeclared write here is still a
        // recorded fail-closed receipt.
        active = { name: `keyboard:${scenario.name}`, get: scenario.get, put: scenario.put };
        getIndex = 0;
        putIndex = 0;
        requestLog = [];
        const kbPage = await kbContext.newPage();
        await kbPage.addInitScript((t) => {
          window.sessionStorage.setItem('happyranch.token', 'synthetic');
          window.localStorage.setItem('happyranch.theme', t);
        }, theme);
        await kbPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
        await kbPage.waitForSelector('#capacity-workers');
        if (scenario.prep) await scenario.prep(kbPage);
        // Some controls in the accepted 16.6 order only ENTER the tab ring once
        // an earlier control has been operated — `Save for next restart` is
        // `disabled` while a shadowed key is unacknowledged (4.1b), and a
        // disabled control is not focusable. Those scenarios declare
        // `operateFirst`, so the keyboard operation runs BEFORE the walk and the
        // walk then sees the control set an operator would really have. The
        // operation is still a real Tab-and-press, and its assertion is still
        // gated; only its position in the scenario moves.
        // A declared negative control may withhold one scenario's operation
        // callback. The expectation is declared independently, so the run must
        // still FAIL — and such a run is marked partial and is never
        // acceptance evidence.
        const operateFn = DROP_OPERATE === scenario.name ? null : scenario.operate;
        let operated = scenario.operateFirst && operateFn
          ? await runOperate(operateFn, kbPage)
          : null;
        const { order, missing } = await walkControls(kbPage, {
          start: scenario.start,
          required: scenario.required,
        });
        // Still keyboard-focused on the LAST control of the walk; re-focusing
        // programmatically here would erase the ring from the screenshot.
        if (scenario.name === 'ordinary' || scenario.screenshot) {
          await kbPage.screenshot({
            path: join(outDir, `capacity-keyboard-${scenario.name}-${viewport.name}-${theme}.png`),
            fullPage: true,
          });
        }
        tabOrders.push({
          scenario: scenario.name, viewport: viewport.name, theme, order, missing,
        });
        if (!scenario.operateFirst && operateFn) {
          operated = await runOperate(operateFn, kbPage);
        }
        // An `operate` callback may record SEVERAL required operations (a
        // choice and the separate manual save it enables). Each is recorded on
        // its own, keyed by scenario/operation/viewport/theme, and judged
        // against the declared REQUIRED_OPERATIONS inventory below.
        for (const record of [operated].flat().filter(Boolean)) {
          keyboardOperations.push({
            scenario: scenario.name, viewport: viewport.name, theme, ...record,
          });
        }
        await kbContext.close();
      }
    }
  }

  // 16.10 — the ACTUAL leave dialog's text and its ENABLED sibling's tone,
  // measured while the real portalled dialog is open.
  //
  // The contrast function could always see the portal, but nothing ever called
  // it there: the 17 capture states never open the dialog and the keyboard
  // scenarios never sampled. This pass opens the dialog exactly the way an
  // operator does, samples EVERY dialog string with the same `CONTRAST_FN` the
  // panel uses, and measures the genuinely enabled `Discard and continue`
  // through real rest / hover / active pointer phases.
  //
  // It is deliberately ISOLATED from the keyboard operations, in its own
  // context: a tone probe that pressed the primary action would CONFIRM the
  // departure, so the measurement must never share a page with the operation
  // that owns that decision. The probe releases the pointer away from the
  // control, and the URL plus the request log are compared before and after so
  // an accidental write or navigation is recorded rather than assumed absent.
  const dialogMeasurements = [];
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const dlgContext = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
        deviceScaleFactor: 1,
        timezoneId: 'UTC',
      });
      await dlgContext.route('**/*', (route) => (route.request().url().startsWith(origin)
        ? route.continue()
        : (blockedExternal.push(route.request().url()), route.abort())));
      active = { name: 'dialog-measure', get: () => OK(OVERRIDE_SNAPSHOT()) };
      getIndex = 0;
      putIndex = 0;
      requestLog = [];
      const dlgPage = await dlgContext.newPage();
      await dlgPage.addInitScript((t) => {
        window.sessionStorage.setItem('happyranch.token', 'synthetic');
        window.localStorage.setItem('happyranch.theme', t);
      }, theme);
      await dlgPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
      await dlgPage.waitForSelector('#capacity-workers');
      await dlgPage.fill('#capacity-workers', '5');
      await dlgPage.fill('#capacity-cap', '12');
      await dlgPage.fill('#capacity-reason', 'retained exact draft');
      await dlgPage.check('#capacity-override input[type=checkbox]');
      const navLink = dlgPage.getByRole('link', { name: 'Organization', exact: true });
      await navLink.focus();
      await dlgPage.keyboard.press('Enter');
      await dlgPage.locator(LEAVE_DIALOG).waitFor({ timeout: 15000 });
      await dlgPage.waitForTimeout(300);
      const urlBefore = dlgPage.url();
      const requestsBefore = requestLog.length;
      const samples = (await dlgPage.evaluate(CONTRAST_FN)).filter((c) => c.inDialog);
      // The footer's SECOND button is the enabled primary sibling; its measured
      // `text` is gated, so a reordered footer cannot be sampled as this one.
      const tone = await measurePrimaryActionTone(dlgPage, `${LEAVE_DIALOG} div > button:nth-of-type(2)`);
      const after = await dlgPage.evaluate((sel) => ({
        dialogOpen: document.querySelector(sel) !== null,
        url: location.href,
      }), LEAVE_DIALOG);
      dialogMeasurements.push({
        viewport: viewport.name,
        theme,
        samples,
        tone,
        requestsDuringProbe: requestLog.length - requestsBefore,
        urlBefore,
        urlAfter: after.url,
        dialogStillOpen: after.dialogOpen,
      });
      await dlgContext.close();
    }
  }

  // 11.x — the focused FAILED-REREAD negative control for "Check saved values".
  //
  // A refetch that fails still resolves carrying the previous cached values.
  // Cached values beside an error are not successful usable evidence, so the
  // SAME `checkUsableVerdict` the positive operation is judged by must REJECT
  // this run. The control is collected in a real browser, not simulated.
  const checkControls = [];
  {
    const ctlViewport = VIEWPORTS[0] ?? ALL_VIEWPORTS[0];
    const ctlTheme = THEMES[0] ?? ALL_THEMES[0];
    const ctlContext = await browser.newContext({
      viewport: { width: ctlViewport.width, height: ctlViewport.height },
      colorScheme: ctlTheme,
      deviceScaleFactor: 1,
      timezoneId: 'UTC',
    });
    await ctlContext.route('**/*', (route) => (route.request().url().startsWith(origin)
      ? route.continue()
      : (blockedExternal.push(route.request().url()), route.abort())));
    active = {
      name: 'check-failed-reread-control',
      get: (index) => (index === 0 ? OK(snapshot()) : FAIL(503, {})),
      put: () => FAIL(500, {}),
    };
    getIndex = 0;
    putIndex = 0;
    requestLog = [];
    const ctlPage = await ctlContext.newPage();
    await ctlPage.addInitScript((t) => {
      window.sessionStorage.setItem('happyranch.token', 'synthetic');
      window.localStorage.setItem('happyranch.theme', t);
    }, ctlTheme);
    await ctlPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
    await ctlPage.waitForSelector('#capacity-workers');
    await DIRTY_DRAFT(ctlPage);
    await ctlPage.click('button[type="submit"]');
    await ctlPage.waitForSelector('text=Save result unknown.', { timeout: 15000 });
    await ctlPage.fill('#capacity-workers', '7');
    await ctlPage.fill('#capacity-cap', '14');
    const evidence = await runOperate(collectCheckEvidence, ctlPage);
    const verdict = checkUsableVerdict(evidence);
    checkControls.push({
      control: 'failed-reread',
      viewport: ctlViewport.name,
      theme: ctlTheme,
      expectUsable: false,
      evidence,
      verdict,
      // Non-vacuous: the control must really have REACHED and activated Check
      // and been rejected FOR the failed read — not merely have gone wrong.
      ok: evidence?.reached === true && verdict.ok === false
        && verdict.fail.some((f) => f.startsWith('Check did not settle')),
    });
    await ctlContext.close();
  }
  console.log(`check-semantics controls: ${checkControls.length}, `
    + `unexpected: ${checkControls.filter((c) => !c.ok).length}`);

  // 16.8 — controls for the computed-visibility predicate itself.
  //
  // The gate is only as good as this predicate, and a predicate defect is
  // invisible in a run where nothing happens to be clipped: every state
  // reports "visible" and the run goes green. These controls run the EXACT
  // function installed on every capture page (`window.__capacityVisibleInView`,
  // from `INSTALL_VISIBILITY`) against synthetic fixtures whose correct answer
  // is known, so a false-green predicate fails the run on its own.
  //
  // Both directions matter. The negatives prove genuine clipping is caught —
  // including the exact `display:flow-root; overflow:clip; height:1px`
  // counterexample that the old `scrollHeight <= clientHeight + 1` conjunct
  // let through. The positives prove the repair did NOT turn every scrollable
  // container into a false finding: content reachable by scrolling, content
  // below the fold, and content that merely overlaps its clip edge must all
  // still report visible.
  const clippingControls = await (async () => {
    const ctlContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await ctlContext.route('**/*', (route) => (route.request().url().startsWith(origin)
      ? route.continue()
      : (blockedExternal.push(route.request().url()), route.abort())));
    const ctlPage = await ctlContext.newPage();
    await ctlPage.addInitScript(INSTALL_VISIBILITY);
    // Served from the venue's own origin, so the fail-closed external guard is
    // not tripped by the control fixtures themselves.
    await ctlPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'domcontentloaded' });
    const out = await ctlPage.evaluate(() => {
      const visibleInView = window.__capacityVisibleInView;
      if (!visibleInView) throw new Error('visibility predicate was not installed');
      const host = document.createElement('div');
      host.id = 'clipping-controls';
      document.body.appendChild(host);
      const CASES = [
        // --- negatives: MUST report not-visible -------------------------
        {
          name: 'overflow-clip-overflowing-counterexample', expect: false,
          html: '<div style="display:flow-root;overflow:clip;height:1px;width:100px"><p data-t style="margin-top:30px">Hidden warning</p></div>',
        },
        {
          name: 'overflow-hidden-zero-height-ancestor', expect: false,
          html: '<div style="overflow:hidden;height:0;width:100px"><p data-t>Hidden warning</p></div>',
        },
        {
          name: 'closed-details', expect: false,
          html: '<details><summary>More</summary><p data-t>Hidden warning</p></details>',
        },
        {
          name: 'visibility-hidden-ancestor', expect: false,
          html: '<div style="visibility:hidden"><p data-t>Hidden warning</p></div>',
        },
        {
          name: 'opacity-zero-ancestor', expect: false,
          html: '<div style="opacity:0"><p data-t>Hidden warning</p></div>',
        },
        // --- positives: MUST report visible ------------------------------
        {
          name: 'ordinary-visible-text', expect: true,
          html: '<p data-t>Ordinary warning</p>',
        },
        {
          name: 'overflow-hidden-reachable-by-scrolling', expect: true,
          html: '<div style="overflow:hidden;height:40px;width:200px"><div style="height:300px"></div><p data-t>Reachable warning</p></div>',
        },
        {
          name: 'overflow-auto-below-the-fold', expect: true,
          html: '<div style="overflow:auto;height:40px;width:200px"><div style="height:300px"></div><p data-t>Scrollable warning</p></div>',
        },
        {
          name: 'overflow-hidden-partially-overlapping-clip', expect: true,
          html: '<div style="overflow:hidden;height:10px;width:200px"><p data-t style="margin:0">Partly visible warning</p></div>',
        },
        {
          name: 'open-details', expect: true,
          html: '<details open><summary>More</summary><p data-t>Disclosed warning</p></details>',
        },
      ];
      const results = [];
      for (const c of CASES) {
        host.innerHTML = c.html;
        const node = host.querySelector('[data-t]');
        const reported = visibleInView(node);
        results.push({ name: c.name, expect: c.expect, reported, ok: reported === c.expect });
      }
      host.remove();
      return results;
    });
    await ctlContext.close();
    return out;
  })();
  const clippingControlFailures = clippingControls.filter((c) => !c.ok);
  console.log(`clipping-predicate controls: ${clippingControls.length}, failures: ${clippingControlFailures.length}`);

  const hashes = readdirSync(outDir).filter((f) => f.endsWith('.png')).sort().map((f) => ({
    file: f,
    sha256: createHash('sha256').update(readFileSync(join(outDir, f))).digest('hex'),
  }));

  const manifest = {
    partialRun: PARTIAL_RUN,
    partialRunFilters: PARTIAL_RUN
      ? { state: ONLY_STATE, viewport: ONLY_VIEWPORT, theme: ONLY_THEME }
      : null,
    disclosure: 'SYNTHETIC INTERCEPTED HTTP — frontend behaviour only. Not evidence of backend persistence, audit, restart or deployment.',
    origin,
    undeclaredApiPaths: undeclared,
    blockedExternalRequests: blockedExternal,
    // Fail-closed means NO undeclared `/api/` path was served. Blocked external
    // requests are the guard WORKING, not a failure — they are enumerated above
    // so the reviewer can see exactly what the app attempted to reach.
    failClosedOk: undeclared.length === 0,
    horizontalOverflowStates: results.filter((r) => r.horizontalOverflow).map((r) => r.file),
    states: results,
    tabOrders,
    // 16.10 / 16.11 roll-ups. Empty arrays are the passing result.
    // GATED: the accepted-criterion set. Any entry here fails the run.
    lowContrastFindings: results.flatMap((r) => (r.contrast ?? [])
      .filter((c) => c.present && c.gated && (c.unmeasurable !== undefined || !c.passesAA))
      .map((c) => ({ file: r.file, ...c }))),
    // The ENUMERATED-exception bucket. It is now structurally EMPTY, because
    // `CONTRAST_EXEMPTIONS` is empty and therefore every measured sample is
    // gated above. It is kept, not deleted, so that if an exemption is ever
    // reintroduced the samples it covers are still enumerated here by exact
    // identity instead of vanishing from the evidence.
    contrastOutsideThisScope: (() => {
      const seen = new Map();
      for (const r of results) {
        for (const c of (r.contrast ?? [])) {
          if (!c.present || c.gated || c.passesAA) continue;
          const key = `${r.theme}|${c.selector}|${c.color}|${c.ratio}`;
          if (!seen.has(key)) {
            seen.set(key, {
              theme: r.theme, selector: c.selector, color: c.color,
              background: c.background, ratio: c.ratio, threshold: c.threshold,
              sample: c.text, exemptionId: c.exemptionId ?? null,
              exemptionReason: c.exemptionReason ?? null, occurrences: 0,
            });
          }
          seen.get(key).occurrences += 1;
        }
      }
      return [...seen.values()].sort((a, b) => a.ratio - b.ratio);
    })(),
    // 19.2 — the case is MAPPED, not retired. It shares 16.11's evidence, and
    // this roll-up names exactly which walks are its result. Empty means the
    // sharing is no longer legitimate, and the gate fails the run.
    // It is now the `ordinary` scenario's walk specifically, so widening the
    // matrix cannot quietly leave 19.2 asserted by some other scenario.
    case192KeyboardPass1440: tabOrders
      .filter((t) => t.scenario === 'ordinary' && t.viewport === '1440x1000'
        && (t.order ?? []).length > 0 && (t.missing ?? []).length === 0)
      .map((t) => ({
        scenario: t.scenario, viewport: t.viewport, theme: t.theme, controls: t.order.length,
      })),
    // Accepted 16.10 is a requirement on ENABLED control labels. Both buckets
    // are measured and GATED at authored colour; the split exists so that an
    // inactive, `disabled:opacity-50`-dimmed label can never be read as
    // evidence that the enabled label passes. `cumulativeOpacity` records how
    // much of the authored colour actually reaches the screen.
    controlLabelSamples: (() => {
      const bucket = (wantInactive) => {
        const seen = new Map();
        for (const r of results) {
          for (const c of (r.contrast ?? [])) {
            if (!c.present || !c.isControlLabel || Boolean(c.inactive) !== wantInactive) continue;
            const key = `${r.theme}|${c.selector}|${c.text}|${c.color}|${c.background}`;
            if (!seen.has(key)) {
              seen.set(key, {
                theme: r.theme, selector: c.selector, sample: c.text,
                color: c.color, background: c.background, ratio: c.ratio,
                threshold: c.threshold, passesAA: c.passesAA,
                cumulativeOpacity: c.cumulativeOpacity, occurrences: 0,
              });
            }
            seen.get(key).occurrences += 1;
          }
        }
        return [...seen.values()].sort((a, b) => a.ratio - b.ratio);
      };
      return { enabled: bucket(false), inactive: bucket(true) };
    })(),
    // The ENABLED primary action measured with a real pointer in its resting,
    // hover and active states. GATED below, both for its ratios and for its
    // own non-vacuity.
    primaryActionTone: results
      .filter((r) => r.primaryActionTone !== null && r.primaryActionTone !== undefined)
      .map((r) => ({
        state: r.state, viewport: r.viewport, theme: r.theme, file: r.file,
        selector: r.primaryActionTone.selector,
        present: r.primaryActionTone.present,
        requestsDuringProbe: r.primaryActionTone.requestsDuringProbe ?? null,
        phases: r.primaryActionTone.phases ?? [],
      })),
    contrastExemptionsDeclared: DECLARED_CONTRAST_EXEMPTIONS,
    // Any exemption id the page emitted that this file does not declare. A
    // non-empty list means the enforcing copy and the declared inventory have
    // drifted, and it FAILS the run.
    undeclaredContrastExemptions: [...new Set(results.flatMap(
      (r) => (r.contrast ?? []).map((c) => c.exemptionId).filter(Boolean),
    ))].filter((id) => !DECLARED_CONTRAST_EXEMPTIONS.some((e) => e.id === id)),
    contrastSamplesMeasured: results.reduce((n, r) => n + (r.contrast ?? []).length, 0),
    focusRingsMissing: tabOrders.flatMap((t) => t.order
      .filter((o) => o && o.inScope && (!o.focusVisible || !o.focusRingVisible))
      .map((o) => ({
        scenario: t.scenario, viewport: t.viewport, theme: t.theme, control: controlName(o),
        focusVisible: o.focusVisible, boxShadow: o.boxShadow, outline: o.outline,
      }))),
    // GATED, not merely reported: accepted 16.10 forbids a low-contrast focus
    // ring, and a ring that paints but cannot be seen does not meet it.
    // `ringContrast` is measured AFTER cumulative opacity, so a full-opacity
    // token painted through a translucent control is scored as what reaches
    // the screen.
    lowContrastFocusRings: tabOrders.flatMap((t) => t.order
      .filter((o) => o && o.inScope && o.focusRingVisible
        && o.ringContrast !== null && o.ringContrast < 3)
      .map((o) => ({
        scenario: t.scenario, viewport: t.viewport, theme: t.theme, control: controlName(o),
        ratio: o.ringContrast, ratioAuthored: o.ringContrastAuthored,
        cumulativeOpacity: o.cumulativeOpacity,
      }))),
    // Controls the tab ring passes through that belong to the shared app shell,
    // NOT to this bounded capacity radius. Reported in full and deliberately
    // NOT gated: restyling shell chrome is outside this leg's authority, so a
    // finding here would be scope expansion rather than coverage. Enumerated
    // by exact identity so it cannot be used to quietly park a capacity
    // control outside the gate.
    focusRingsOutsideThisScope: (() => {
      const seen = new Map();
      for (const t of tabOrders) {
        for (const o of t.order) {
          if (!o || o.inScope) continue;
          if (!(o.focusRingVisible && o.ringContrast !== null && o.ringContrast < 3)) continue;
          const key = `${t.theme}|${controlName(o)}|${o.ringContrast}`;
          if (!seen.has(key)) {
            seen.set(key, {
              theme: t.theme, control: controlName(o), ratio: o.ringContrast, occurrences: 0,
            });
          }
          seen.get(key).occurrences += 1;
        }
      }
      return [...seen.values()].sort((a, b) => a.ratio - b.ratio);
    })(),
    // 16.6/16.11 — a required control the keyboard walk never reached. Named,
    // so a shortened walk cannot pass as a complete one.
    keyboardControlGaps: tabOrders
      .filter((t) => (t.missing ?? []).length > 0)
      .map((t) => ({
        scenario: t.scenario, viewport: t.viewport, theme: t.theme, missing: t.missing,
      })),
    // Non-vacuity: every scenario must actually have been walked at every
    // viewport and theme. A scenario that silently produced nothing would
    // otherwise contribute no findings and read as a pass.
    // 16.6/16.7 — every control was not merely focusable but actually
    // OPERATED from the keyboard, and caused the state change it should. The
    // raw records are the evidence; the gate derives coverage and failure from
    // the DECLARED inventories below, so an operation that never happened is a
    // failure rather than an absent row.
    keyboardOperations,
    expectedOperations: REQUIRED_OPERATIONS,
    requiredWalks: REQUIRED_WALKS,
    requiredDialogSamples: REQUIRED_DIALOG_SAMPLES.map((d) => d.key),
    matrix: {
      viewports: VIEWPORTS.map((v) => v.name),
      themes: THEMES,
      scenarios: SELECTED_SCENARIOS.map((sc) => sc.name),
      droppedOperate: DROP_OPERATE,
    },
    // 16.10 — the ACTUAL portalled dialog, sampled and tone-measured while it
    // was open, in its own isolated context.
    dialogMeasurements,
    // 10.x/11.x — the failed-reread negative control for Check semantics.
    checkControls,
    // 2.9/14.3 — the C1 identical-success -> failed-read retained receipt.
    c1Sequences: results
      .filter((r) => r.c1 !== undefined && r.c1 !== null)
      .map((r) => ({
        state: r.state, viewport: r.viewport, theme: r.theme, file: r.file,
        times: C1_TIMES, requests: r.requests, ...r.c1,
      })),
    // 16.8 — the predicate's own controls, both directions, GATED below.
    clippingControls,
    clippingControlFailures,
    visibilityFailures: results
      .filter((r) => !r.allAlertsVisible || (r.hiddenImportantText ?? []).length > 0)
      .map((r) => ({
        file: r.file,
        allAlertsVisible: r.allAlertsVisible,
        hiddenImportantText: r.hiddenImportantText,
      })),
    // 19.1 / 14.1-14.3 — the retained-receipt, error and recovery banners are
    // provably INSIDE the captured frame, not merely present in the DOM.
    framedSubjects,
    subjectsNotFramed: framedSubjects.filter((f) => !f.inFrame),
    statesCaptured: SELECTED_STATES.map((st) => st.name),
    // R8: a success banner must not coexist with unsaved/changed-elsewhere or
    // a still-pinned submission.
    incoherentPostSaveStates: results
      .filter((r) => r.savedBanner && (r.unsavedChanges || r.changedElsewhere || r.submittedRecord))
      .map((r) => ({
        file: r.file,
        unsavedChanges: r.unsavedChanges,
        changedElsewhere: r.changedElsewhere,
        submittedRecord: r.submittedRecord,
        requests: r.requests,
        outcomeRegionText: r.outcomeRegionText,
      })),
    pngs: hashes,
  };
  const gate = evaluateGate(manifest, {
    partialRun: PARTIAL_RUN,
    viewports: VIEWPORTS.map((v) => v.name),
    themes: THEMES,
    // A filtered run narrows WHICH scenarios are expected; it never narrows the
    // expectation for a scenario whose operation callback was withheld.
    scenarios: SELECTED_SCENARIOS.map((sc) => sc.name),
  });
  manifest.gateFailures = gate.map(([label, findings]) => ({ label, count: findings.length }));
  writeFileSync(join(outDir, 'MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`);

  console.log(`captured ${hashes.length} PNGs -> ${outDir}`);
  console.log(`undeclared /api/ paths: ${undeclared.length}`);
  console.log(`blocked external requests: ${blockedExternal.length}`);
  console.log(`contrast samples measured: ${manifest.contrastSamplesMeasured}`);
  console.log(`keyboard operations recorded: ${manifest.keyboardOperations.length}`);
  console.log(`leave-dialog measurements: ${manifest.dialogMeasurements.length}`);
  console.log(`C1 retained-receipt sequences: ${manifest.c1Sequences.length}`);
  console.log(`visibility failures (16.8): ${manifest.visibilityFailures.length}`);
  if (gate.length > 0) {
    for (const [label, findings] of gate) {
      console.error(`FAIL: ${label}: ${findings.length}`);
      console.error(JSON.stringify(findings.slice(0, 12), null, 2));
    }
    process.exitCode = 1;
  } else if (PARTIAL_RUN) {
    console.log('gate clean, but this was a FILTERED run — not acceptance evidence.');
  } else {
    console.log('ACCEPTANCE GATE PASS: every gated criterion is clean.');
  }
} finally {
  // Deliberately unanswered responses are released so the server can close.
  for (const res of held) {
    try { res.destroy(); } catch { /* already gone */ }
  }
  await browser.close();
  await server.close();
}

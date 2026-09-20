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
 *         reading would not see it.
 *   16.11 the 16.6 tab order walked by real keystrokes at both widths in both
 *         themes, with the ring measured where it actually paints.
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
 */
import { mkdirSync, writeFileSync, readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, resolve } from 'node:path';
import { createServer, findDist } from './harness.mjs';

const PLAYWRIGHT = process.env.HR_PLAYWRIGHT_ROOT
  ?? '/tmp/task-6530-playwright/node_modules/playwright/index.js';
// Playwright ships CommonJS, so an ESM `import()` hands it back under
// `default`. Accept either shape rather than assuming one.
const playwrightModule = await import(PLAYWRIGHT);
const { chromium } = playwrightModule.default ?? playwrightModule;

const args = process.argv.slice(2);
const outDir = resolve(args[args.indexOf('--out') + 1] ?? './capacity-shots');
mkdirSync(outDir, { recursive: true });

const SLUG = 'alpha';
const REV_A = `sha256:${'a'.repeat(64)}`;
const REV_B = `sha256:${'b'.repeat(64)}`;

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
      if (!hit) return { action: 'keyboard-open Capacity details', ok: false, detail: 'never reached' };
      const before = await page.evaluate(() => document.querySelector('details')?.open ?? null);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(200);
      const after = await page.evaluate(() => document.querySelector('details')?.open ?? null);
      return {
        action: 'keyboard-open Capacity details', before, after, ok: before === false && after === true,
      };
    },
  },
  {
    // The acknowledgment sits between the reason and Save in the accepted
    // order, and only exists while the environment shadows a key.
    name: 'override-ack',
    get: () => OK(snapshot({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'The environment sets this value; a restart alone will not make the saved file win.',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    })),
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
      if (!hit) return { action: 'keyboard-toggle acknowledgment', ok: false, detail: 'never reached' };
      const before = await page.evaluate(() => document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null);
      await page.keyboard.press('Space');
      await page.waitForTimeout(200);
      const after = await page.evaluate(() => document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null);
      return {
        action: 'keyboard-toggle acknowledgment', before, after, ok: before === false && after === true,
      };
    },
  },
  {
    // "Check saved values" is only offered after a genuinely UNCERTAIN write
    // outcome, so it is reached through a real refused PUT, not a fixture.
    // The follow-up read is a real, usable GET, so the control's own effect is
    // observable.
    name: 'unknown-check',
    get: (index) => (index === 0
      ? OK(snapshot())
      : OK(snapshot({
        persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
        next_start: { queue_workers: 5, host_global_session_cap: 12 },
        revision: REV_B,
      }))),
    put: () => FAIL(500, {}),
    prep: async (page) => {
      await DIRTY_DRAFT(page);
      await page.click('button[type="submit"]');
      await page.waitForSelector('text=Save result unknown.', { timeout: 15000 });
    },
    start: '#capacity-workers',
    required: [
      { key: 'workers', id: 'capacity-workers' },
      { key: 'save', text: 'Save for next restart' },
      { key: 'refresh', text: 'Refresh running state' },
      { key: 'check', text: 'Check saved values' },
    ],
    operate: async (page) => {
      const hit = await tabTo(page, { text: 'Check saved values' });
      if (!hit) return { action: 'keyboard-activate Check saved values', ok: false, detail: 'never reached' };
      const before = await page.evaluate(() => document.querySelector('#capacity-outcome')?.innerText ?? null);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(600);
      const after = await page.evaluate(() => document.querySelector('#capacity-outcome')?.innerText ?? null);
      return {
        action: 'keyboard-activate Check saved values',
        beforeLength: before?.length ?? null,
        afterLength: after?.length ?? null,
        // The uncertainty must actually resolve into a different surface.
        ok: typeof before === 'string' && typeof after === 'string' && before !== after,
      };
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
      if (!hit) return { action: 'keyboard-activate rebase onto latest', ok: false, detail: 'never reached' };
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      const state = await page.evaluate(() => ({
        focusedId: document.activeElement?.id ?? null,
        focusedTag: document.activeElement?.tagName ?? null,
        stillOffering: document.body.textContent.includes('Keep my draft, rebase onto latest'),
        workers: document.querySelector('#capacity-workers')?.value ?? null,
      }));
      return {
        action: 'keyboard-activate rebase onto latest',
        ...state,
        // 16.7: focus moves to the reconciled region, is never lost to <body>,
        // and the choice actually clears the reconciliation offer. The draft
        // text is kept verbatim by a rebase.
        ok: state.focusedId === 'capacity-outcome'
          && state.focusedTag !== 'BODY'
          && state.stillOffering === false
          && state.workers === '5',
      };
    },
  },
  {
    // The PORTALLED leave-confirmation dialog. It is a capacity-owned surface
    // that lives outside the panel wrapper, which is exactly why its focus
    // rings were never in these rollups. Reached the way an operator reaches
    // it: a real draft, then a real keyboard activation of a real navigation
    // control.
    name: 'leave-dialog',
    get: () => OK(snapshot()),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'retained exact draft');
      const link = page.getByRole('link', { name: 'Organization', exact: true });
      await link.focus();
      await page.keyboard.press('Enter');
      await page.locator('[role="dialog"][aria-label="discard capacity draft confirmation"]').waitFor({ timeout: 15000 });
      await page.waitForTimeout(250);
    },
    start: '[role="dialog"][aria-label="discard capacity draft confirmation"] button',
    required: [
      { key: 'stay', text: 'Stay on page' },
      { key: 'discard-continue', text: 'Discard and continue' },
      { key: 'dialog-close', ariaLabel: 'Close' },
    ],
    screenshot: true,
    operate: async (page) => {
      // Stay: route unchanged, draft retained EXACTLY, focus returned to the
      // navigation trigger that opened the dialog (16.7).
      const urlBefore = page.url();
      const stay = await tabTo(page, { text: 'Stay on page' });
      if (!stay) return { action: 'keyboard Stay then confirmed departure', ok: false, detail: 'Stay never reached' };
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      const afterStay = await page.evaluate(() => ({
        activeTag: document.activeElement?.tagName ?? null,
        activeText: (document.activeElement?.textContent ?? '').trim(),
        activeHref: document.activeElement?.getAttribute('href') ?? null,
        workers: document.querySelector('#capacity-workers')?.value ?? null,
        cap: document.querySelector('#capacity-cap')?.value ?? null,
        reason: document.querySelector('#capacity-reason')?.value ?? null,
        ack: document.querySelector('#capacity-override input[type=checkbox]')?.checked ?? null,
        // Scoped by accessible name on purpose: the always-mounted assistant
        // dock also carries `role="dialog"`, so a bare `[role="dialog"]` query
        // is never false and would report the leave dialog as still open.
        dialogOpen: document.querySelector('[role="dialog"][aria-label="discard capacity draft confirmation"]') !== null,
      }));
      // Reopen and confirm departure really completes.
      const link = page.getByRole('link', { name: 'Organization', exact: true });
      await link.focus();
      await page.keyboard.press('Enter');
      await page.locator('[role="dialog"][aria-label="discard capacity draft confirmation"]').waitFor({ timeout: 15000 });
      await page.waitForTimeout(200);
      const leave = await tabTo(page, { text: 'Discard and continue' });
      if (!leave) return { action: 'keyboard Stay then confirmed departure', ok: false, detail: 'Discard and continue never reached' };
      await page.keyboard.press('Enter');
      let departed = false;
      try {
        await page.waitForURL('**/settings/organization', { timeout: 15000 });
        departed = true;
      } catch { departed = false; }
      return {
        action: 'keyboard Stay then confirmed departure',
        urlBefore,
        urlAfter: page.url(),
        ...afterStay,
        departed,
        ok: afterStay.dialogOpen === false
          // Route unchanged by Stay.
          && urlBefore.endsWith('/settings/daemon-capacity')
          // Draft retained byte-identically.
          && afterStay.workers === '5'
          && afterStay.cap === '12'
          && afterStay.reason === 'retained exact draft'
          // Focus returned to the navigation trigger, not lost to <body>.
          && afterStay.activeTag === 'A'
          && afterStay.activeText === 'Organization'
          // Reopening and confirming really departs.
          && departed,
      };
    },
  },
];

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
    const fg = parse(style.color);
    const bg = effectiveBackground(el);
    if (!fg || !bg) {
      out.push({
        selector, present: true, unmeasurable: style.color, gated: exempt === null,
        inactive: inactiveHost !== null, isControlLabel,
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
const VIEWPORTS = ALL_VIEWPORTS.filter((v) => !ONLY_VIEWPORT || v.name === ONLY_VIEWPORT);
const THEMES = ALL_THEMES.filter((t) => !ONLY_THEME || t === ONLY_THEME);
const SELECTED_STATES = STATES.filter((st) => !ONLY_STATE || st.name === ONLY_STATE);
const PARTIAL_RUN = Boolean(ONLY_STATE || ONLY_VIEWPORT || ONLY_THEME);

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
      });
      answer(res, spec);
    },
  },
  {
    method: 'PUT',
    path: CAPACITY_PATH,
    handler: (req, res) => {
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
        if (state.prep) await state.prep(page);
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
      for (const scenario of KEYBOARD_SCENARIOS) {
        const kbContext = await browser.newContext({
          viewport: { width: viewport.width, height: viewport.height },
          colorScheme: theme,
          deviceScaleFactor: 1,
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
        let operated = scenario.operateFirst && scenario.operate
          ? await scenario.operate(kbPage)
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
        if (!scenario.operateFirst && scenario.operate) {
          operated = await scenario.operate(kbPage);
        }
        if (operated) {
          keyboardOperations.push({
            scenario: scenario.name, viewport: viewport.name, theme, ...operated,
          });
        }
        await kbContext.close();
      }
    }
  }

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
    keyboardCoverageGaps: PARTIAL_RUN ? [] : VIEWPORTS.flatMap(
      (v) => THEMES.flatMap((th) => KEYBOARD_SCENARIOS.map((sc) => {
        const walk = tabOrders.find(
          (t) => t.scenario === sc.name && t.viewport === v.name && t.theme === th,
        );
        return (walk && (walk.order ?? []).length > 0)
          ? null
          : { scenario: sc.name, viewport: v.name, theme: th, reason: 'no keyboard walk recorded' };
      })),
    ).filter(Boolean),
    // 16.6/16.7 — every control was not merely focusable but actually
    // OPERATED from the keyboard, and caused the state change it should.
    keyboardOperations,
    keyboardOperationFailures: keyboardOperations.filter((op) => op.ok !== true),
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
  writeFileSync(join(outDir, 'MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`);

  console.log(`captured ${hashes.length} PNGs -> ${outDir}`);
  console.log(`undeclared /api/ paths: ${undeclared.length}`);
  console.log(`blocked external requests: ${blockedExternal.length}`);
  console.log(`horizontal overflow states: ${manifest.horizontalOverflowStates.length}`);
  console.log(`contrast samples measured: ${manifest.contrastSamplesMeasured}`);
  console.log(`low-contrast findings in the GATED set (16.10): ${manifest.lowContrastFindings.length}`);
  console.log(`low-contrast observations in the ENUMERATED exception set: ${manifest.contrastOutsideThisScope.length}`);
  console.log(`controls without a visible focus ring (16.11): ${manifest.focusRingsMissing.length}`);
  console.log(`focus rings below 3:1 against their surround (16.10/16.11): ${manifest.lowContrastFocusRings.length}`);
  console.log(`visibility failures (16.8): ${manifest.visibilityFailures.length}`);

  // --- 16.10 primary action, derived gate inputs ----------------------------
  // A phase that fails its threshold, is unmeasurable, or was read off a
  // DISABLED control (which is not enabled-label evidence).
  const primaryToneFindings = manifest.primaryActionTone.flatMap((t) => (t.present
    ? t.phases
      .filter((ph) => ph.present !== true || ph.unmeasurable !== undefined
        || ph.disabled === true || ph.passesAA !== true)
      .map((ph) => ({
        state: t.state, viewport: t.viewport, theme: t.theme, phase: ph.phase,
        disabled: ph.disabled ?? null, color: ph.color ?? null,
        background: ph.backgroundColor ?? null, ratio: ph.ratio ?? null,
        threshold: ph.threshold ?? null, unmeasurable: ph.unmeasurable ?? null,
      }))
    : [{ state: t.state, viewport: t.viewport, theme: t.theme, phase: 'all', missing: t.selector }]));
  // Non-vacuity: every viewport x theme must carry a real ENABLED reading in
  // all three phases, otherwise a silently-absent probe would "pass".
  const primaryToneCoverageGaps = PARTIAL_RUN ? [] : VIEWPORTS.flatMap((v) => THEMES.map((th) => {
    const got = manifest.primaryActionTone.filter(
      (t) => t.viewport === v.name && t.theme === th && t.present
        && (t.phases ?? []).length === 3
        && t.phases.every((ph) => ph.present === true && ph.disabled === false
          && typeof ph.ratio === 'number'),
    );
    return got.length === 0
      ? { viewport: v.name, theme: th, reason: 'no enabled 3-phase primary-action tone reading' }
      : null;
  })).filter(Boolean);
  // A probe that wrote through the surface it was measuring is not evidence.
  const primaryToneProbeWrites = manifest.primaryActionTone
    .filter((t) => (t.requestsDuringProbe ?? 0) !== 0)
    .map((t) => ({
      state: t.state, viewport: t.viewport, theme: t.theme,
      requestsDuringProbe: t.requestsDuringProbe,
    }));

  console.log(`primary-action tone readings: ${manifest.primaryActionTone.length}`);
  console.log(`enabled control-label samples: ${manifest.controlLabelSamples.enabled.length}`);
  console.log(`inactive control-label samples: ${manifest.controlLabelSamples.inactive.length}`);
  console.log(`primary-action tone findings (16.10): ${primaryToneFindings.length}`);
  console.log(`primary-action tone coverage gaps: ${primaryToneCoverageGaps.length}`);

  // ACCEPTANCE GATE. Every one of these is an accepted criterion, so every one
  // of them FAILS the evidence run. Reporting a failed criterion as a residual
  // is what made the previous run unusable as acceptance evidence.
  const gate = [
    ['undeclared /api/ paths (fail-closed venue)', undeclared],
    ['horizontal overflow', manifest.horizontalOverflowStates],
    ['low-contrast text (16.10)', manifest.lowContrastFindings],
    ['undeclared contrast exemptions', manifest.undeclaredContrastExemptions],
    // The exemption list is empty now; a non-empty one would mean a sample was
    // moved out of the gated set, which this leg is not allowed to do.
    ['declared contrast exemptions (must be none)', DECLARED_CONTRAST_EXEMPTIONS],
    ['low-contrast primary-action states (16.10)', primaryToneFindings],
    ['primary-action tone coverage gaps (16.10)', primaryToneCoverageGaps],
    ['primary-action tone probe wrote through the surface', primaryToneProbeWrites],
    // 19.2 is asserted by its ABSENCE of a result, so the gate entry is
    // inverted: a missing 1440x1000 keyboard pass is the failure.
    ['19.2 keyboard pass at 1440x1000 missing',
      (!PARTIAL_RUN && manifest.case192KeyboardPass1440.length === 0)
        ? ['no 1440x1000 tab-order walk'] : []],
    ['controls without a visible focus ring (16.11)', manifest.focusRingsMissing],
    ['low-contrast focus rings (16.10)', manifest.lowContrastFocusRings],
    ['required keyboard controls never reached (16.6/16.11)', manifest.keyboardControlGaps],
    ['keyboard scenario coverage gaps (16.11)', manifest.keyboardCoverageGaps],
    ['keyboard operations that did not take effect (16.6/16.7)', manifest.keyboardOperationFailures],
    ['clipping-predicate control failures (16.8)', clippingControlFailures],
    ['computed visibility failures (16.8)', manifest.visibilityFailures],
    ['declared capture subjects outside the captured frame (19.1/14.1-14.3)',
      manifest.subjectsNotFramed],
    ['incoherent post-save surface (R8)', manifest.incoherentPostSaveStates],
  ].filter(([, findings]) => findings.length > 0);
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

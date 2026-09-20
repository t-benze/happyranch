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
 *   16.10 computed contrast for the gated helper/error/warning/ack/
 *         reconciliation/status set, plus the :focus-visible ring.
 *   16.11 the 16.6 tab order walked by real keystrokes at both widths in both
 *         themes, with the ring measured where it actually paints.
 *   19.2  RETIRED as a separate case — superseded by 16.11, which adds both
 *         themes and both widths. Recorded here so the pointer is not lost.
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
  { name: 'ordinary', get: () => OK(snapshot()) },
  {
    name: 'dirty-consequence',
    get: () => OK(snapshot()),
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
 * 16.10 — computed contrast in a REAL browser.
 *
 * WCAG 2.x relative luminance and contrast ratio, measured against the element's
 * EFFECTIVE background (the nearest ancestor with a non-transparent
 * background-color), not an assumed page colour. Reported per state, viewport
 * and theme; nothing here is asserted from a design token or a DOM presence.
 */
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
  const panel = document.querySelector('#capacity-panel-heading')?.closest('div');
  const candidates = panel ? [...panel.querySelectorAll('*')].filter((node) => {
    if (node.matches('script, style, svg, path, canvas')) return false;
    const own = [...node.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent ?? '')
      .join('')
      .trim();
    if (own.length === 0) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }) : [];

  /**
   * Accepted 16.10 (as extended by the review) gates HELPER, ERROR, WARNING,
   * ACKNOWLEDGMENT, RECONCILIATION and STATUS copy plus the focus ring. Every
   * other capacity string is still measured and reported, but its colour comes
   * from a SHARED token this bounded screen has no authority to change, so it
   * is reported as an out-of-scope observation rather than silently omitted.
   */
  const GATED_SELF = '[role="alert"], [role="status"], [id$="-help"], [id$="-guidance"],'
    + ' [id$="-error"], label[for^="capacity-"]';
  const GATED_ROOTS = '#capacity-override, #capacity-outcome, [role="alert"], [role="status"]';
  const isGated = (el) => el.matches(GATED_SELF) || el.closest(GATED_ROOTS) !== null;

  const out = [];
  for (const el of candidates) {
    const selector = el.id
      ? `#${el.id}`
      : `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(/\s+/).slice(0, 2).join('.')}`;
    const style = getComputedStyle(el);
    const fg = parse(style.color);
    const bg = effectiveBackground(el);
    if (!fg || !bg) { out.push({ selector, present: true, unmeasurable: style.color }); continue; }
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
      gated: isGated(el),
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

  return {
    tag: el.tagName,
    id: el.id || null,
    text: (el.textContent ?? '').trim().slice(0, 40) || null,
    focusVisible: el.matches(':focus-visible'),
    outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
    boxShadow: style.boxShadow,
    paintedRingLayers: ringLayers.map((l) => l.layer),
    // The honest predicate: keyboard focus is actually matched AND something
    // actually paints.
    focusRingVisible: el.matches(':focus-visible') && (outlineReal || ringLayers.length > 0),
    // The translucent ring composited over what is behind it, against that
    // same background — what a person actually has to see.
    ringContrast: ringSource
      ? Number(ratio(over(ringSource, surroundBg), surroundBg).toFixed(2))
      : null,
  };
};

/**
 * Tab in from the document start so focus is keyboard-driven, stop on the first
 * capacity control, then record the panel's tab order.
 */
async function walkTabOrder(page, steps = 7) {
  await page.evaluate(() => document.body.focus());
  let reached = false;
  for (let i = 0; i < 80 && !reached; i += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => document.activeElement?.id === 'capacity-workers');
  }
  if (!reached) throw new Error('never tabbed to #capacity-workers');
  const order = [];
  for (let i = 0; i < steps; i += 1) {
    order.push(await page.evaluate(RING_PROBE));
    if (i < steps - 1) await page.keyboard.press('Tab');
  }
  return order;
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

        const file = `capacity-${state.name}-${viewport.name}-${theme}.png`;
        await page.screenshot({ path: join(outDir, file), fullPage: true });

        // Computed visibility + contrast receipts for the a11y cases, taken in
        // a REAL browser rather than from DOM presence.
        const receipt = await page.evaluate(() => {
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
              const clipsY = (s.overflowY === 'hidden' || s.overflowY === 'clip')
                && el.scrollHeight <= el.clientHeight + 1;
              const clipsX = (s.overflowX === 'hidden' || s.overflowX === 'clip')
                && el.scrollWidth <= el.clientWidth + 1;
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
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const kbContext = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
        deviceScaleFactor: 1,
      });
      await kbContext.route('**/*', (route) => (route.request().url().startsWith(origin)
        ? route.continue()
        : (blockedExternal.push(route.request().url()), route.abort())));
      active = { name: 'keyboard', get: () => ({ status: 200, json: snapshot() }) };
      getIndex = 0;
      putIndex = 0;
      const kbPage = await kbContext.newPage();
      await kbPage.addInitScript((t) => {
        window.sessionStorage.setItem('happyranch.token', 'synthetic');
        window.localStorage.setItem('happyranch.theme', t);
      }, theme);
      await kbPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
      await kbPage.waitForSelector('#capacity-workers');
      const order = await walkTabOrder(kbPage, 7);
      // Still keyboard-focused on the LAST control of the walk; re-focusing
      // programmatically here would erase the ring from the screenshot.
      await kbPage.screenshot({
        path: join(outDir, `capacity-keyboard-focus-${viewport.name}-${theme}.png`),
        fullPage: true,
      });
      tabOrders.push({ viewport: viewport.name, theme, order });
      await kbContext.close();
    }
  }

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
    // REPORTED, with its cause named: decorative/muted capacity copy whose
    // colour is the SHARED `--color-text-muted` (and the shared Button
    // primitive's accent foreground), which this bounded screen may not change.
    // Disclosed as an out-of-scope observation, never as a met criterion.
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
              sample: c.text, occurrences: 0,
            });
          }
          seen.get(key).occurrences += 1;
        }
      }
      return [...seen.values()].sort((a, b) => a.ratio - b.ratio);
    })(),
    contrastSamplesMeasured: results.reduce((n, r) => n + (r.contrast ?? []).length, 0),
    focusRingsMissing: tabOrders.flatMap((t) => t.order
      .filter((o) => o && (!o.focusVisible || !o.focusRingVisible))
      .map((o) => ({
        viewport: t.viewport, theme: t.theme, control: o.id ?? o.text,
        focusVisible: o.focusVisible, boxShadow: o.boxShadow, outline: o.outline,
      }))),
    // GATED, not merely reported: accepted 16.10 forbids a low-contrast focus
    // ring, and a ring that paints but cannot be seen does not meet it.
    lowContrastFocusRings: tabOrders.flatMap((t) => t.order
      .filter((o) => o && o.focusRingVisible && o.ringContrast !== null && o.ringContrast < 3)
      .map((o) => ({
        viewport: t.viewport, theme: t.theme, control: o.id ?? o.text, ratio: o.ringContrast,
      }))),
    visibilityFailures: results
      .filter((r) => !r.allAlertsVisible || (r.hiddenImportantText ?? []).length > 0)
      .map((r) => ({
        file: r.file,
        allAlertsVisible: r.allAlertsVisible,
        hiddenImportantText: r.hiddenImportantText,
      })),
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
  console.log(`low-contrast observations outside this scope (shared tokens): ${manifest.contrastOutsideThisScope.length}`);
  console.log(`controls without a visible focus ring (16.11): ${manifest.focusRingsMissing.length}`);
  console.log(`focus rings below 3:1 against their surround (16.10/16.11): ${manifest.lowContrastFocusRings.length}`);
  console.log(`visibility failures (16.8): ${manifest.visibilityFailures.length}`);

  // ACCEPTANCE GATE. Every one of these is an accepted criterion, so every one
  // of them FAILS the evidence run. Reporting a failed criterion as a residual
  // is what made the previous run unusable as acceptance evidence.
  const gate = [
    ['undeclared /api/ paths (fail-closed venue)', undeclared],
    ['horizontal overflow', manifest.horizontalOverflowStates],
    ['low-contrast text (16.10)', manifest.lowContrastFindings],
    ['controls without a visible focus ring (16.11)', manifest.focusRingsMissing],
    ['low-contrast focus rings (16.10)', manifest.lowContrastFocusRings],
    ['computed visibility failures (16.8)', manifest.visibilityFailures],
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

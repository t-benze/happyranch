/**
 * TASK-5553 (GH-688 Phase 1 follow-up) — Reply Delivery rail + purpose-fidelity
 * browser evidence, Mode A (prod build + node /api mock).
 *
 * Deterministic target-state captures of the redesigned rail:
 *   - THR-5553  populated + the EXACT founder regression: system seq 39 +
 *               founder message seq 40 + one RUNNING REPLY pair range 39-40
 *               for investment_advisor, PLUS concurrent multi-agent
 *               deliveries (ops_lead running, qa_engineer queued,
 *               support_lead retry_required) — proves the compact hierarchy
 *               and that exactly ONE replying row renders for the REPLY.
 *   - THR-999  settled/empty: fully-settled projection → section omitted.
 *
 * Viewports: realistic rail width (1440x900), narrow (1280x720), wide
 * (1920x1080). Both themes. Every capture waits on a REAL rendered control
 * (the Reply delivery section / a disclosure button), never a skeleton.
 *
 * Probes (writes out/probe-results.json): enumerate ALL disclosure controls,
 * ancestor overflow/clipping walk, per-row bounding-rect containment, full
 * untruncated agent identity, disclosure toggle + keyboard (Enter)
 * activation, aria-expanded/aria-controls wiring.
 *
 * Run: needs `npm run build` in web/ first.
 *   node scripts/screenshot-harness/shot-thr688-reply-delivery.mjs
 * Out: web/scripts/screenshot-harness/out/thr688-<name>-<theme>-<vp>.png
 *      web/scripts/screenshot-harness/out/thr688-probe-results.json
 */
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ */
/*  Wire fixtures                                                     */
/* ------------------------------------------------------------------ */

const THREAD_LIST = (id, subject) => ({
  thread_id: id,
  subject,
  status: 'open',
  started_at: '2026-08-24T10:00:00Z',
  archived_at: null,
  forwarded_from_id: null,
  forwarded_from_kind: null,
  turn_cap: 500,
  turns_used: 12,
  summary: null,
  transcript_path: null,
  composed_from_dream_id: null,
  last_speaker: 'founder',
});

function msg(seq, speaker, body, extra = {}) {
  return {
    seq,
    speaker,
    kind: 'message',
    body_markdown: body,
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: '2026-08-24T11:00:00Z',
    responder_status: [],
    ...extra,
  };
}

function sys(seq, kindTag, responder_status = []) {
  return {
    seq,
    speaker: 'founder',
    kind: 'system',
    body_markdown: null,
    decline_reason: null,
    system_payload: { kind_tag: kindTag, status: 'ok' },
    attachments: [],
    created_at: '2026-08-24T11:05:00Z',
    responder_status,
  };
}

const rs = (agent_name, status, purpose, started_at = null) => ({
  agent_name,
  purpose,
  status,
  responded_at: null,
  started_at,
  decline_reason: null,
  category: null,
});

const rd = (agent_name, state, from_seq, through_seq, over = {}) => ({
  agent_name,
  state,
  from_seq,
  through_seq,
  coalesced_message_count: through_seq - from_seq + 1,
  started_at: state === 'running' ? '2026-08-24T11:00:30Z' : null,
  updated_at: '2026-08-24T11:00:30Z',
  last_terminal_reason: null,
  ...over,
});

// THR-5553 — populated + exact regression (system seq 39 + founder msg 40 +
// one running REPLY range 39-40) + concurrent multi-agent deliveries.
const TARGET_MESSAGES = [
  msg(1, 'founder', 'Kickoff — Macau venue sweep.', {
    responder_status: [
      rs('investment_advisor', 'replied', 'reply'),
      rs('ops_lead', 'replied', 'reply'),
      rs('qa_engineer', 'replied', 'reply'),
    ],
  }),
  sys(39, 'resumed', [rs('investment_advisor', 'working', 'reply', '2026-08-24T11:00:30Z')]),
  msg(40, 'founder', 'any thoughts on the two shortlist candidates?'),
];
const TARGET_DELIVERY = [
  rd('investment_advisor', 'running', 39, 40),
  rd('ops_lead', 'running', 41, 42, { started_at: '2026-08-24T11:04:30Z' }),
  rd('qa_engineer', 'queued', 41, 42),
  rd('support_lead', 'retry_required', 43, 46, { last_terminal_reason: 'timeout' }),
];

// THR-999 — fully settled → rail section omitted.
const EMPTY_MESSAGES = [msg(1, 'founder', 'Settled thread')];
const EMPTY_DELIVERY = [];

const dashboardSummary = {
  heartbeat: [],
  narrative_counts: { completed_today: 0, failed_today: 0, escalated_open: 0, kb_added_today: 0, agents_active_now: 0, spend_today_usd: 0 },
  escalations: [],
  active_by_team: [],
  recent_activity: [],
  updates_this_week: [],
  org_pulse: [],
  org_age_days: 30,
  server_now: '2026-08-24T11:30:00Z',
};

function threadDetail(id, subject, messages, delivery) {
  return {
    ...THREAD_LIST(id, subject),
    participants: ['investment_advisor', 'ops_lead', 'qa_engineer', 'support_lead'],
    messages,
    reply_delivery: delivery,
  };
}

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/threads`, json: { threads: [
    THREAD_LIST('THR-5553', 'Reply delivery evidence'),
    THREAD_LIST('THR-999', 'Settled thread'),
  ] } },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-5553`, json: threadDetail('THR-5553', 'Reply delivery evidence', TARGET_MESSAGES, TARGET_DELIVERY) },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-5553/messages`, json: { messages: TARGET_MESSAGES, reply_delivery: TARGET_DELIVERY } },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-999`, json: threadDetail('THR-999', 'Settled thread', EMPTY_MESSAGES, EMPTY_DELIVERY) },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-999/messages`, json: { messages: EMPTY_MESSAGES, reply_delivery: EMPTY_DELIVERY } },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-5553/tasks`, json: { tasks: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/THR-999/tasks`, json: { tasks: [] } },
  { path: `/api/v1/orgs/${SLUG}/tokens`, json: { rollup: [] } },
  { path: `/api/v1/orgs/${SLUG}/agents`, json: { agents: [
    { name: 'investment_advisor', team: 'advisory', role: 'worker' },
    { name: 'ops_lead', team: 'operations', role: 'worker' },
    { name: 'qa_engineer', team: 'engineering', role: 'worker' },
    { name: 'support_lead', team: 'support', role: 'worker' },
  ] } },
  { path: `/api/v1/orgs/${SLUG}/dashboard/summary`, json: dashboardSummary },
];

/* ------------------------------------------------------------------ */
/*  playwright-cli driver                                             */
/* ------------------------------------------------------------------ */

function pw(session, args) {
  return new Promise((resolve, reject) => {
    const p = spawn('playwright-cli', [`-s=${session}`, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('exit', (code) =>
      code === 0 ? resolve(out.trim()) : reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${err}`)),
    );
    p.on('error', reject);
  });
}

async function gotoAndWait(session, url, waitSelector) {
  await pw(session, ['open']);
  try {
    await pw(session, ['goto', url]);
    // Wait for the REAL reply-delivery section (never the skeleton).
    for (let i = 0; i < 60; i++) {
      const found = await pw(session, ['find', waitSelector]).catch(() => '');
      if (found && !/No results/.test(found)) return;
      await sleep(250);
    }
    throw new Error(`waitFor ${waitSelector} timed out at ${url}`);
  } catch (e) {
    await pw(session, ['close']).catch(() => {});
    throw e;
  }
}

// eval helper: runs page.evaluate with a function that returns a JSON string.
// playwright-cli prints the result as a quoted JS string under `### Result`
// (e.g. "{\"a\":1}"), so we extract, unquote, and unescape before parsing.
async function evalJson(session, fnSrc) {
  const raw = await pw(session, ['eval', fnSrc]);
  const m = raw.match(/### Result\s*\n([\s\S]*?)(?:\n###|\n\n|$)/);
  const chunk = (m ? m[1] : raw).trim();
  const unquoted = chunk.replace(/^"/, '').replace(/"$/, '').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
  return JSON.parse(unquoted);
}

/** Parse an eval result printed by playwright-cli into a plain JS string. */
async function evalText(session, fnSrc) {
  const raw = await pw(session, ['eval', fnSrc]);
  const m = raw.match(/### Result\s*\n([\s\S]*?)(?:\n###|\n\n|$)/);
  const chunk = (m ? m[1] : raw).trim();
  return chunk.replace(/^"/, '').replace(/"$/, '').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
}

const PROBE = {
  /** Enumerate ALL disclosure controls + row/identity diagnostics. */
  controls: `() => {
    const rail = document.querySelector('[aria-label="Reply delivery"]');
    const buttons = Array.from(document.querySelectorAll('button[aria-controls]'))
      .map((b) => ({ label: b.getAttribute('aria-label'), expanded: b.getAttribute('aria-expanded'), controls: b.getAttribute('aria-controls') }));
    const rows = rail ? Array.from(rail.querySelectorAll('li')).map((li) => {
      const name = li.querySelector('span[class*="font-mono"]');
      return { name: name ? name.textContent : null, truncated: name ? name.className.includes('truncate') : null };
    }) : [];
    return JSON.stringify({ railPresent: !!rail, buttons, rows, railText: rail ? rail.textContent : '' });
  }`,
  /** Ancestor overflow/clipping walk + per-row bounding-rect containment. */
  clipping: `() => {
    const rail = document.querySelector('[aria-label="Reply delivery"]');
    const vp = { w: window.innerWidth, h: window.innerHeight };
    if (!rail) return JSON.stringify({ railPresent: false, vp });
    const r = rail.getBoundingClientRect();
    const inViewport = r.left >= 0 && r.right <= vp.w && r.top >= 0;
    const clippedAncestors = [];
    for (let el = rail.parentElement; el; el = el.parentElement) {
      const cs = getComputedStyle(el);
      if (['hidden', 'scroll', 'auto', 'clip'].includes(cs.overflowX) || ['hidden', 'scroll', 'auto', 'clip'].includes(cs.overflowY)) {
        const er = el.getBoundingClientRect();
        clippedAncestors.push({ cls: el.className, overflow: cs.overflowX + '/' + cs.overflowY, rect: { l: er.left, r: er.right, t: er.top, b: er.bottom } });
      }
    }
    const rows = Array.from(rail.querySelectorAll('li')).map((li) => {
      const er = li.getBoundingClientRect();
      return { name: li.textContent.slice(0, 40), insideRail: er.left >= r.left - 1 && er.right <= r.right + 1, inViewport: er.left >= 0 && er.right <= vp.w };
    });
    return JSON.stringify({ railPresent: true, vp, railRect: { l: r.left, r: r.right, t: r.top, b: r.bottom }, inViewport, clippedAncestors, rows });
  }`,
  /** Enumerate the transcript-tail replying bubbles for the regression agent. */
  tailBubbles: `() => {
    const labels = Array.from(document.querySelectorAll('[aria-label]'))
      .map((el) => el.getAttribute('aria-label'))
      .filter((l) => l && / is (replying|queued)$/.test(l));
    const invAdvisor = labels.filter((l) => l.startsWith('investment_advisor'));
    return JSON.stringify({ allTailBubbles: labels, investmentAdvisor: invAdvisor });
  }`,
  /** Red-side displacement probe: translate the rail and verify every row's
   *  bounding rect stays CONTAINED inside the rail (layout containment is
   *  provided by the rail, never the viewport boundary), then restore. */
  displace: `() => {
    const rail = document.querySelector('[aria-label="Reply delivery"]');
    const vp = { w: window.innerWidth, h: window.innerHeight };
    if (!rail) return JSON.stringify({ railPresent: false, vp });
    const before = rail.getBoundingClientRect();
    rail.style.transform = 'translateX(-24px)';
    const moved = rail.getBoundingClientRect();
    const rows = Array.from(rail.querySelectorAll('li')).map((li) => {
      const er = li.getBoundingClientRect();
      return {
        name: li.textContent.slice(0, 24),
        insideRail: er.left >= moved.left - 1 && er.right <= moved.right + 1,
        inViewport: er.left >= 0 && er.right <= vp.w,
      };
    });
    rail.style.transform = '';
    const restored = rail.getBoundingClientRect();
    return JSON.stringify({
      railPresent: true, vp,
      before: { l: Math.round(before.left), r: Math.round(before.right) },
      moved: { l: Math.round(moved.left), r: Math.round(moved.right) },
      restored: { l: Math.round(restored.left), r: Math.round(restored.right) },
      rows,
    });
  }`,
};

async function runProbes(session, results, label) {
  try {
    results.push({ label, ...await evalJson(session, PROBE.controls) });
  } catch (e) {
    results.push({ label, error: String(e) });
  }
  try {
    results.push({ label: `${label} (clipping)`, ...await evalJson(session, PROBE.clipping) });
  } catch (e) {
    results.push({ label: `${label} (clipping)`, error: String(e) });
  }
  try {
    results.push({ label: `${label} (tail)`, ...await evalJson(session, PROBE.tailBubbles) });
  } catch (e) {
    results.push({ label: `${label} (tail)`, error: String(e) });
  }
  try {
    results.push({ label: `${label} (displacement)`, ...await evalJson(session, PROBE.displace) });
  } catch (e) {
    results.push({ label: `${label} (displacement)`, error: String(e) });
  }
}

async function shoot({ session, url, theme, viewport, outName, results, label, expand = false }) {
  await pw(session, ['open']);
  try {
    await pw(session, ['resize', String(viewport[0]), String(viewport[1])]);
    await pw(session, ['goto', url]);
    await pw(session, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(session, ['reload']);
    for (let i = 0; i < 60; i++) {
      const found = await pw(session, ['find', 'Reply delivery']).catch(() => '');
      if (found && !/No results/.test(found)) break;
      await sleep(250);
    }
    await sleep(400);
    await runProbes(session, results, `${label}-${theme}-${viewport[0]}x${viewport[1]}`);
    if (expand) {
      // Expand the FIRST queued/retry disclosure (qa_engineer) to show the
      // collapsed coalescing detail + verify the disclosure contract.
      await pw(session, [
        'eval',
        "()=>{const b=[...document.querySelectorAll('button[aria-controls]')].find(x=>x.getAttribute('aria-label')&&x.getAttribute('aria-label').includes('qa_engineer'));if(b)b.click();}",
      ]);
      await sleep(300);
      const after = await evalJson(session, PROBE.controls).catch(() => null);
      if (after) results.push({ label: `${label}-${theme}-expanded`, buttons: after.buttons });
    }
    await pw(session, ['screenshot', `--filename=${join(OUT, outName)}`]);
  } finally {
    await pw(session, ['close']).catch(() => {});
  }
}

/* ------------------------------------------------------------------ */
/*  Main                                                              */
/* ------------------------------------------------------------------ */

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
const results = [];
try {
  const viewports = [
    [1440, 900], // realistic rail width (app default)
    [1280, 720], // narrow
    [1920, 1080], // wide
  ];
  for (const theme of ['light', 'dark']) {
    for (const [w, h] of viewports) {
      console.log(`[thr688] ${theme} ${w}x${h} — target rail`);
      await shoot({
        session: `thr688-${theme}-${w}`,
        url: `${srv.url}/orgs/${SLUG}/threads/THR-5553`,
        theme,
        viewport: [w, h],
        outName: `thr688-target-${theme}-${w}x${h}.png`,
        results,
        label: `target`,
        expand: true,
      });

      console.log(`[thr688] ${theme} ${w}x${h} — settled (empty)`);
      await shoot({
        session: `thr688-empty-${theme}-${w}`,
        url: `${srv.url}/orgs/${SLUG}/threads/THR-999`,
        theme,
        viewport: [w, h],
        outName: `thr688-empty-${theme}-${w}x${h}.png`,
        results,
        label: `empty`,
      });
    }
  }
  // Keyboard probe on the final session: focus a disclosure and toggle with
  // Enter (keyboard/screen-reader accessibility).
  await pw('thr688-kbd', ['open']);
  try {
    await pw('thr688-kbd', ['resize', '1440', '900']);
    await pw('thr688-kbd', ['goto', `${srv.url}/orgs/${SLUG}/threads/THR-5553`]);
    await pw('thr688-kbd', ['localstorage-set', 'happyranch.theme', 'light']);
    await pw('thr688-kbd', ['reload']);
    await sleep(1800);
    await pw('thr688-kbd', ['eval', "()=>{const b=[...document.querySelectorAll('button[aria-controls]')].find(x=>x.getAttribute('aria-label')&&x.getAttribute('aria-label').includes('qa_engineer'));if(b){b.focus();window.__probeFocused=b;}}"]);
    const before = await evalText('thr688-kbd', "()=>{const b=window.__probeFocused;return JSON.stringify({expanded:b?b.getAttribute('aria-expanded'):null,label:b?b.getAttribute('aria-label'):null})}");
    await pw('thr688-kbd', ['press', 'Enter']);
    await sleep(250);
    const after = await evalText('thr688-kbd', "()=>{const b=window.__probeFocused;const details=b?document.getElementById(b.getAttribute('aria-controls')):null;return JSON.stringify({expanded:b?b.getAttribute('aria-expanded'):null,detailsVisible:details?getComputedStyle(details).display!=='none'&&details.offsetParent!==null:false})}");
    results.push({ label: 'keyboard-enter-toggle', before, after });
  } finally {
    await pw('thr688-kbd', ['close']).catch(() => {});
  }

  await writeFile(join(OUT, 'thr688-probe-results.json'), JSON.stringify(results, null, 2));
  console.log('[thr688] probes+shots done ->', OUT);
} finally {
  await srv.close();
}

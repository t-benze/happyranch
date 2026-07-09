/**
 * THR-069 msg85 evidence (TASK-2417): the per-participant Remove control in the
 * thread detail rail + its confirm step, captured in light AND dark.
 *
 * Mode A (prod build + node /api mock). Two shots per theme:
 *   1. remove-control — participants rail with the per-row Remove (✕) affordance
 *      revealed (it is hover-gated in normal use; forced visible here so the
 *      static PNG shows it).
 *   2. remove-confirm — the RemoveParticipantDialog confirm step after clicking
 *      a participant's Remove control.
 *
 * Run: node scripts/screenshot-harness/shot-2417.mjs   (needs `npm run build`)
 * Out: web/scripts/screenshot-harness/out/thr069-remove-{control,confirm}-{light,dark}.png
 */
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const THREAD_ID = 'THR-DEMO-1';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function threadDetail() {
  return {
    thread_id: THREAD_ID,
    subject: 'Refund policy for late shipments',
    status: 'open',
    started_at: '2026-07-08T10:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 3,
    summary: null,
    transcript_path: null,
    composed_from_dream_id: null,
    participants: ['founder', 'engineering_head', 'qa_engineer'],
    messages: [],
  };
}

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/agents`, json: { agents: [
    { name: 'engineering_head', team: 'core', role: 'manager', executor: 'claude', description: null, repos: {}, system_prompt: '' },
    { name: 'qa_engineer', team: 'core', role: 'worker', executor: 'claude', description: null, repos: {}, system_prompt: '' },
  ] } },
  { path: `/api/v1/orgs/${SLUG}/threads`, json: { threads: [threadDetail()] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}`, json: () => threadDetail() },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/messages`, json: { messages: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/tasks`, json: [] },
];

function pw(session, args) {
  return new Promise((resolve, reject) => {
    const p = spawn('playwright-cli', [`-s=${session}`, ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let err = '';
    p.stderr.on('data', (d) => (err += d));
    p.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${err}`)),
    );
    p.on('error', reject);
  });
}

async function runTheme(baseUrl, theme) {
  const s = `t2417-${theme}`;
  const detailUrl = `${baseUrl}/orgs/${SLUG}/threads/${THREAD_ID}`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', detailUrl]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1300);
    // Reveal the hover-gated Remove controls so the static PNG shows them.
    await pw(s, ['eval', "() => document.querySelectorAll('button[aria-label^=\"Remove \"]').forEach((b) => { b.style.opacity = '1'; })"]);
    await sleep(300);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-remove-control-${theme}.png`)}`]);

    // Open the confirm step for one participant.
    await pw(s, ['click', "getByRole('button', { name: 'Remove qa_engineer' })"]);
    await sleep(600);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-remove-confirm-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2417] capturing ${theme}…`);
    await runTheme(srv.url, theme);
  }
  // eslint-disable-next-line no-console
  console.log('[shot-2417] done ->', OUT);
} finally {
  await srv.close();
}

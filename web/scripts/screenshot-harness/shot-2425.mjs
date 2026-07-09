/**
 * THR-069 msg85 evidence (TASK-2425): per-participant Remove control on the
 * thread detail rail, mirroring the invite affordance.
 *
 * Mode A (prod build + node /api mock), STATEFUL: the remove POST filters the
 * agent out of the served participant list, and the mutation invalidates
 * ['thread', slug, threadId] — so the invalidated detail refetch drops the
 * removed agent from the rail without a full-page reload.
 *
 * Flow per theme:
 *   detail -> hover a participant (reveal ✕) -> shot "control"
 *          -> click ✕ -> confirm dialog -> shot "confirm"
 *          -> click Remove -> rail refetches -> shot "after" (agent gone)
 *
 * Run: node scripts/screenshot-harness/shot-2425.mjs   (needs `npm run build`)
 * Out: web/scripts/screenshot-harness/out/thr069-remove-{control,confirm,after}-{light,dark}.png
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
const REMOVE = 'qa_engineer';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Mutable participant list the mock serves; the remove POST filters it.
const state = { participants: ['founder', 'engineering_manager', 'qa_engineer'] };

function detail() {
  return {
    thread_id: THREAD_ID,
    subject: 'Ship the runtime health page',
    status: 'open',
    started_at: '2026-07-08T10:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 2,
    summary: null,
    transcript_path: null,
    participants: [...state.participants],
    messages: [],
  };
}

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/threads`, json: { threads: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}`, json: () => detail() },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/messages`, json: { messages: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/tasks`, json: [] },
  {
    method: 'POST',
    path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/remove-participant`,
    handler: (req, res) => {
      state.participants = state.participants.filter((n) => n !== REMOVE);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ thread_id: THREAD_ID, agent_name: REMOVE, system_message_seq: 3 }));
    },
  },
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
  state.participants = ['founder', 'engineering_manager', 'qa_engineer']; // reset
  const s = `t2425-${theme}`;
  const url = `${baseUrl}/orgs/${SLUG}/threads/${THREAD_ID}`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', url]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1400);
    // Reveal the hover-gated ✕ on the target participant row.
    await pw(s, ['hover', `getByRole('button', { name: 'Remove ${REMOVE}' })`]);
    await sleep(300);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-remove-control-${theme}.png`)}`]);
    // Click ✕ -> confirm dialog (the confirm step before anything fires).
    await pw(s, ['click', `getByRole('button', { name: 'Remove ${REMOVE}' })`]);
    await sleep(500);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-remove-confirm-${theme}.png`)}`]);
    // Confirm -> mutation fires -> invalidate -> rail refetches without the agent.
    await pw(s, ['click', "getByRole('button', { name: 'Remove', exact: true })"]);
    await sleep(1000);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-remove-after-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2425] capturing ${theme}…`);
    await runTheme(srv.url, theme);
  }
  // eslint-disable-next-line no-console
  console.log('[shot-2425] done ->', OUT);
} finally {
  await srv.close();
}

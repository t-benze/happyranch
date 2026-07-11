/**
 * THR-061 seq197 evidence (TASK-2603): TWO FE-only thread-detail polish items.
 *
 *  ITEM 1 — @-mention suggestion list must open UPWARD so it stays fully in the
 *           viewport (the composer sits near the bottom → a downward list clips).
 *  ITEM 2 — LINKED TASKS pills must be FULL-WIDTH rows (status LEFT, TASK-ID
 *           RIGHT, justify-between) so a busy rail reads as a clean column.
 *
 * Mode A (prod build + node /api mock). Seeds a BUSY thread: 12 linked tasks
 * (mixed statuses, exceeds the max-h-24 cap → internal scroll) + 8 agents so
 * the @ popup fills near the viewport bottom. Two shots per theme:
 *   - `rail`    : the linked-tasks rail (item 2 alignment)
 *   - `mention` : composer @ popup OPEN near the bottom (item 1 clip fix)
 *
 * BEFORE/AFTER is driven by rebuilding the app between phases; pass the phase
 * via env:  PHASE=before node scripts/screenshot-harness/shot-2603.mjs
 * Out: web/scripts/screenshot-harness/out/thr061-{rail,mention}-{before,after}-{light,dark}.png
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
const PHASE = process.env.PHASE === 'before' ? 'before' : 'after';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A busy, realistic linked-tasks rail: mixed active + terminal statuses so the
// active-first ordering, every color token, and the full-width alignment all
// show. 12 rows exceed the max-h-24 cap → the internal scroll engages.
const TASKS = [
  { id: 'TASK-2603', status: 'in_progress', agent: 'frontend_engineer' },
  { id: 'TASK-2592', status: 'blocked', agent: 'frontend_engineer' },
  { id: 'TASK-2590', status: 'escalated', agent: 'engineering_manager' },
  { id: 'TASK-2574', status: 'pending', agent: 'qa_engineer' },
  { id: 'TASK-2553', status: 'in_progress', agent: 'code_reviewer' },
  { id: 'TASK-2550', status: 'completed', agent: 'frontend_engineer' },
  { id: 'TASK-2546', status: 'completed', agent: 'backend_engineer' },
  { id: 'TASK-2535', status: 'failed', agent: 'qa_engineer' },
  { id: 'TASK-2523', status: 'cancelled', agent: 'engineering_manager' },
  { id: 'TASK-2508', status: 'completed', agent: 'frontend_engineer' },
  { id: 'TASK-2485', status: 'resolved_superseded', agent: 'code_reviewer' },
  { id: 'TASK-2475', status: 'archived', agent: 'backend_engineer' },
].map((t) => ({
  id: t.id,
  status: t.status,
  brief: `Linked task ${t.id}`,
  assigned_agent: t.agent,
  created_at: '2026-07-08T10:00:00Z',
  parent_task_id: null,
}));

const AGENTS = [
  { name: 'engineering_manager', team: 'engineering', role: 'manager' },
  { name: 'frontend_engineer', team: 'engineering', role: 'worker' },
  { name: 'backend_engineer', team: 'engineering', role: 'worker' },
  { name: 'code_reviewer', team: 'engineering', role: 'worker' },
  { name: 'qa_engineer', team: 'engineering', role: 'worker' },
  { name: 'product_lead', team: 'product', role: 'manager' },
  { name: 'content_writer', team: 'content', role: 'worker' },
  { name: 'design_lead', team: 'design', role: 'manager' },
].map((a) => ({ ...a, executor: 'claude', description: null, repos: {}, system_prompt: '' }));

function detail() {
  return {
    thread_id: THREAD_ID,
    subject: 'Ship the thread-detail polish batch',
    status: 'open',
    started_at: '2026-07-08T10:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 6,
    summary: null,
    transcript_path: null,
    participants: ['founder', 'engineering_manager', 'frontend_engineer', 'qa_engineer'],
    messages: [],
  };
}

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/agents`, json: { agents: AGENTS } },
  { path: `/api/v1/orgs/${SLUG}/threads`, json: { threads: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}`, json: () => detail() },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/messages`, json: { messages: [] } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/tasks`, json: TASKS },
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
  const s = `t2603-${theme}`;
  const url = `${baseUrl}/orgs/${SLUG}/threads/${THREAD_ID}`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', url]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1600);
    // Item 2 — the linked-tasks rail alignment (no popup).
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr061-rail-${PHASE}-${theme}.png`)}`]);
    // Item 1 — open the @ popup in the composer near the viewport bottom.
    await pw(s, ['click', "getByRole('textbox')"]);
    await sleep(150);
    await pw(s, ['type', '@']);
    await sleep(500);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr061-mention-${PHASE}-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2603] (${PHASE}) capturing ${theme}…`);
    await runTheme(srv.url, theme);
  }
  // eslint-disable-next-line no-console
  console.log(`[shot-2603] (${PHASE}) done ->`, OUT);
} finally {
  await srv.close();
}

/**
 * THR-069 msg78 evidence (TASK-2324): the task LIST must reflect the NEW status
 * after a status change on the task DETAIL page.
 *
 * Mode A (prod build + node /api mock), but STATEFUL: the mock flips the task's
 * status from `in_progress` -> `cancelled` when the detail-page Cancel POST
 * lands. The flow uses CLIENT-SIDE (SPA) navigation throughout so the React
 * Query cache from the list page persists — that is the whole point: only if
 * the mutation invalidates the `tasks-roots-infinite` family will the list
 * refetch and show `cancelled`. Full-page reloads would refetch unconditionally
 * and prove nothing.
 *
 * Flow per theme: list (in_progress) -> click row -> detail -> Cancel -> confirm
 * -> click "All tasks" (SPA back) -> list refetches -> screenshot shows cancelled.
 *
 * Run: node scripts/screenshot-harness/shot-2324.mjs   (needs `npm run build`)
 * Out: web/scripts/screenshot-harness/out/thr069-list-{before,after}-{light,dark}.png
 */
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const TASK_ID = 'TASK-DEMO-1';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Mutable task status the mock serves; the Cancel POST flips it.
const state = { status: 'in_progress' };

function taskRecord() {
  return {
    task_id: TASK_ID,
    team: 'engineering',
    brief: 'Demo task for THR-069 list cache invalidation',
    status: state.status,
    block_kind: null,
    assigned_agent: 'frontend_engineer',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-07-08T10:00:00Z',
    updated_at: '2026-07-08T10:05:00Z',
    closed_at: null,
    cancelled_at: state.status === 'cancelled' ? '2026-07-08T10:06:00Z' : null,
    session_timeout_seconds: null,
  };
}

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  // task LIST family read by TasksPage (useTasksRootsInfinite)
  {
    path: `/api/v1/orgs/${SLUG}/tasks/roots`,
    json: () => ({ tasks: [taskRecord()], next_cursor: null }),
  },
  // bounded list family (kept consistent so any surface reads the same status)
  {
    path: `/api/v1/orgs/${SLUG}/tasks`,
    json: () => ({ tasks: [taskRecord()], next_cursor: null }),
  },
  // detail envelope
  {
    path: `/api/v1/orgs/${SLUG}/tasks/${TASK_ID}`,
    json: () => ({
      task: taskRecord(),
      results: [],
      audit_log: [],
      revisit_chain: [],
      direct_revisits: [],
      predecessor_prior_status: null,
      active_chain: null,
      superseded_by_task_id: null,
    }),
  },
  // recall tree (detail page fires this)
  {
    path: `/api/v1/orgs/${SLUG}/tasks/${TASK_ID}/recall`,
    json: () => ({
      task_id: TASK_ID,
      assigned_agent: 'frontend_engineer',
      brief: 'Demo task for THR-069 list cache invalidation',
      status: state.status,
      output_summary: null,
      children: [],
    }),
  },
  // jobs list for this task (detail page fires this)
  { path: `/api/v1/orgs/${SLUG}/jobs/`, json: { jobs: [] } },
  // the status-changing mutation: flip state, then 200
  {
    method: 'POST',
    path: `/api/v1/orgs/${SLUG}/tasks/${TASK_ID}/cancel`,
    handler: (req, res) => {
      state.status = 'cancelled';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
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
  state.status = 'in_progress'; // reset for a clean run
  const s = `t2324-${theme}`;
  const listUrl = `${baseUrl}/orgs/${SLUG}/tasks`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', listUrl]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1200);
    // BEFORE: list shows in_progress
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-list-before-${theme}.png`)}`]);

    // SPA nav to detail by clicking the task row link
    await pw(s, ['click', `getByRole('link', { name: /${TASK_ID}/ })`]);
    await sleep(900);
    // open the Cancel dialog (exact avoids matching "Cancel task"/"Cancelling…")
    await pw(s, ['click', "getByRole('button', { name: 'Cancel', exact: true })"]);
    await sleep(500);
    // confirm — fires useCancelTask.mutateAsync -> POST -> onSuccess invalidate
    await pw(s, ['click', "getByRole('button', { name: 'Cancel task' })"]);
    await sleep(900);
    // SPA back to the list (cache persists; invalidated family refetches)
    await pw(s, ['click', "getByRole('link', { name: 'All tasks' })"]);
    await sleep(1400);
    // AFTER: list now reflects the NEW status (cancelled) — the fix
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr069-list-after-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2324] capturing ${theme}…`);
    await runTheme(srv.url, theme);
  }
  // eslint-disable-next-line no-console
  console.log('[shot-2324] done ->', OUT);
} finally {
  await srv.close();
}

/**
 * THR-099 number-overflow AFTER evidence (TASK-3023).
 *
 * Mode A (prod build + node /api mock). Seeds overflow-reproducing token data
 * (sums in the millions, cache in the hundreds-of-millions, a 346.1K-class
 * Dashboard TODAY value) and captures the swept metric surfaces full-page in
 * BOTH themes, proving no clip + compact text + full-precision title.
 *
 *   - Dashboard  : TODAY tile (A1) + Top-token-threads panel (A2/A3)
 *   - Usage      : by-agent (A4/A5) + by-team (A7) + top-threads-by-burn
 *                  (A8/A9), captured at 24h / 7d / 30d ranges
 *   - Health     : stat cards (A11 formatCount fold), a large seeded count
 *
 * Waits on a REAL seeded StatValue (getByTitle of the full-precision figure),
 * never the loading skeleton (MEM-101). Run: needs `npm run build` first.
 *   node scripts/screenshot-harness/shot-thr099-overflow.mjs
 * Out: web/scripts/screenshot-harness/out/thr099-<surface>-<range?>-<theme>.png
 */
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---- seeded rollups -------------------------------------------------- */

// by-agent rollup — feeds the Dashboard TODAY sum (346,100 → "346.1K"),
// the Usage by-agent table (A4 total / A5 cache) and the by-team card (A7).
// Totals stay K-scale so TODAY renders the founder's 346.1K case; cache
// columns carry M-scale to stress the cache cell.
const agentRollup = [
  { agent: 'engineering_head', sessions: 12, input_tokens: 120_000, output_tokens: 60_000, cache_read_tokens: 84_200_117, cache_creation_tokens: 0, reasoning_tokens: 20_000, total_tokens: 200_000, model_distinct: 1, model_any: 'claude-opus-4-8[1m]', non_null_sessions: 12 },
  { agent: 'qa_engineer', sessions: 8, input_tokens: 60_000, output_tokens: 30_000, cache_read_tokens: 41_880_902, cache_creation_tokens: 0, reasoning_tokens: 10_000, total_tokens: 100_000, model_distinct: 1, model_any: 'claude-sonnet-4-6', non_null_sessions: 8 },
  { agent: 'content_writer', sessions: 3, input_tokens: 30_000, output_tokens: 12_000, cache_read_tokens: 9_004_556, cache_creation_tokens: 0, reasoning_tokens: 4_100, total_tokens: 46_100, model_distinct: 1, model_any: 'claude-opus-4-8[1m]', non_null_sessions: 3 },
]; // total = 346,100

// by-thread rollup — feeds the Dashboard Top-token-threads panel (A2/A3) and
// the Usage Top-threads-by-burn card (A8/A9). Reproduces the founder overflow
// (3,707,054 total + 126,335,691 cache — overflow-11.24.40).
const threadRollup = [
  { thread_id: 'THR-burn-01', sessions: 40, input_tokens: 2_400_000, output_tokens: 1_100_000, cache_read_tokens: 126_335_691, cache_creation_tokens: 0, reasoning_tokens: 207_054, total_tokens: 3_707_054, model_distinct: 1, model_any: 'claude-opus-4-8[1m]', non_null_sessions: 40 },
  { thread_id: 'THR-burn-02', sessions: 22, input_tokens: 900_000, output_tokens: 300_000, cache_read_tokens: 88_200_341, cache_creation_tokens: 0, reasoning_tokens: 40_880, total_tokens: 1_240_880, model_distinct: 1, model_any: 'claude-sonnet-4-6', non_null_sessions: 22 },
  { thread_id: 'THR-burn-03', sessions: 9, input_tokens: 260_000, output_tokens: 80_000, cache_read_tokens: 12_004_556, cache_creation_tokens: 0, reasoning_tokens: 6_100, total_tokens: 346_100, model_distinct: 1, model_any: 'claude-opus-4-8[1m]', non_null_sessions: 9 },
];

const modelRollup = [
  { model: 'claude-opus-4-8[1m]', sessions: 49, input_tokens: 2_520_000, output_tokens: 1_160_000, cache_read_tokens: 135_340_247, cache_creation_tokens: 0, reasoning_tokens: 213_154, total_tokens: 3_893_154 },
  { model: 'claude-sonnet-4-6', sessions: 30, input_tokens: 960_000, output_tokens: 330_000, cache_read_tokens: 130_081_243, cache_creation_tokens: 0, reasoning_tokens: 50_880, total_tokens: 1_340_880 },
];

function tokensHandler(req, res) {
  const groupBy = new URL(req.url, 'http://localhost').searchParams.get('group_by');
  const rollup =
    groupBy === 'thread' ? threadRollup :
    groupBy === 'model' ? modelRollup :
    agentRollup;
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ rollup }));
}

const dashboardSummary = {
  heartbeat: Array.from({ length: 24 }, (_, h) => ({ hour: h, steps: h >= 9 && h <= 17 ? 3 + (h % 5) : 0, failed: h === 15 ? 1 : 0, tier: h === 15 ? 'warn' : 'ok' })),
  narrative_counts: { completed_today: 0, failed_today: 0, escalated_open: 1, kb_added_today: 0, agents_active_now: 7, spend_today_usd: 0 },
  escalations: [{ task_id: 'TASK-901', agent: 'engineering_head', team: 'engineering', question: 'Approve the release?', raised_at: '2026-07-17T11:00:00Z', age_seconds: 720 }],
  active_by_team: [{ team: 'engineering', count: 2, task_ids: ['TASK-902', 'TASK-903'] }],
  recent_activity: [{ timestamp: '2026-07-17T11:42:12Z', who: 'senior_dev', event_kind: 'completion_report', task_id: 'TASK-902', verdict: 'ok' }],
  updates_this_week: [{ marker: 'add', text: 'KB +1', meta: 'photo-attribution-required', timestamp: '2026-07-17T10:42:00Z' }],
  org_pulse: [{ team: 'engineering', acceptance_pct: 87, trend_delta: -3, sparkline: [0.94, 0.92, 0.88, 0.86, 0.84, 0.82, 0.84, 0.86, 0.88, 0.86, 0.87, 0.87], members: 5, lead: 'engineering_head' }],
  org_age_days: 15,
  server_now: '2026-07-17T12:00:00Z',
};

const metricsSnapshot = {
  uptime_seconds: 123_456,
  loops: {},
  http: {},
  tasks: { pending_and_in_flight: 42 },
  jobs_in_flight: 7,
  executor_sessions_active: 3,
  run_step_queue_depth: 1_234_567, // large seeded count → proves grouped, no clip
};

const agentsRoster = {
  agents: [
    { name: 'engineering_head', team: 'engineering', role: 'manager' },
    { name: 'qa_engineer', team: 'engineering', role: 'worker' },
    { name: 'content_writer', team: 'content', role: 'worker' },
  ],
};

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/dashboard/summary`, json: dashboardSummary },
  { path: `/api/v1/orgs/${SLUG}/tokens`, handler: tokensHandler },
  { path: `/api/v1/orgs/${SLUG}/agents`, json: agentsRoster },
  { path: `/api/v1/metrics`, json: metricsSnapshot },
  { path: `/api/v1/metrics/history`, json: { snapshots: [] } },
];

/* ---- playwright driver ---------------------------------------------- */

function pw(session, args) {
  return new Promise((resolve, reject) => {
    const p = spawn('playwright-cli', [`-s=${session}`, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
    let err = '';
    p.stderr.on('data', (d) => (err += d));
    p.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${err}`))));
    p.on('error', reject);
  });
}

async function shoot({ session, url, theme, waitTitle, outName, rangeClicks = [] }) {
  await pw(session, ['open']);
  try {
    await pw(session, ['resize', '1440', '900']);
    await pw(session, ['goto', url]);
    await pw(session, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(session, ['reload']);
    // Wait on a REAL seeded value (auto-waits), never the skeleton (MEM-101).
    await pw(session, ['hover', `getByTitle('${waitTitle}')`]);
    for (const rc of rangeClicks) {
      await pw(session, ['click', rc.click]);
      await pw(session, ['hover', `getByTitle('${waitTitle}')`]);
      await sleep(500);
      await pw(session, ['screenshot', '--full-page', `--filename=${join(OUT, rc.out.replace('THEME', theme))}`]);
    }
    if (outName) {
      await sleep(400);
      await pw(session, ['screenshot', '--full-page', `--filename=${join(OUT, outName.replace('THEME', theme))}`]);
    }
  } finally {
    await pw(session, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    console.log(`[thr099] ${theme} — dashboard`);
    await shoot({ session: `thr099-${theme}`, theme, url: `${srv.url}/orgs/${SLUG}/dashboard`, waitTitle: '346,100', outName: 'thr099-dashboard-THEME.png' });

    console.log(`[thr099] ${theme} — usage (24h/7d/30d)`);
    await shoot({
      session: `thr099-${theme}`, theme, url: `${srv.url}/orgs/${SLUG}/usage`,
      waitTitle: '126,335,691',
      rangeClicks: [
        { click: "getByRole('button', { name: '24h' })", out: 'thr099-usage-24h-THEME.png' },
        { click: "getByRole('button', { name: '7d' })", out: 'thr099-usage-7d-THEME.png' },
        { click: "getByRole('button', { name: '30d' })", out: 'thr099-usage-30d-THEME.png' },
      ],
    });

    console.log(`[thr099] ${theme} — health`);
    await shoot({ session: `thr099-${theme}`, theme, url: `${srv.url}/orgs/${SLUG}/health`, waitTitle: '1,234,567', outName: 'thr099-health-THEME.png' });
  }
  console.log('[thr099] done ->', OUT);
} finally {
  await srv.close();
}

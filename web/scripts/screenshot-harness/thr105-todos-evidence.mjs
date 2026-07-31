/**
 * THR-105 Todos fidelity evidence capture — TASK-3834.
 *
 * Composes the shared Mode-A harness (harness.mjs) with a deterministic
 * fixture set covering every required Todos state at 1440x900, light+dark.
 * Evidence-only tooling under web/scripts/ (not shipped app code; outside
 * eslint/tsc/vite build graph per screenshot-harness/README.md).
 *
 * Usage: node scripts/screenshot-harness/thr105-todos-evidence.mjs [outDir]
 */
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import {
  createServer,
  defaultApiRoutes,
  findDist,
  capture,
} from './harness.mjs';

const OUT = process.argv[2] || join(process.cwd(), 'scripts/screenshot-harness/out/thr105');
const ORG = 'acme';
const VIEWPORT = [1440, 900];

// ---------------------------------------------------------------------------
// Fixture data — deterministic, covers all 8 schedule statuses across both
// kinds (one_shot / weekly) and both agents. Timestamps are fixed ISO UTC
// instants so renders are reproducible run-to-run.
// ---------------------------------------------------------------------------

const SCHEDULES_FULL = [
  {
    schedule_id: 'SCHEDULE-101',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-08-01T01:00:00Z', // Sat 09:00 Asia/Shanghai
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Send the weekly market update every Saturday at 09:00 Asia/Shanghai.',
    source_instruction: 'Every Saturday, send me the weekly market update.',
    status: 'armed',
    active: 1,
    expires_at: '2026-10-23T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-7001', 'TASK-7002', 'TASK-7003', 'TASK-7004'],
    last_fired_at: '2026-07-25T01:00:00Z',
    fire_count: 4,
    created_at: '2026-06-06T02:00:00Z',
    updated_at: '2026-07-25T01:00:05Z',
  },
  {
    schedule_id: 'SCHEDULE-102',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-08-02T18:30:00Z', // 14:30 America/New_York
    recurrence: null,
    timezone: 'America/New_York',
    normalized_brief: 'Follow up with the customer about ticket #4821.',
    source_instruction: 'Follow up with this customer 48 hours after the issue was filed.',
    status: 'armed',
    active: 1,
    expires_at: '2026-10-31T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: null,
    fire_count: 0,
    created_at: '2026-07-31T18:30:00Z',
    updated_at: '2026-07-31T18:30:00Z',
  },
  {
    schedule_id: 'SCHEDULE-103',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-07-31T14:00:00Z',
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Send the weekly market update every Saturday at 09:00 Asia/Shanghai.',
    source_instruction: 'Every Saturday, send me the weekly market update.',
    status: 'firing',
    active: 1,
    expires_at: '2026-10-23T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-7001', 'TASK-7002', 'TASK-7003'],
    last_fired_at: '2026-07-25T01:00:00Z',
    fire_count: 3,
    created_at: '2026-06-06T02:00:00Z',
    updated_at: '2026-07-31T14:00:00Z',
  },
  {
    schedule_id: 'SCHEDULE-104',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-08-05T21:00:00Z', // 16:00 America/Chicago
    recurrence: null,
    timezone: 'America/Chicago',
    normalized_brief: 'Check in with the vendor about the delayed shipment.',
    source_instruction: 'Remind me to check in with the vendor next week if the shipment has not arrived.',
    status: 'paused',
    active: 0,
    expires_at: '2026-10-01T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: null,
    fire_count: 0,
    created_at: '2026-07-20T10:00:00Z',
    updated_at: '2026-07-28T09:15:00Z',
  },
  {
    schedule_id: 'SCHEDULE-105',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-08-03T08:00:00Z', // Mon 08:00 Europe/London
    recurrence: { day: 'Mon', time: '08:00', tz: 'Europe/London' },
    timezone: 'Europe/London',
    normalized_brief: 'Send the Monday portfolio risk digest.',
    source_instruction: 'Every Monday morning, send me the portfolio risk digest.',
    status: 'paused',
    active: 0,
    expires_at: null,
    indefinite: 1,
    spawned_task_ids: ['TASK-6801', 'TASK-6802'],
    last_fired_at: '2026-07-27T07:00:00Z',
    fire_count: 8,
    created_at: '2026-05-04T08:00:00Z',
    updated_at: '2026-07-29T11:40:00Z',
  },
  {
    schedule_id: 'SCHEDULE-106',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-07-20T14:30:00Z',
    recurrence: null,
    timezone: 'America/New_York',
    normalized_brief: 'Follow up with the customer about ticket #4711.',
    source_instruction: 'Follow up with this customer 48 hours after the issue was filed.',
    status: 'fired',
    active: 0,
    expires_at: '2026-10-18T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-6650'],
    last_fired_at: '2026-07-20T14:30:03Z',
    fire_count: 1,
    created_at: '2026-07-18T14:30:00Z',
    updated_at: '2026-07-20T14:30:03Z',
  },
  {
    schedule_id: 'SCHEDULE-107',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-07-25T01:00:00Z',
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Send the weekly options-flow summary.',
    source_instruction: 'Every Saturday, send me the options-flow summary.',
    status: 'failed',
    active: 0,
    expires_at: '2026-09-19T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-6500', 'TASK-6520', 'TASK-6540'],
    last_fired_at: '2026-07-25T01:00:04Z',
    fire_count: 3,
    created_at: '2026-06-20T01:00:00Z',
    updated_at: '2026-07-25T01:02:11Z',
  },
  {
    schedule_id: 'SCHEDULE-108',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-07-22T09:00:00Z',
    recurrence: null,
    timezone: 'America/Chicago',
    normalized_brief: 'Follow up with the customer about ticket #4390.',
    source_instruction: 'Follow up with this customer 48 hours after the issue was filed.',
    status: 'timeout',
    active: 0,
    expires_at: '2026-10-20T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-6410'],
    last_fired_at: '2026-07-22T09:00:00Z',
    fire_count: 1,
    created_at: '2026-07-20T09:00:00Z',
    updated_at: '2026-07-22T09:05:00Z',
  },
  {
    schedule_id: 'SCHEDULE-109',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-07-04T01:00:00Z',
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Send the weekly sector-rotation note.',
    source_instruction: 'Every Saturday, send me the sector-rotation note.',
    status: 'expired',
    active: 0,
    expires_at: '2026-07-01T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-5900', 'TASK-5920'],
    last_fired_at: '2026-06-27T01:00:00Z',
    fire_count: 6,
    created_at: '2026-04-02T01:00:00Z',
    updated_at: '2026-07-01T00:00:05Z',
  },
  {
    schedule_id: 'SCHEDULE-110',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-07-15T16:00:00Z',
    recurrence: null,
    timezone: 'America/Chicago',
    normalized_brief: 'Check in with the vendor about the delayed shipment.',
    source_instruction: 'Remind me to check in with the vendor next week if the shipment has not arrived.',
    status: 'cancelled',
    active: 0,
    expires_at: '2026-10-01T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: null,
    fire_count: 0,
    created_at: '2026-07-10T09:00:00Z',
    updated_at: '2026-07-16T08:00:00Z',
  },
];

// Reduced fixture with zero failed/timeout rows — used for the
// filtered-empty capture (clicking "Needs attention" yields no rows).
const SCHEDULES_NO_ATTENTION = SCHEDULES_FULL.filter(
  (s) => !['failed', 'timeout'].includes(s.status),
);

function byId(id, schedules = SCHEDULES_FULL) {
  const s = schedules.find((r) => r.schedule_id === id);
  if (!s) throw new Error(`fixture not found: ${id}`);
  return s;
}

// ---------------------------------------------------------------------------
// /api route builders
// ---------------------------------------------------------------------------

function scheduleApiRoutes(schedules, { editResponse } = {}) {
  const routes = [
    {
      method: 'GET',
      path: `/api/v1/orgs/${ORG}/schedules`,
      json: { schedules },
    },
    {
      method: 'GET',
      path: new RegExp(`^/api/v1/orgs/${ORG}/schedules/(SCHEDULE-\\d+)$`),
      handler: (req, res) => {
        const id = req.url.split('/').pop();
        const found = schedules.find((s) => s.schedule_id === id);
        res.writeHead(found ? 200 : 404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(found ?? { detail: { code: 'not_found', schedule_id: id } }));
      },
    },
    {
      method: 'POST',
      path: new RegExp(`^/api/v1/orgs/${ORG}/schedules/SCHEDULE-\\d+/pause$`),
      json: {},
    },
    {
      method: 'POST',
      path: new RegExp(`^/api/v1/orgs/${ORG}/schedules/SCHEDULE-\\d+/cancel$`),
      json: {},
    },
  ];
  if (editResponse) {
    routes.push({
      method: 'PATCH',
      path: new RegExp(`^/api/v1/orgs/${ORG}/schedules/SCHEDULE-\\d+$`),
      handler: (req, res) => {
        res.writeHead(editResponse.status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(editResponse.body));
      },
    });
  }
  return routes;
}

async function serve(schedules, opts = {}) {
  return createServer({
    root: findDist(),
    api: [...defaultApiRoutes({ orgs: [{ slug: ORG, root: '/tmp/acme' }] }), ...scheduleApiRoutes(schedules, opts)],
  });
}

async function shot(srv, { route, name, theme, prep = [], settleMs }) {
  return capture({
    url: `${srv.url}${route}`,
    out: join(OUT, `${name}-${theme}.png`),
    viewport: VIEWPORT,
    theme,
    prep,
    settleMs,
  });
}

async function bothThemes(srv, { route, name, prep = [], settleMs }) {
  const out = [];
  for (const theme of ['light', 'dark']) {
    out.push(await shot(srv, { route, name, theme, prep, settleMs }));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Main capture plan
// ---------------------------------------------------------------------------

async function main() {
  await mkdir(OUT, { recursive: true });
  const manifest = [];

  // 1. List — normal (All filter, default tab)
  {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, { route: `/orgs/${ORG}/todos`, name: 'list-all' });
      manifest.push({ state: 'list-all', files });
    } finally {
      await srv.close();
    }
  }

  // 2-5. List — Active / Paused / Needs attention / History filter tabs
  const filterTabs = [
    { name: 'list-active', label: 'Active' },
    { name: 'list-paused', label: 'Paused' },
    { name: 'list-needs-attention', label: 'Needs attention' },
    { name: 'list-history', label: 'History' },
  ];
  for (const tab of filterTabs) {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos`,
        name: tab.name,
        prep: [['click', `getByRole('button', { name: '${tab.label}', exact: true })`]],
      });
      manifest.push({ state: tab.name, files });
    } finally {
      await srv.close();
    }
  }

  // 6. List — filtered-empty (Needs attention tab, zero failed/timeout rows)
  {
    const srv = await serve(SCHEDULES_NO_ATTENTION);
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos`,
        name: 'list-filtered-empty',
        prep: [['click', "getByRole('button', { name: 'Needs attention', exact: true })"]],
      });
      manifest.push({ state: 'list-filtered-empty', files });
    } finally {
      await srv.close();
    }
  }

  // 7. List — empty (zero schedules org-wide)
  {
    const srv = await serve([]);
    try {
      const files = await bothThemes(srv, { route: `/orgs/${ORG}/todos`, name: 'list-empty' });
      manifest.push({ state: 'list-empty', files });
    } finally {
      await srv.close();
    }
  }

  // 8. List — error (500 from the schedules list route)
  {
    const srv = await createServer({
      root: findDist(),
      api: [
        ...defaultApiRoutes({ orgs: [{ slug: ORG, root: '/tmp/acme' }] }),
        {
          method: 'GET',
          path: `/api/v1/orgs/${ORG}/schedules`,
          handler: (req, res) => {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ detail: { code: 'internal_error' } }));
          },
        },
      ],
    });
    try {
      // "retry" reuses this same error render — the Retry button is the
      // control visible in this exact screenshot (see README note in the
      // PR comment / variance ledger for why no separate post-retry state
      // is captured).
      const files = await bothThemes(srv, { route: `/orgs/${ORG}/todos`, name: 'list-error-retry' });
      manifest.push({ state: 'list-error-retry', files });
    } finally {
      await srv.close();
    }
  }

  // 9. List — loading (schedules list delayed past the capture settle window)
  {
    const srv = await createServer({
      root: findDist(),
      api: [
        ...defaultApiRoutes({ orgs: [{ slug: ORG, root: '/tmp/acme' }] }),
        {
          method: 'GET',
          path: `/api/v1/orgs/${ORG}/schedules`,
          handler: (req, res) => {
            setTimeout(() => {
              res.writeHead(200, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ schedules: SCHEDULES_FULL }));
            }, 6000);
          },
        },
      ],
    });
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos`,
        name: 'list-loading',
        settleMs: 250,
      });
      manifest.push({ state: 'list-loading', files });
    } finally {
      await srv.close();
    }
  }

  // 10-17. Detail — one capture per status
  const detailStatuses = [
    ['SCHEDULE-101', 'detail-armed'],
    ['SCHEDULE-103', 'detail-firing'],
    ['SCHEDULE-104', 'detail-paused'],
    ['SCHEDULE-106', 'detail-fired'],
    ['SCHEDULE-107', 'detail-failed'],
    ['SCHEDULE-108', 'detail-timeout'],
    ['SCHEDULE-109', 'detail-expired'],
    ['SCHEDULE-110', 'detail-cancelled'],
  ];
  for (const [id, name] of detailStatuses) {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, { route: `/orgs/${ORG}/todos/${id}`, name });
      manifest.push({ state: name, files });
    } finally {
      await srv.close();
    }
  }

  // 18. Pause confirmation (armed schedule)
  {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos/SCHEDULE-101`,
        name: 'detail-pause-confirm',
        prep: [['click', "getByRole('button', { name: 'Pause', exact: true })"]],
      });
      manifest.push({ state: 'detail-pause-confirm', files });
    } finally {
      await srv.close();
    }
  }

  // 19. Cancel confirmation (armed schedule)
  {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos/SCHEDULE-101`,
        name: 'detail-cancel-confirm',
        prep: [['click', "getByRole('button', { name: 'Cancel', exact: true })"]],
      });
      manifest.push({ state: 'detail-cancel-confirm', files });
    } finally {
      await srv.close();
    }
  }

  // 20. Edit — normal (dialog open, no error, no conflict)
  {
    const srv = await serve(SCHEDULES_FULL);
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos/SCHEDULE-101`,
        name: 'detail-edit-normal',
        prep: [['click', "getByRole('button', { name: 'Edit timing', exact: true })"]],
      });
      manifest.push({ state: 'detail-edit-normal', files });
    } finally {
      await srv.close();
    }
  }

  // 21. Edit — validation rejection (PATCH -> 422 invalid_fire_at; existing
  // schedule preserved, inline error shown, dialog stays open).
  {
    const srv = await serve(SCHEDULES_FULL, {
      editResponse: {
        status: 422,
        body: { detail: { code: 'invalid_fire_at', got: 'not-a-date' } },
      },
    });
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos/SCHEDULE-101`,
        name: 'detail-edit-validation',
        prep: [
          ['click', "getByRole('button', { name: 'Edit timing', exact: true })"],
          ['click', "getByRole('button', { name: 'Save changes', exact: true })"],
        ],
        settleMs: 1200,
      });
      manifest.push({ state: 'detail-edit-validation', files });
    } finally {
      await srv.close();
    }
  }

  // 22. Edit — conflict/reload (PATCH -> 409 state_conflict)
  {
    const srv = await serve(SCHEDULES_FULL, {
      editResponse: {
        status: 409,
        body: { detail: { code: 'state_conflict', message: 'schedule SCHEDULE-101 is no longer armed' } },
      },
    });
    try {
      const files = await bothThemes(srv, {
        route: `/orgs/${ORG}/todos/SCHEDULE-101`,
        name: 'detail-edit-conflict',
        prep: [
          ['click', "getByRole('button', { name: 'Edit timing', exact: true })"],
          ['click', "getByRole('button', { name: 'Save changes', exact: true })"],
        ],
        settleMs: 1200,
      });
      manifest.push({ state: 'detail-edit-conflict', files });
    } finally {
      await srv.close();
    }
  }

  await mkdir(OUT, { recursive: true });
  const { writeFile } = await import('node:fs/promises');
  await writeFile(join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 2));
  console.log(`Captured ${manifest.length} states (${manifest.length * 2} screenshots) to ${OUT}`);
  console.log(JSON.stringify(manifest.map((m) => m.state), null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

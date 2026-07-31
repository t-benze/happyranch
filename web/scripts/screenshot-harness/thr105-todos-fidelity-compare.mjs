/**
 * THR-105 Todos — content-aligned comparison capture (TASK-3834).
 *
 * Supplements thr105-todos-evidence.mjs. That script covers the full state
 * matrix with realistic-but-original fixture data (so raw pixel-diff against
 * the reference HTML is dominated by TEXT CONTENT differences, not style).
 * This script re-captures ONLY the two states that have a direct 1:1 render
 * in the reference HTML (`#/todos` list, `#/todos/SCHEDULE-042` detail) using
 * fixture data whose id/title/agent/date/counts match the reference's own
 * sample rows as closely as the shipped ScheduleRecord schema allows, so the
 * resulting pixel-diff measures STYLE fidelity rather than copy differences.
 *
 * Evidence-only tooling under web/scripts/ — not shipped app code.
 */
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { createServer, defaultApiRoutes, findDist, capture } from './harness.mjs';

const OUT = process.argv[2] || join(process.cwd(), 'scripts/screenshot-harness/out/thr105-compare');
const ORG = 'acme';
const VIEWPORT = [1440, 900];

// Row order matches the reference HTML's #/todos view exactly (see
// /tmp/thr105-ref/ref-list-light.png and ref-list-scrolled-light.png).
export const SCHEDULES = [
  {
    schedule_id: 'SCHEDULE-042',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-07-25T01:00:00Z', // Sat Jul 25 2026 09:00 Asia/Shanghai
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Send the weekly market update',
    source_instruction: 'Every Saturday, send me the weekly market update.',
    status: 'armed',
    active: 1,
    expires_at: '2026-10-23T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-1042', 'TASK-1029', 'TASK-1015'],
    last_fired_at: '2026-07-18T01:00:00Z',
    fire_count: 3,
    created_at: '2026-05-03T01:00:00Z',
    updated_at: '2026-07-18T01:02:00Z',
  },
  {
    schedule_id: 'SCHEDULE-058',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'one_shot',
    fire_at: '2026-08-01T14:00:00Z',
    recurrence: null,
    timezone: 'UTC',
    normalized_brief: 'Follow up on the Acme trial issue',
    source_instruction: 'Follow up on the Acme trial issue once it starts.',
    status: 'armed',
    active: 1,
    expires_at: '2026-10-30T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: null,
    fire_count: 0,
    created_at: '2026-07-25T14:00:00Z',
    updated_at: '2026-07-25T14:00:00Z',
  },
  {
    schedule_id: 'SCHEDULE-064',
    agent_name: 'qa_engineer',
    team: 'qa',
    kind: 'weekly',
    fire_at: '2026-08-06T22:00:00Z',
    recurrence: { day: 'Thu', time: '22:00', tz: 'UTC' },
    timezone: 'UTC',
    normalized_brief: 'Run the nightly regression sweep',
    source_instruction: 'Every Thursday at 22:00, run the nightly regression sweep.',
    status: 'firing',
    active: 1,
    expires_at: null,
    indefinite: 1,
    spawned_task_ids: [],
    last_fired_at: '2026-07-30T22:00:00Z',
    fire_count: 14,
    created_at: '2026-04-30T22:00:00Z',
    updated_at: '2026-07-30T22:00:00Z',
  },
  {
    schedule_id: 'SCHEDULE-071',
    agent_name: 'dev_agent',
    team: 'engineering',
    kind: 'weekly',
    fire_at: '2026-07-31T17:00:00Z',
    recurrence: { day: 'Fri', time: '17:00', tz: 'UTC' },
    timezone: 'UTC',
    normalized_brief: 'Sync the customer changelog',
    source_instruction: 'Every Friday, sync the customer changelog.',
    status: 'failed',
    active: 0,
    expires_at: '2026-09-25T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: '2026-07-31T17:00:04Z',
    fire_count: 6,
    created_at: '2026-06-19T17:00:00Z',
    updated_at: '2026-07-31T17:02:00Z',
  },
  {
    schedule_id: 'SCHEDULE-088',
    agent_name: 'support_agent',
    team: 'support',
    kind: 'weekly',
    fire_at: '2026-08-04T08:00:00Z',
    recurrence: { day: 'Tue', time: '08:00', tz: 'UTC' },
    timezone: 'UTC',
    normalized_brief: 'Summarize overnight inbox triage',
    source_instruction: 'Every Tuesday, summarize overnight inbox triage.',
    status: 'timeout',
    active: 0,
    expires_at: '2026-09-29T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: '2026-07-28T08:00:00Z',
    fire_count: 9,
    created_at: '2026-05-26T08:00:00Z',
    updated_at: '2026-07-28T08:05:00Z',
  },
  {
    schedule_id: 'SCHEDULE-031',
    agent_name: 'product_lead',
    team: 'product',
    kind: 'weekly',
    fire_at: '2026-08-03T10:00:00Z',
    recurrence: { day: 'Mon', time: '10:00', tz: 'UTC' },
    timezone: 'UTC',
    normalized_brief: 'Review roadmap risks',
    source_instruction: 'Every Monday, review roadmap risks.',
    status: 'paused',
    active: 0,
    expires_at: null,
    indefinite: 1,
    spawned_task_ids: [],
    last_fired_at: '2026-07-27T10:00:00Z',
    fire_count: 5,
    created_at: '2026-06-01T10:00:00Z',
    updated_at: '2026-07-28T09:00:00Z',
  },
  {
    schedule_id: 'SCHEDULE-102',
    agent_name: 'product_lead',
    team: 'product',
    kind: 'one_shot',
    fire_at: '2026-07-10T11:00:00Z',
    recurrence: null,
    timezone: 'UTC',
    normalized_brief: 'Draft partner outreach note',
    source_instruction: 'Draft the partner outreach note before the Jul 10 sync.',
    status: 'cancelled',
    active: 0,
    expires_at: '2026-10-08T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: null,
    fire_count: 0,
    created_at: '2026-07-01T11:00:00Z',
    updated_at: '2026-07-09T09:00:00Z',
  },
  {
    schedule_id: 'SCHEDULE-019',
    agent_name: 'engineering_manager',
    team: 'engineering',
    kind: 'one_shot',
    fire_at: '2026-07-20T09:00:00Z',
    recurrence: null,
    timezone: 'UTC',
    normalized_brief: 'Check the release health metric',
    source_instruction: 'Check the release health metric the morning after ship.',
    status: 'fired',
    active: 0,
    expires_at: '2026-10-18T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: ['TASK-1900'],
    last_fired_at: '2026-07-20T09:00:03Z',
    fire_count: 1,
    created_at: '2026-07-18T09:00:00Z',
    updated_at: '2026-07-20T09:00:03Z',
  },
  {
    schedule_id: 'SCHEDULE-007',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-06-27T01:00:00Z',
    recurrence: { day: 'Sat', time: '09:00', tz: 'Asia/Shanghai' },
    timezone: 'Asia/Shanghai',
    normalized_brief: 'Weekly market update',
    source_instruction: 'Every Saturday, send me the weekly market update.',
    status: 'expired',
    active: 0,
    expires_at: '2026-07-01T00:00:00Z',
    indefinite: 0,
    spawned_task_ids: [],
    last_fired_at: '2026-06-27T01:00:00Z',
    fire_count: 12,
    created_at: '2026-01-03T01:00:00Z',
    updated_at: '2026-07-01T00:00:05Z',
  },
];

export function scheduleApiRoutes(schedules) {
  return [
    { method: 'GET', path: `/api/v1/orgs/${ORG}/schedules`, json: { schedules } },
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
  ];
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const srv = await createServer({
    root: findDist(),
    api: [...defaultApiRoutes({ orgs: [{ slug: ORG, root: '/tmp/acme' }] }), ...scheduleApiRoutes(SCHEDULES)],
  });
  try {
    await capture({
      url: `${srv.url}/orgs/${ORG}/todos`,
      out: join(OUT, 'list-all-aligned-light.png'),
      viewport: VIEWPORT,
      theme: 'light',
    });
    await capture({
      url: `${srv.url}/orgs/${ORG}/todos/SCHEDULE-042`,
      out: join(OUT, 'detail-042-aligned-light.png'),
      viewport: VIEWPORT,
      theme: 'light',
    });
  } finally {
    await srv.close();
  }
  console.log(`Aligned comparison captures written to ${OUT}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}

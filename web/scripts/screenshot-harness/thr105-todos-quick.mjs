/**
 * Complete LIGHT desktop Todos evidence capture — TASK-4129.
 *
 * Captures the list and every approved detail/status state at 1440x900 light
 * using the existing Mode-A prod-build + /api mock harness. Each fixture has a
 * deterministic, unambiguous filename that includes kind + status, so weekly and
 * one-shot records with the same status never collide.
 *
 * Reference images for the two approved design-target states live in
 * ./reference/ and are used by thr105-todos-diff-light.mjs for numeric diff.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { modeAProdApi } from './harness.mjs';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const OUT = process.argv[2] || join(HERE, 'out', 'thr105-complete-light');
const ORG = 'acme';
const VIEWPORT = [1440, 900];

const SCHEDULES = [
  {
    schedule_id: 'SCHEDULE-101',
    agent_name: 'investment_advisor',
    team: 'research',
    kind: 'weekly',
    fire_at: '2026-08-01T01:00:00Z',
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
    fire_at: '2026-08-02T18:30:00Z',
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
    fire_at: '2026-08-05T21:00:00Z',
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
    fire_at: '2026-08-03T08:00:00Z',
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

function scheduleApiRoutes() {
  return [
    { path: `/api/v1/orgs/${ORG}/schedules`, json: { schedules: SCHEDULES } },
    {
      path: new RegExp(`/api/v1/orgs/${ORG}/schedules/[^/]+$`),
      json: (url) => {
        const id = url.pathname.split('/').pop();
        const s = SCHEDULES.find((r) => r.schedule_id === id);
        return s ?? { detail: 'Not found' };
      },
    },
    {
      path: new RegExp(`/api/v1/orgs/${ORG}/schedules/[^/]+/pause`),
      method: 'POST',
      json: (url) => {
        const id = url.pathname.split('/').slice(-2)[0];
        const s = SCHEDULES.find((r) => r.schedule_id === id);
        if (s) s.status = 'paused';
        return s ?? { detail: 'Not found' };
      },
    },
    {
      path: new RegExp(`/api/v1/orgs/${ORG}/schedules/[^/]+/cancel`),
      method: 'POST',
      json: (url) => {
        const id = url.pathname.split('/').slice(-2)[0];
        const s = SCHEDULES.find((r) => r.schedule_id === id);
        if (s) s.status = 'cancelled';
        return s ?? { detail: 'Not found' };
      },
    },
  ];
}

function safeName(s) {
  return `${s.kind}_${s.status}_${s.schedule_id}`.toLowerCase().replace(/[^a-z0-9_-]/g, '-');
}

async function main() {
  await mkdir(OUT, { recursive: true });

  const stateMap = [];
  const api = scheduleApiRoutes();
  const shots = [];

  shots.push(
    ...(await modeAProdApi({
      route: `/orgs/${ORG}/todos`,
      outDir: OUT,
      name: 'todos-list',
      api,
      orgs: [{ slug: ORG, root: '/tmp/acme' }],
      viewport: VIEWPORT,
    })),
  );
  stateMap.push({ file: 'todos-list-light.png', route: `/orgs/${ORG}/todos`, state: 'List (all groups)' });

  for (const s of SCHEDULES) {
    const name = `todos-detail-${safeName(s)}`;
    shots.push(
      ...(await modeAProdApi({
        route: `/orgs/${ORG}/todos/${s.schedule_id}`,
        outDir: OUT,
        name,
        api,
        orgs: [{ slug: ORG, root: '/tmp/acme' }],
        viewport: VIEWPORT,
      })),
    );
    stateMap.push({
      file: `${name}-light.png`,
      route: `/orgs/${ORG}/todos/${s.schedule_id}`,
      state: `${s.kind} ${s.status} (${s.schedule_id})`,
    });
  }

  await writeFile(
    join(OUT, 'state-map.json'),
    JSON.stringify({ capturedAt: new Date().toISOString(), viewport: VIEWPORT, states: stateMap }, null, 2),
  );

  console.log('Captured:');
  for (const s of shots) console.log(' ', s);
  console.log(`State map written to ${join(OUT, 'state-map.json')}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { modeAProdApi } from './harness.mjs'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const OUT = process.argv[2] || join(HERE, 'out', 'task-5060-native-recurring')
const ORG = 'acme'
const recurring = {
  schedule_id: 'SCHEDULE-5060', agent_name: 'investment_advisor', team: 'research', kind: 'recurring',
  fire_at: '2026-08-10T01:00:00Z',
  recurrence: { freq: 'MONTHLY', interval: 2, ordinal: 'second', byday: ['MO'], time: '09:00', tz: 'Asia/Shanghai', until: null, count: 6, anchor_date: '2026-08-10' },
  timezone: 'Asia/Shanghai', normalized_brief: 'Review the recurring portfolio allocation',
  source_instruction: 'Review the portfolio on the second Monday every other month.',
  status: 'armed', active: 1, expires_at: '2026-10-23T00:00:00Z', indefinite: 0,
  spawned_task_ids: [], last_fired_at: null, fire_count: 0, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
}
const api = [
  { path: `/api/v1/orgs/${ORG}/schedules`, json: { schedules: [recurring] } },
  { path: `/api/v1/orgs/${ORG}/schedules/${recurring.schedule_id}`, json: recurring },
  { path: `/api/v1/orgs/${ORG}/schedules/${recurring.schedule_id}`, method: 'PATCH', json: recurring },
]

await mkdir(OUT, { recursive: true })
const common = { api, orgs: [{ slug: ORG, root: '/tmp/acme' }], viewport: [1440, 900], outDir: OUT }
await modeAProdApi({ ...common, route: `/orgs/${ORG}/todos`, name: 'todos-native-recurring-list' })
await modeAProdApi({ ...common, route: `/orgs/${ORG}/todos/${recurring.schedule_id}`, name: 'todos-native-recurring-detail' })
await writeFile(join(OUT, 'state-map.json'), JSON.stringify({ viewport: [1440, 900], states: ['native recurring list', 'native recurring detail'] }, null, 2))
console.log(`Captured native recurring evidence in ${OUT}`)

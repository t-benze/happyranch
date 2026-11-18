// TASK-17407 — Task detail rail 'Job' row: Running vs Finished groups.
// Mode A (prod build + Node-side /api mock). Synthetic data and the harness's
// synthetic token only; no in-browser request interception.
import { modeAProdApi } from './harness.mjs';

const outDir = process.argv[2] || '/tmp/T17407-shots';
const name = process.argv[3] || 'rail-jobs';
const TASK = 'TASK-9001';
const now = '2026-11-18T14:00:00Z';
const statuses = [
  'running', 'pending', 'running',
  'completed', 'failed', 'completed', 'rejected', 'completed', 'failed', 'completed',
  'completed', 'completed', 'failed', 'completed', 'completed', 'completed',
  'completed', 'completed', 'completed', 'completed', 'completed', 'completed',
  'failed', 'completed', 'completed', 'completed',
];
const jobs = statuses.map((status, i) => ({
  id: `JOB-${String(5200 - i).padStart(4, '0')}`,
  task_id: TASK, agent_name: 'frontend_engineer', title: `synthetic job ${i}`,
  rationale: '', script_text: 'true', interpreter: 'bash', cwd_hint: null, status,
  exit_code: status === 'completed' ? 0 : status === 'failed' ? 1 : null,
  stdout_head: null, stderr_head: null, stdout_path: null, stderr_path: null,
  duration_ms: null, started_at: now, finished_at: null, reviewed_at: null,
  reviewed_by: null, reject_reason: null, cwd_resolved: null, max_runtime_seconds: null,
  max_output_bytes: null, review_required: false, persistent: false, created_at: now,
}));
const task = {
  task_id: TASK, team: 'engineering', brief: 'Synthetic task with mixed running and finished jobs.',
  status: 'in_progress', block_kind: 'blocked_on_job', blocked_on_job_ids: JSON.stringify(jobs.slice(0, 3).map((j) => j.id)),
  assigned_agent: 'frontend_engineer', parent_task_id: null, revisit_of_task_id: null,
  created_at: now, updated_at: now, closed_at: null, cancelled_at: null, session_timeout_seconds: null,
};
const api = [
  { path: `/api/v1/orgs/demo/tasks/${TASK}`, json: {
    task, results: [], audit_log: [], revisit_chain: [], direct_revisits: [],
    predecessor_prior_status: null, active_chain: null, superseded_by_task_id: null,
  } },
  { path: '/api/v1/orgs/demo/jobs/', json: { jobs } },
  { path: '/api/v1/orgs/demo/jobs', json: { jobs } },
  { path: `/api/v1/orgs/demo/tasks/${TASK}/recall`, json: { task_id: TASK, children: [] } },
  { path: `/api/v1/orgs/demo/tasks/${TASK}/attachments`, json: { attachments: [] } },
];
const shots = await modeAProdApi({ route: `/orgs/demo/tasks/${TASK}`, outDir, name, api, viewport: [1440, 1400] });
console.log(shots.join('\n'));

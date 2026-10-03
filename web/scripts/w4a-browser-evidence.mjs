#!/usr/bin/env node
/**
 * W4a browser-evidence harness (THR-118 W4a-1 health + dreams; W4b todos + work-hours + audit; W4c agents + skills; later W4 slices add rows).
 *
 * Same mechanism as `w3b-jobs-browser-evidence.mjs` (no new dependency): ONE
 * isolated headless Chrome driven over the DevTools Protocol against the
 * ORDINARY production SPA bundle (no build flag), served same-origin next to a
 * synthetic `/api/v1` stub whose every request lands in a server-side ledger.
 * Expected copy is read from the shipped typed catalogs.
 *
 * Everything route-specific lives in three tables so a later slice only adds
 * rows: `API_ROUTES` (synthetic daemon payloads), `VIEW_ROUTES` (case V) and
 * `SWITCH_ROUTES` (case S). Cases (receipt.json; exit 1 if any fails):
 *   G  the ordinary bundle carries every VIEW_ROUTES row's zh-CN catalog copy;
 *   V  each VIEW_ROUTES row in en and zh-CN at 1440x900 and 390x844:
 *      <html lang>, localized chrome, daemon values verbatim, no document-level
 *      horizontal overflow, optional `contained` (every clipping card holds
 *      its rows: scrollWidth <= clientWidth), PNG + sha256;
 *   S  each SWITCH_ROUTES row (an input/dialog surface): focus the control,
 *      switch en -> zh-CN -> en via the storage-event path; the SAME container
 *      and control nodes, focus (and draft, when the row types one), localized
 *      copy, ZERO /api requests per switch window.
 *
 * Build + run (from web/):
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   node scripts/w4a-browser-evidence.mjs --dist <tmp>/dist-ordinary \
 *     --out <evidence dir> --head <sha>
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const { en } = await import(join(HERE, '../src/lib/i18n/locales/en.ts'));
const { zhCN } = await import(join(HERE, '../src/lib/i18n/locales/zh-CN.ts'));
const CATALOG = { en, 'zh-CN': zhCN };
/** Catalog lookup with named params and explicit plurals (mirrors catalog.ts). */
function tr(locale, key, params = {}) {
  let value = CATALOG[locale][key];
  if (value === undefined) throw new Error(`harness: unknown catalog key ${key}`);
  if (typeof value !== 'string') {
    const n = Number(params.count);
    value = locale === 'en' && n === 1 && value.one !== undefined ? value.one : value.other;
  }
  return value.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 || index + 1 >= process.argv.length ? fallback : process.argv[index + 1];
}

const dist = arg('dist') && resolve(arg('dist'));
const outDir = resolve(arg('out', '.w4a-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
const EXTERNAL_BLOCK = ['https://*', 'http://*.com/*', 'http://*.net/*'];
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon',
};

function distJs(dir) {
  const out = [];
  const walk = (d) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (p.endsWith('.js')) out.push(p);
    }
  };
  walk(dir);
  return out;
}

// ------------------------------------------------------------------ CDP driver
class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.ready = new Promise((ok, fail) => {
      this.ws.addEventListener('open', () => ok());
      this.ws.addEventListener('error', () => fail(new Error('CDP websocket error')));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8'));
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
        return;
      }
      for (const handler of [...(this.handlers.get(message.method) || [])]) handler(message);
    });
  }

  async send(method, params = {}, sessionId) {
    await this.ready;
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    const promise = new Promise((ok, fail) => this.pending.set(id, { resolve: ok, reject: fail }));
    this.ws.send(JSON.stringify(payload));
    return promise;
  }

  waitFor(method, { sessionId, timeout = 30000 } = {}) {
    return new Promise((ok, fail) => {
      const handler = (message) => {
        if (sessionId && message.sessionId !== sessionId) return;
        off();
        clearTimeout(timer);
        ok(message.params);
      };
      this.handlers.set(method, [...(this.handlers.get(method) || []), handler]);
      const off = () => this.handlers.set(method, (this.handlers.get(method) || []).filter((h) => h !== handler));
      const timer = setTimeout(() => { off(); fail(new Error(`timeout waiting for CDP ${method}`)); }, timeout);
    });
  }

  close() {
    try { this.ws.close(); } catch { /* closed */ }
  }
}

async function waitForDevTools(userDataDir, timeout = 30000) {
  const file = join(userDataDir, 'DevToolsActivePort');
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (existsSync(file)) {
      const [port, path] = readFileSync(file, 'utf8').split('\n');
      if (port && Number(port) > 0 && path) return { port: Number(port), path: path.trim() };
    }
    await sleep(100);
  }
  throw new Error(`Chrome DevTools port file not found at ${file}`);
}


// ------------------------------------------------------------------ synthetic API
const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const LEDGER = [];
const HUNG = [];

// Daemon bytes that must survive every locale unchanged.
const HEALTH_SNAPSHOT = {
  uptime_seconds: 3 * 3600 + 14 * 60,
  loops: { work_hours_scheduler_loop: { last_tick_iso: iso(30e3), interval_seconds: 60, last_duration_seconds: 0.012 } },
  http: {
    __all__: { count: 120, p50: 0.01, p95: 0.05, max: 0.2 },
    'GET /api/v1/orgs/{slug}/tasks': { count: 42, p50: 0.008, p95: 0.02, max: 0.05 },
  },
  tasks: { pending_and_in_flight: 4 }, jobs_in_flight: 2, executor_sessions_active: 1, run_step_queue_depth: 7,
};
const DREAM_BASE = {
  scheduled_for: iso(9 * 3600e3), window_start: null, window_end: iso(8 * 3600e3), started_at: iso(9 * 3600e3),
  ended_at: null, transcript_path: null, founder_thread_id: null, error: null, summary: null,
};
const DREAMS = [
  { ...DREAM_BASE, dream_id: 'DREAM-0012', agent_name: 'engineering_manager', local_date: '2026-09-30', status: 'completed',
    summary: 'Routine nightly reflection — «raw» summary.', new_learnings_count: 2, kb_candidate_count: 0 },
  { ...DREAM_BASE, dream_id: 'DREAM-0011', agent_name: 'product_lead', local_date: '2026-09-30', status: 'completed',
    summary: 'Identified a recurring pattern.', new_learnings_count: 1, kb_candidate_count: 1, founder_thread_id: 'THR-010' },
  { ...DREAM_BASE, dream_id: 'DREAM-0009', agent_name: 'dev_agent', local_date: '2026-09-29', status: 'failed',
    new_learnings_count: 0, kb_candidate_count: 0, error: 'Executor API returned 503' },
];
const DREAM_DETAIL = {
  ...DREAMS[1], transcript: '## Reflection\n\nIdentified a recurring pattern.',
  kb_candidates: [{
    id: 7, dream_id: 'DREAM-0011', agent_name: 'product_lead', slug: 'spanish-after-hours', title: 'Spanish after-hours routing',
    topic: 'support', rationale: 'Seen three times this week.', body_markdown: 'Route Spanish tickets after 18:00.',
    status: 'pending', promoted_kb_slug: null, created_at: iso(8 * 3600e3), updated_at: iso(8 * 3600e3),
  }],
};

// W4b todos
const TODO_WEEKLY = {
  schedule_id: 'SCHEDULE-042', agent_name: 'investment_advisor', team: 'engineering', kind: 'weekly',
  fire_at: '2026-07-25T01:00:00Z', recurrence: { day: 'Sat', time: '09:00' }, timezone: 'Asia/Shanghai',
  normalized_brief: 'Send the weekly market update — «raw» brief',
  source_instruction: 'Every Saturday, send me the weekly market update.',
  status: 'armed', active: 1, expires_at: '2026-10-23T12:00:00Z', indefinite: 0, spawned_task_ids: ['TASK-8899'],
  last_fired_at: '2026-07-18T01:00:00Z', fire_count: 3, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
};
const TODO_MONTHLY = {
  schedule_id: 'SCHEDULE-120', agent_name: 'portfolio_agent', team: 'engineering', kind: 'recurring',
  fire_at: '2026-08-10T01:00:00Z',
  recurrence: { freq: 'MONTHLY', interval: 2, ordinal: 'second', byday: ['MO'], time: '09:00', tz: 'Asia/Shanghai', until: null, count: 6, anchor_date: '2026-08-10' },
  timezone: 'Asia/Shanghai', normalized_brief: 'Review the recurring portfolio allocation',
  source_instruction: 'Review the portfolio on the second Monday every other month.',
  status: 'armed', active: 1, expires_at: null, indefinite: 0, spawned_task_ids: [], last_fired_at: null, fire_count: 0,
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
};
const TODO_FAILED = {
  ...TODO_WEEKLY, schedule_id: 'SCHEDULE-071', agent_name: 'dev_agent', normalized_brief: 'Sync the customer changelog',
  status: 'failed', recurrence: { day: 'Fri', time: '17:00' }, timezone: 'America/Chicago', fire_count: 6,
};


// W4b work-hours
const WH = {
  enabled: true,
  agents: { mode: 'all', include: [], exclude: ['support_bot'] },
  default: { mode: 'windowed', window: { start: '09:00', end: '17:00', timezone: 'UTC' }, interval: '2h', days: ['mon', 'tue', 'wed', 'thu', 'fri'], catch_up_on_startup: false },
  teams: { eng: { mode: null, window: { start: null, end: null, timezone: 'America/Los_Angeles' }, interval: null, days: null, catch_up_on_startup: null } },
  overrides: { dev_agent: { mode: null, window: { start: null, end: '19:00', timezone: null }, interval: '30m', days: null, catch_up_on_startup: null } },
};
const WH_SETTINGS = {
  system: {
    claude_cli_path: { value: '/c', restart_required: true }, codex_cli_path: { value: '/c', restart_required: true },
    opencode_cli_path: { value: '/c', restart_required: true }, pi_cli_path: { value: '/c', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: false }, queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true }, protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    dreaming: { enabled: true, schedule: { time: '02:00', timezone: 'UTC' }, catch_up_on_startup: false, agents: { mode: 'all', include: [], exclude: [] } },
    threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
    working_hours: WH,
  },
};
const WH_AGENT = (name, prompt) => ({ name, team: 'eng', role: 'worker', executor: 'claude', description: null, repos: {}, system_prompt: prompt });
const WH_WAKE = (o) => ({
  work_hour_id: 'WH-1', agent_name: 'dev_agent', local_date: '2026-09-30', slot: '09:00', mode: 'windowed',
  scheduled_for: '2026-09-30T09:00:00Z', started_at: null, ended_at: null, status: 'completed', routine_count: 2,
  spawned_task_ids: ['TASK-77'], spawned_task_count: 1, summary: 'Reviewed 3 PRs — «raw» summary.', transcript_path: null,
  session_id: null, error: null, created_at: '2026-09-30T09:00:00Z', ...o,
});


// W4b audit — one entry per narrative shape the V row asserts. Timestamps are
// one minute old so every row groups under the catalog TODAY header.
const AUDIT_ENTRIES = [
  { id: 4, task_id: 'TASK-1', session_id: 's1', agent: 'dev_agent', action: 'completion_report',
    payload: { status: 'completed', confidence: 90 }, timestamp: iso(60e3) },
  { id: 3, task_id: 'TASK-2', session_id: 's2', agent: 'code_reviewer', action: 'review_verdict',
    payload: { verdict: 'APPROVE' }, timestamp: iso(61e3) },
  { id: 2, task_id: 'THR-020', session_id: 's3', agent: 'engineering_manager', action: 'thread_dispatch',
    payload: { task_id: 'TASK-410', target_agent: 'qa_engineer', team: 'engineering' }, timestamp: iso(62e3),
    _thread_dream_id: 'DREAM-0011' },
  { id: 1, task_id: 'TASK-9', session_id: 's4', agent: null, action: 'session_end',
    payload: { duration_seconds: 80, token_usage: { total: 1500 } }, timestamp: iso(63e3) },
];


// W4c agents — a team manager (`lead`) so team-escalation-policy renders; a pending enrollment.
const W4C_LEAD = { name: 'lead', team: 'eng', role: 'manager', executor: 'claude', description: 'Leads «raw» engineering', repos: {}, system_prompt: 'You lead the eng team.' };
const W4C_ENROLLMENT = { name: 'new_hire_bot', team: 'eng', role: 'worker', executor: 'codex', description: 'Pending «raw» enrollment', status: 'pending', enrolled_by: 'lead', created_at: '2026-09-30T08:00:00Z' };
const W4C_POLICY = {
  team: 'eng', target_manager: 'lead', can_mutate: true,
  family: 'v2', contract_version: 'v2', selector_id: `APS-${'b'.repeat(64)}`, selector_epoch: 4,
  bootstrap_template: { title: 'Canonical legacy policy', normative_text: 'Normative text', clauses: [{ id: 'esc-one', category: 'protected', condition: 'Stop.', action: 'escalate_to_founder' }], continuation_phrase: 'routine follow-through' },
  v2_starter: { policy_id: 'team-eng-dual-text', title: 'Engineering escalation policy', what_to_escalate: 'Escalate starter.', what_not_to_escalate: 'Continue starter.' },
  active: {
    family: 'v2', activation_id: `APV2A-${'e'.repeat(64)}`, selector_epoch: 4, action: 'activate',
    created_at: '2026-09-03T00:00:00Z', actor_attribution: 'shared local operator credential',
    release: { id: `APV2-${'d'.repeat(64)}`, policy_id: 'team-eng-dual-text', version: 2, title: 'Eng «raw» policy title',
      what_to_escalate: 'Escalate «raw» scope changes.', what_not_to_escalate: 'Continue «raw» ordinary work.',
      digest: 'd'.repeat(64), actor_attribution: 'shared local operator credential' },
  },
};

// W4c skills
const W4C_SKILL = (o) => ({
  skill_id: 'pdf-tools', name: 'PDF Tools «raw»', type: 'managed', source: 'bundled', system_contract: false,
  visibility_category: 'toggleable', policy_class: 'standard', status: 'active', version: '1.4.2',
  validation_state: 'validated', assigned_agent_count: 1, effective_agent_count: 1, has_assigned_not_yet_effective: false,
  summary: 'Read and fill «raw» PDF forms.', ...o,
});
const W4C_SKILL_DETAIL = { ...W4C_SKILL({}), description: 'Full «raw» description of PDF tools.', when_to_use: 'When a «raw» PDF arrives.', owner: 'platform',
  validation: { ok: true, errors: [] }, assignments: [{ agent: 'dev_agent', assigned: true, effective: true, state: 'effective' }] };
const W4C_SKILL_STATUS = { skill_id: 'pdf-tools', source: 'bundled', in_catalog: true, validated: true, current_version: '1.4.2',
  assignments: [{ agent: 'dev_agent', assigned: true, effective: true, materialized_version: '1.4.2', state: 'effective' }],
  last_validation: null };
const W4C_VALIDATION = { label: 'Recent', events: [
  { id: 2, skill_id: 'pdf-tools', slug: 'pdf-tools', agent: 'dev_agent', source: 'first_party', severity: 'error', ok: false, version: '1.4.2',
    findings: ['Missing «raw» frontmatter field'], reason_codes: ['frontmatter_missing'], created_at: iso(5 * 60e3) },
  { id: 1, skill_id: 'pdf-tools', slug: 'pdf-tools', agent: null, source: 'materialization', severity: 'info', ok: true, version: '1.4.1',
    findings: [], reason_codes: [], created_at: iso(2 * 3600e3) },
] };
const W4C_CUSTOM = { skill_id: 'CS-1', slug: 'release-notes', name: 'Release Notes «raw»', description: 'Draft «raw» release notes.',
  current_version_id: 3, retired_at: null, validation_state: 'validated', content_hash: 'f'.repeat(64),
  skill_md_cache: '---\nname: release-notes\n---\n# Release «raw» notes', hidden_reason: null };
const W4C_POLICY_HISTORY = { items: [], next_cursor: null };
// The shared /agents roster. W4c adds the `lead` manager (the policy page and
// entry card require a live manager with a unique teams.yaml registration);
// the W4b EligibilityEditor S row derives its expected live count from this list.
const ROSTER = [WH_AGENT('dev_agent', '## Routine Tasks\n- Review open PRs\n- Triage bugs'), WH_AGENT('support_bot', 'No routine section here.'), W4C_LEAD];

/** pathname -> payload (or (search) => payload). Later W4 slices add rows here. */
const W4D_KB = { slug: 'raw-knowledge', title: 'Authored «raw» Knowledge', type: 'RAW_Type', topic: 'Raw_Topic', tags: ['Raw_Tag'], body: 'Authored «raw» KB body.', authored_by: 'Raw_Agent', source_task: 'TASK-0042', related_entries: [], updated_at: iso(3600e3) };
const W4D_ARTIFACTS = [
  { name: 'Raw_Agent-2026-06-16-THR-042-Raw_Title.pdf', size_bytes: 1536, modified_at: '2026-06-20T14:30:00Z' },
  { name: 'Raw_Folder/Raw_File.txt', size_bytes: 512, modified_at: '' },
];
const API_ROUTES = {
  [`/api/v1/orgs/${ORG}/kb`]: { entries: [W4D_KB] },
  [`/api/v1/orgs/${ORG}/kb/stats`]: { entries: [{ slug: W4D_KB.slug, view_count: 1000 }] },
  [`/api/v1/orgs/${ORG}/kb/raw-knowledge`]: W4D_KB,
  [`/api/v1/orgs/${ORG}/artifacts`]: { artifacts: W4D_ARTIFACTS },
  '/api/v1/auth/bootstrap': { token: 'w4a-evidence-token' },
  '/api/v1/orgs': { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] },
  [`/api/v1/orgs/${ORG}/dashboard/summary`]: { org_age_days: 12 },
  // W4a-1 health
  '/api/v1/metrics': HEALTH_SNAPSHOT,
  '/api/v1/metrics/history': {
    snapshots: [1, 2, 3].map((id) => ({ id, captured_at: iso(id * 60e3), snapshot_json: JSON.stringify({ ...HEALTH_SNAPSHOT, run_step_queue_depth: id }) })),
  },
  // W4a-1 dreams
  [`/api/v1/orgs/${ORG}/dreams`]: { dreams: DREAMS },
  [`/api/v1/orgs/${ORG}/dreams/DREAM-0011`]: DREAM_DETAIL,
  // W4b todos
  [`/api/v1/orgs/${ORG}/schedules`]: { schedules: [TODO_WEEKLY, TODO_MONTHLY, TODO_FAILED] },
  [`/api/v1/orgs/${ORG}/schedules/SCHEDULE-042`]: TODO_WEEKLY,
  [`/api/v1/orgs/${ORG}/schedules/SCHEDULE-120`]: TODO_MONTHLY,
  // W4b work-hours
  [`/api/v1/orgs/${ORG}/settings`]: WH_SETTINGS,
  [`/api/v1/orgs/${ORG}/agents`]: { agents: ROSTER },
  [`/api/v1/orgs/${ORG}/teams`]: { teams: [{ name: 'eng', manager: 'lead', workers: ['dev_agent', 'support_bot'] }] },
  [`/api/v1/orgs/${ORG}/work-hours`]: { work_hours: [
    WH_WAKE({}),
    WH_WAKE({ work_hour_id: 'WH-2', slot: '11:00', scheduled_for: '2026-09-30T11:00:00Z', status: 'failed', routine_count: 1, spawned_task_ids: [], summary: null, error: 'Executor exited 137' }),
    WH_WAKE({ work_hour_id: 'WH-3', agent_name: 'qa_engineer', status: 'weird_state', routine_count: 0, spawned_task_ids: [], summary: null }),
  ] },
  [`/api/v1/orgs/${ORG}/work-hours/next-wakes`]: { agent: 'dev_agent', enabled: true, timezone: 'America/Los_Angeles', mode: 'windowed', next_wakes: ['2026-10-01T15:00:00-07:00'], error: null },
  // W4b audit
  [`/api/v1/orgs/${ORG}/audit`]: { entries: AUDIT_ENTRIES, next_cursor: null },
  // W4c agents
  [`/api/v1/orgs/${ORG}/agents/enrollments`]: { enrollments: [W4C_ENROLLMENT] },
  ...Object.fromEntries(ROSTER.flatMap((a) => [
    [`/api/v1/orgs/${ORG}/agents/${a.name}/memory/entries/`, { entries: [] }],
    [`/api/v1/orgs/${ORG}/agents/${a.name}/cleanup-activity`, { activities: [] }],
  ])),
  [`/api/v1/orgs/${ORG}/tasks`]: { tasks: [], next_cursor: null },
  [`/api/v1/orgs/${ORG}/jobs/`]: { jobs: [] },
  '/api/v1/executors/runtime/profiles': { profiles: [] },
  '/api/v1/health/prereqs': { prereqs: ['claude', 'codex'].map((tool) => ({ tool, present: true, path: `/usr/bin/${tool}`, hint: '' })) },
  [`/api/v1/orgs/${ORG}/agents/lead/team-escalation-policy`]: W4C_POLICY,
  [`/api/v1/orgs/${ORG}/agents/lead/team-escalation-policy/v2/history`]: W4C_POLICY_HISTORY,
  // W4c skills
  [`/api/v1/orgs/${ORG}/skills/catalog`]: { items: [W4C_SKILL({})] },
  [`/api/v1/orgs/${ORG}/skills/catalog/pdf-tools`]: W4C_SKILL_DETAIL,
  [`/api/v1/orgs/${ORG}/skills/pdf-tools/status`]: W4C_SKILL_STATUS,
  [`/api/v1/orgs/${ORG}/skills/validation`]: W4C_VALIDATION,
  [`/api/v1/orgs/${ORG}/custom-skills/catalog`]: { skills: [W4C_CUSTOM] },
  [`/api/v1/orgs/${ORG}/custom-skills/CS-1`]: W4C_CUSTOM,
  [`/api/v1/orgs/${ORG}/custom-skills/CS-1/eligibility`]: { rules: [{ scope_type: 'team', scope_target: 'eng', effect: 'allow' }], revision: 2 },
  [`/api/v1/orgs/${ORG}/custom-skills/CS-1/versions`]: { versions: [{ id: 3, content_hash: 'f'.repeat(64), created_at: '2026-09-30T08:00:00Z', author_kind: 'founder', validation_state: 'validated' }] },
};

function api(pathname, search) {
  const row = API_ROUTES[pathname];
  if (row === undefined) return {};
  return typeof row === 'function' ? row(search) : row;
}

function startServer(root) {
  return new Promise((ok, fail) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        const p = url.pathname;
        if (p.startsWith('/api/')) {
          LEDGER.push({ method: request.method, path: p, search: url.search, t: Date.now() });
          if (p.endsWith('/events') || p.includes('/stream') || p.endsWith('/tail')) {
            response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
            HUNG.push(response);
            return;
          }
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(api(p, url.search)));
          return;
        }
        if (p === '/__w4a_blank') {
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
          response.end('<!doctype html><title>w4a second tab</title>');
          return;
        }
        let file = p === '/' ? join(root, 'index.html') : join(root, decodeURIComponent(p));
        if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
        response.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
        response.end(readFileSync(file));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.on('error', fail);
    server.listen(0, '127.0.0.1', () => ok({ server, url: `http://127.0.0.1:${server.address().port}` }));
  });
}

const seedLocale = (locale) => `try { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); } catch (e) {}`;
const CHINESE_NAVIGATOR = `(() => {
  Object.defineProperty(Navigator.prototype, 'language', { get: () => 'zh-CN', configurable: true });
  Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
})();`;
// textContent, not innerText: CSS text-transform must not hide catalog bytes.
const bodyHas = (s) => `document.body.textContent.includes(${JSON.stringify(s)})`;
const langIs = (l) => `document.documentElement.lang === ${JSON.stringify(l)}`;
const noOverflow = `document.documentElement.scrollWidth <= innerWidth + 1`;
/** Cards (parents of rows matching `rowSel`) whose content is wider than the card; `cards` keeps it non-vacuous. */
const cardsContain = (rowSel) => `(() => {
  const cards = [...new Set([...document.querySelectorAll(${JSON.stringify(rowSel)})].map((a) => a.parentElement))];
  return { cards: cards.length > 0, over: cards.filter((c) => c.scrollWidth > c.clientWidth).map((c) => \`clientW=\${c.clientWidth} scrollW=\${c.scrollWidth}\`) };
})()`;

// ------------------------------------------------------------------ route tables
/** W4c: every bordered rounded card on the page (parent of any child) must hold its content. */
const BORDERED_CARD_CHILD = '[class*="rounded"][class*="border"] > *';
const DREAM_CARD = (id) => `[...document.querySelectorAll('li > button')].find((b) => b.textContent.includes(${JSON.stringify(id)}))`;

/**
 * Case V rows. `keys` are catalog keys (string or [key, params]) that must be
 * visible; `verbatim` are daemon bytes that must render unchanged; `ready` is
 * the content predicate; optional `prep(page, h)` drives the page to the state.
 */
const VIEW_ROUTES = [
  {
    id: 'kb-list', route: 'kb', path: `/orgs/${ORG}/kb`, ready: () => bodyHas(W4D_KB.title),
    keys: ['kb.pageTitle', 'kb.railAllEntries', 'kb.railTagsSection', ['kb.headerEyebrow', { count: 1, number: '1' }], ['kb.viewedLabel', { count: 1000, number: '1,000' }]],
    verbatim: [W4D_KB.title, 'RAW_Type', 'Raw_Tag', 'raw-knowledge'],
    checks: () => [
      ['heading and entry text fit their containers', `(() => {
        const main = document.querySelector('main main');
        const heading = main?.querySelector('h1');
        const title = [...(main?.querySelectorAll('a span') ?? [])].find(el => el.textContent === ${JSON.stringify(W4D_KB.title)});
        if (!main || !heading || !title) return false;
        return [heading, title].every(el => {
          const range = document.createRange(); range.selectNodeContents(el);
          const rects = [...range.getClientRects()];
          if (!rects.length) return false;
          return rects.every(r => {
            if (r.width <= 0 || r.left < 0 || r.right > innerWidth) return false;
            for (let parent = el; parent; parent = parent.parentElement) {
              if (!['hidden', 'auto', 'scroll', 'clip'].includes(getComputedStyle(parent).overflowX)) continue;
              const box = parent.getBoundingClientRect();
              if (r.left < box.left - 1 || r.right > box.right + 1) return false;
            }
            return true;
          });
        });
      })()`, true],
      ['raw type has no text transform', `(() => { const badge = [...document.querySelectorAll('main a span')].find(el => el.textContent === 'RAW_Type'); return Boolean(badge && getComputedStyle(badge).textTransform === 'none'); })()`, true],
    ],
  },
  {
    id: 'kb-detail', route: 'kb detail', path: `/orgs/${ORG}/kb/raw-knowledge`, ready: () => bodyHas(W4D_KB.body),
    keys: ['kb.pageTitle', 'kb.sourceTaskLabel', ['kb.authoredBy', { agent: 'Raw_Agent' }]],
    verbatim: [W4D_KB.title, W4D_KB.body, 'Raw_Agent', 'TASK-0042'],
    // The closed Assistant dock also stays mounted with role=dialog. Bind the
    // bounds oracle to this open drawer and its verbatim authored title.
    checks: () => [['drawer fits viewport', `(() => { const el = [...document.querySelectorAll('[role="dialog"][data-state="open"]')].find(d => d.querySelector('h2')?.textContent === ${JSON.stringify(W4D_KB.title)}); if (!el) return false; const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; })()`, true]],
  },
  {
    id: 'kb-candidates', route: 'kb candidates', path: `/orgs/${ORG}/kb`, ready: () => bodyHas(W4D_KB.title),
    prep: async (page, h) => {
      const SELECT = `[...document.querySelectorAll('aside button')].find(b => b.textContent.includes(${JSON.stringify(tr('en', 'kb.railCandidates'))}) || b.textContent.includes(${JSON.stringify(tr('zh-CN', 'kb.railCandidates'))}))`;
      await h.waitTrue(page, `Boolean(${SELECT})`, 'KB candidates count'); await h.clickSrc(page, SELECT);
      await h.waitTrue(page, bodyHas('Spanish after-hours routing'), 'KB candidate row');
      await h.clickSrc(page, `[...document.querySelectorAll('button')].find(b => b.textContent.includes('Spanish after-hours routing'))`);
      await h.waitTrue(page, `[...document.querySelectorAll('[role="dialog"][data-state="open"]')].some(d => d.querySelector('h2')?.textContent === 'Spanish after-hours routing')`, 'KB candidate detail');
    },
    keys: ['kb.acceptButton', 'kb.dismissButton', ['kb.candidatePendingLabel', { agent: 'product_lead' }]],
    verbatim: ['Spanish after-hours routing', 'spanish-after-hours', 'Seen three times this week.', 'product_lead'],
  },
  {
    id: 'artifacts-list', route: 'artifacts', path: `/orgs/${ORG}/artifacts`, ready: () => bodyHas('THR-042-Raw_Title.pdf'),
    keys: ['artifacts.pageTitle', 'artifacts.type.doc', 'artifacts.fromFilename', 'artifacts.download'],
    checks: locale => [['localized filter accessible name', `document.querySelector('[role="tablist"]').getAttribute('aria-label')`, tr(locale, 'artifacts.filterLabel')]],
    verbatim: ['Raw_Agent', 'Raw_Folder/', 'THR-042', 'THR-042-Raw_Title.pdf', '1.5 KB'],
  },
  {
    id: 'artifacts-folder', route: 'artifacts folder', path: `/orgs/${ORG}/artifacts`, ready: () => bodyHas('Raw_Folder/'),
    prep: async (page, h) => { await h.clickSrc(page, `[...document.querySelectorAll('button')].find(b => b.textContent.includes('Raw_Folder/'))`); await h.waitTrue(page, bodyHas('Raw_File.txt'), 'artifact folder'); },
    keys: ['artifacts.root', 'artifacts.modifiedUnavailable', 'artifacts.sortFolders', 'artifacts.download'],
    verbatim: ['Raw_Folder', 'Raw_File.txt', '512 B'],
  },
  {
    id: 'artifacts-upload', route: 'artifacts upload', path: `/orgs/${ORG}/artifacts`, ready: () => bodyHas('THR-042-Raw_Title.pdf'),
    prep: async (page, h) => { await h.clickSrc(page, `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(tr('en', 'artifacts.upload'))} || b.textContent.trim() === ${JSON.stringify(tr('zh-CN', 'artifacts.upload'))})`); },
    keys: ['artifacts.uploadTitle', 'artifacts.file', 'artifacts.nameLabel', 'artifacts.nameHint', 'common.cancel'],
    verbatim: ['[A-Za-z0-9._-]+', '10 MB'],
  },

  {
    id: 'health', route: 'health', path: `/orgs/${ORG}/health`,
    ready: () => `${bodyHas('work_hours_scheduler_loop')} && ${bodyHas('GET /api/v1/orgs/{slug}/tasks')}`,
    keys: [
      'health.title', 'health.meta', 'health.metric.uptime', 'health.metric.uptimeHint', 'health.metric.queue',
      'health.loops.title', 'health.loops.column.lastDuration', 'health.http.title', 'health.http.allRoutes',
      'health.metric.p95', ['health.trends.title', { window: '@health.window.24h' }], ['health.trends.snapshots', { count: 3 }],
      ['health.uptime.hours', { hours: 3, minutes: 14 }], ['health.loops.interval', { value: 60 }],
    ],
    verbatim: ['work_hours_scheduler_loop', 'GET /api/v1/orgs/{slug}/tasks'],
  },
  {
    id: 'dreams', route: 'dreams', path: `/orgs/${ORG}/dreams`,
    ready: () => `${bodyHas('DREAM-0012')} && ${bodyHas('DREAM-0009')}`,
    keys: [
      ['dreams.header.eyebrow', { count: 2 }], 'dreams.header.statement', ['dreams.feed.count', { count: 3 }],
      'dreams.status.completed', 'dreams.status.failed', 'dreams.quiet.title', 'dreams.thread.open', 'dreams.thread.none',
      'dreams.rail.overview', 'dreams.rail.candidates', 'dreams.rail.scheduleNote', ['dreams.count.reflections', { count: 3 }],
    ],
    verbatim: ['DREAM-0012', 'engineering_manager', 'Routine nightly reflection — «raw» summary.', 'Executor API returned 503', '2026-09-30'],
  },
  {
    id: 'dream-drawer', route: 'dreams (detail drawer)', path: `/orgs/${ORG}/dreams`,
    ready: () => `${bodyHas('DREAM-0011')}`,
    prep: async (page, h) => {
      await h.clickSrc(page, DREAM_CARD('DREAM-0011'));
      await h.waitTrue(page, bodyHas('Spanish after-hours routing'), 'drawer content');
    },
    keys: [
      ['dreams.candidate.pending', { agent: 'product_lead' }], 'dreams.candidate.accept', 'dreams.candidate.dismiss',
      ['dreams.detail.toReview', { count: 1 }], 'dreams.rail.candidates',
    ],
    verbatim: ['product_lead · 2026-09-30', 'Spanish after-hours routing', 'spanish-after-hours', 'Seen three times this week.'],
  },
  {
    id: 'todos', route: 'todos', path: `/orgs/${ORG}/todos`,
    ready: () => `${bodyHas('SCHEDULE-042')} && ${bodyHas('SCHEDULE-120')} && ${bodyHas('SCHEDULE-071')}`,
    keys: [
      'todos.list.eyebrow', 'todos.list.title', 'todos.list.subtitle', 'todos.list.trustLine',
      'todos.filter.all', 'todos.group.active', 'todos.group.paused', 'todos.group.needsAttention', 'todos.group.history',
      'todos.filter.allAgents', ['todos.list.summaryActive', { count: 2, n: '2' }], ['todos.list.summaryAttention', { count: 1, n: '1' }],
      'todos.status.armed', 'todos.status.failed', 'todos.row.nextFire', ['todos.row.runs', { count: 3, n: '3' }],
      ['todos.schedule.every', { day: '@todos.weekdayShort.sat', time: '09:00' }],
      ['todos.schedule.wasEvery', { day: '@todos.weekdayShort.fri', time: '17:00' }],
      ['todos.recurrence.endsAfter', { count: '6' }],
    ],
    verbatim: ['SCHEDULE-042', 'investment_advisor', 'portfolio_agent', 'Send the weekly market update — «raw» brief', 'Asia/Shanghai'],
    contained: () => cardsContain(`a[href^="/orgs/${ORG}/todos/"]`),
  },
  {
    id: 'todo-detail', route: 'todos/:scheduleId', path: `/orgs/${ORG}/todos/SCHEDULE-042`,
    ready: () => `${bodyHas('Every Saturday, send me the weekly market update.')}`,
    keys: [
      'todos.list.title', 'todos.action.pause', 'todos.action.edit', 'todos.action.cancel', 'todos.row.nextFire',
      'todos.detail.recurrence', 'todos.kind.weekly', 'todos.detail.schedule', 'todos.detail.timezone', 'todos.detail.review',
      'todos.detail.normalized', 'todos.detail.original', 'todos.detail.activity', 'todos.detail.runs', 'todos.detail.lastFired',
      'todos.detail.viewActivity', 'todos.detail.recordDetails', 'todos.detail.created', 'todos.detail.updated',
      'todos.detail.team', 'todos.detail.scheduleId',
      ['todos.schedule.every', { day: '@todos.weekdayShort.sat', time: '09:00' }],
    ],
    verbatim: ['SCHEDULE-042', 'investment_advisor', 'engineering', 'Asia/Shanghai', 'TASK-8899', 'task_id=SCHEDULE-042',
      'Every Saturday, send me the weekly market update.', 'Send the weekly market update — «raw» brief'],
  },
  {
    id: 'todo-detail-recurring', route: 'todos/:scheduleId (recurring)', path: `/orgs/${ORG}/todos/SCHEDULE-120`,
    ready: () => `${bodyHas('Review the portfolio on the second Monday every other month.')}`,
    keys: [
      'todos.kind.recurring',
      ['todos.recurrence.endsAfter', { count: '6' }],
    ],
    verbatim: ['SCHEDULE-120', 'portfolio_agent', 'Asia/Shanghai'],
  },
  {
    id: 'work-hours', route: 'work-hours', path: `/orgs/${ORG}/work-hours`,
    ready: () => `${bodyHas('support_bot')} && ${bodyHas('America/Los_Angeles')}`,
    keys: [
      'workHours.header.eyebrow', 'workHours.header.title', 'workHours.header.wakeHistory', 'workHours.tabs.overview',
      'workHours.tabs.wakes', 'workHours.statusBar.label', 'workHours.statusBar.on', 'workHours.manageOperatingControl',
      'workHours.editOrgDefault', 'workHours.editTeamPlaceholder', 'workHours.roster.cadence', 'workHours.roster.eligibility',
      'workHours.eligibility.eligible', 'workHours.eligibility.excluded', 'workHours.onDot.on', 'workHours.onDot.off',
      ['workHours.cadence.every', { interval: '30m' }],
    ],
    verbatim: ['dev_agent', 'support_bot', 'windowed', '09:00–19:00 mon,tue,wed,thu,fri America/Los_Angeles'],
  },
  {
    id: 'work-hours-agent', route: 'work-hours/:agent', path: `/orgs/${ORG}/work-hours/dev_agent`,
    ready: () => `${bodyHas('Review open PRs')} && ${bodyHas('window.timezone')}`,
    keys: [
      'workHours.detail.backToWorkHours', 'workHours.detail.provenanceHeading', ['workHours.editTeam', { team: 'eng' }],
      'workHours.detail.editAgent', 'workHours.detail.col.leaf', 'workHours.detail.col.effective', 'workHours.provenance.org',
      'workHours.provenance.agent', ['workHours.provenance.teamNamed', { team: 'eng' }], 'workHours.detail.nextWakes',
      ['workHours.detail.dispatches', { tasks: 'Review open PRs; Triage bugs' }], 'workHours.detail.routineHeading',
      'workHours.detail.routineReadOnly', 'workHours.detail.routineInfo',
    ],
    verbatim: ['dev_agent', 'window.timezone', '▶ 30m', '▶ America/Los_Angeles', '(America/Los_Angeles)', 'Review open PRs', 'Triage bugs', '## Routine Tasks'],
  },
  {
    id: 'work-hours-wakes', route: 'work-hours?view=wakes', path: `/orgs/${ORG}/work-hours?view=wakes`,
    ready: () => `${bodyHas('Executor exited 137')} && ${bodyHas('weird_state')}`,
    keys: [
      'workHours.wakes.eyebrow', 'workHours.wakes.title', 'workHours.wakes.description', 'workHours.wakes.viewOnly',
      'workHours.wakes.status.completed', 'workHours.wakes.status.failed', ['workHours.wakes.cardCount', { count: 2 }],
      ['workHours.wakes.cardCount', { count: 1 }], ['workHours.wakes.routines', { count: 2 }],
    ],
    // The eyebrow "{n} wakes across {n} agents" is split by <span> nodes; textContent still matches:
    // en "3 wakes across 2 agents", zh-CN "3 次唤醒，涉及 2 个智能体" (add as a bodyHas literal per locale if desired).
    verbatim: ['dev_agent', 'qa_engineer', 'weird_state', 'Reviewed 3 PRs — «raw» summary.', 'Executor exited 137', 'TASK-77', '2026-09-30', '09:00'],
  },
  {
    id: 'audit', route: 'audit', path: `/orgs/${ORG}/audit`,
    ready: () => `${bodyHas('TASK-410')} && ${bodyHas('code_reviewer')}`,
    keys: [
      'audit.page.eyebrow', 'audit.page.title', 'audit.page.export',
      'audit.since.24h', 'audit.since.7d', 'audit.since.all',
      'audit.rail.title', 'audit.class.dispatch', 'audit.class.completed', 'audit.class.merge',
      'audit.class.escalation', 'audit.class.failure',
      'audit.clean.title', ['audit.clean.body', { count: 4 }],
      'audit.day.today', 'audit.timeline.fromDream', 'audit.timeline.end',
      ['audit.n.completion_report', { agent: 'dev_agent', target: 'TASK-1' }],
      ['audit.n.review_verdict', { agent: 'code_reviewer', target: 'TASK-2' }],
      ['audit.n.thread_dispatch.to', { agent: 'engineering_manager', task: 'TASK-410', who: 'qa_engineer' }],
      ['audit.n.session_end', { agent: '@audit.n.subject.system', target: 'TASK-9' }],
      ['audit.detail.confidence', { value: 90 }],
      ['audit.detail.team', { team: 'engineering' }],
      ['audit.detail.minutesSeconds', { m: 1, s: 20 }],
    ],
    verbatim: ['dev_agent', 'code_reviewer', 'engineering_manager', 'qa_engineer', 'TASK-1', 'TASK-410', 'APPROVE', 'completed · '],
  },
  {
    id: 'agents', route: 'agents', path: `/orgs/${ORG}/agents`,
    ready: () => `${bodyHas('support_bot')} && ${bodyHas('dev_agent')}`,
    keys: ['agents.page.title', 'agents.page.meta', 'agents.page.newAgent', 'agents.tab.active', 'agents.tab.pending'],
    verbatim: ['dev_agent', 'support_bot', 'lead', 'worker', 'manager'],
    checks: () => [['roster roles are exact daemon bytes', `(() => {
      const roster = document.querySelectorAll('aside')[1];
      return [...roster.querySelectorAll('li > button')].map((button) => [
        button.querySelector('.font-display').textContent,
        [...button.querySelectorAll('div')].find((node) => node.classList.contains('mt-0.5') && node.classList.contains('text-xs')).textContent,
      ]);
    })()`, ROSTER.map((agent) => [agent.name, agent.role])]],
    contained: () => cardsContain('li > button > *'),
  },
  {
    id: 'agent-detail', route: 'agents/:agent_name', path: `/orgs/${ORG}/agents/lead`,
    ready: (locale) => `${bodyHas(tr(locale, 'agents.policy.entryTitle'))}`,
    keys: [
      'agents.page.title', 'agents.field.description', 'agents.field.systemPrompt', 'agents.executor.label', 'agents.detail.model',
      'agents.detail.repos', 'agents.detail.learnings', 'agents.detail.recentTasks',
      'agents.policy.entryTitle', ['agents.policy.entryMeta', { team: 'eng', name: 'lead' }], 'agents.policy.open',
    ],
    verbatim: ['lead', 'support_bot', 'eng', 'manager', 'worker'],
    checks: (_locale, width) => [['main min-height and body direction match breakpoint', `(() => {
      const roster = document.querySelectorAll('aside')[1];
      const body = roster.parentElement;
      const main = body.querySelector(':scope > main');
      return { minHeight: getComputedStyle(main).minHeight, direction: getComputedStyle(body).flexDirection };
    })()`, { minHeight: width < 768 ? '0px' : 'auto', direction: width < 768 ? 'column' : 'row' }]],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
  {
    id: 'agent-policy', route: 'agents/:agent_name/team-escalation-policy', path: `/orgs/${ORG}/agents/lead/team-escalation-policy`,
    ready: (locale) => `${bodyHas(tr(locale, 'agents.policy.history.empty'))}`,
    keys: [
      // Agent/team identifiers stay byte-verbatim; the freeform policy title is separate.
      'agents.policy.title', ['agents.policy.backTo', { name: 'lead' }], 'agents.policy.teamOwned', ['agents.policy.ownedBy', { team: 'eng' }],
      'agents.policy.whatTo', 'agents.policy.whatNot', 'agents.policy.save', 'agents.policy.history.title', 'agents.policy.history.empty',
    ],
    verbatim: ['Eng «raw» policy title', 'team-eng-dual-text', `APV2-${'d'.repeat(64)}`, `APS-${'b'.repeat(64)}`],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
  {
    id: 'skills', route: 'skills', path: `/orgs/${ORG}/skills`,
    ready: () => `${bodyHas('PDF Tools «raw»')}`,
    keys: ['skills.catalog.heading', 'skills.catalog.runtimeValidation', 'skills.addCustom', 'skills.card.assigned', 'skills.card.effective'],
    verbatim: ['PDF Tools «raw»', 'Read and fill «raw» PDF forms.', '1.4.2'],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
  {
    id: 'skill-detail', route: 'skills/:skillId', path: `/orgs/${ORG}/skills/pdf-tools`,
    ready: () => `${bodyHas('Full «raw» description of PDF tools.')}`,
    keys: ['skills.detail.back', 'skills.detail.whenToUse', 'skills.detail.whereEffective', 'skills.detail.perAgent', 'skills.catalog.source'],
    verbatim: ['PDF Tools «raw»', 'When a «raw» PDF arrives.', 'dev_agent', '1.4.2'],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
  {
    id: 'skills-validation', route: 'skills/validation', path: `/orgs/${ORG}/skills/validation`,
    ready: () => `${bodyHas('Missing «raw» frontmatter field')}`,
    keys: ['skills.validation.description', 'skills.validation.filters'],
    verbatim: ['Missing «raw» frontmatter field', 'pdf-tools', 'dev_agent', '1.4.2'],
    contained: () => cardsContain('article[data-event-id] > *'),
  },
  {
    id: 'skills-custom', route: 'skills/custom', path: `/orgs/${ORG}/skills/custom`,
    ready: () => `${bodyHas('Release Notes «raw»')}`,
    keys: ['skills.customList.title', 'skills.addCustom', 'skills.view.current'],
    verbatim: ['Release Notes «raw»', 'Draft «raw» release notes.', 'v3'],
    contained: () => cardsContain('article[data-source="custom"] > *'),
  },
  {
    id: 'skills-custom-new', route: 'skills/custom/new', path: `/orgs/${ORG}/skills/custom/new`,
    ready: (locale) => `${bodyHas(tr(locale, 'skills.create.title'))}`,
    keys: ['skills.create.title', 'skills.create.name', 'skills.create.slug', 'skills.create.descriptionLabel', 'skills.backToCustom'],
    verbatim: [],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
  {
    id: 'skills-custom-detail', route: 'skills/custom/:skillId', path: `/orgs/${ORG}/skills/custom/CS-1`,
    ready: (locale) => `${bodyHas(tr(locale, 'skills.customDetail.versions'))}`,
    keys: ['skills.backToCustom', 'skills.customDetail.metadata', 'skills.customDetail.versions', 'skills.customDetail.eligibility', 'skills.customDetail.currentGuidance', 'skills.customDetail.retire'],
    verbatim: ['release-notes'],
    contained: () => cardsContain(BORDERED_CARD_CHILD),
  },
];

/**
 * Case S rows (surfaces with an input or dialog). `open` drives to the
 * surface; `container`/`control` are DOM expressions; `copy(locale)` lists
 * catalog strings that must be visible after each switch; `draft` (optional)
 * is typed into the control first.
 */
const SWITCH_ROUTES = [
  {
    id: 'artifacts-upload-file', path: `/orgs/${ORG}/artifacts`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('THR-042-Raw_Title.pdf'), 'artifact grid');
      await h.clickSrc(page, `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Upload')`);
      await h.waitTrue(page, `Boolean(document.querySelector('input[type="file"]'))`, 'upload form');
      await h.evaluate(page, `(() => {
        const input = document.querySelector('input[type="file"]'); const selected = new File(['exact raw bytes'], 'Raw_Selected.pdf', { type: 'application/pdf' });
        const data = new DataTransfer(); data.items.add(selected); input.files = data.files; input.dispatchEvent(new Event('change', { bubbles: true }));
        window.__w4dSelected = input.files[0]; window.__w4dFileInput = input;
        window.__w4dUploadButton = [...document.querySelectorAll('section button')].find(b => b.textContent.trim() === 'Upload'); return true;
      })()`);
    },
    control: `document.querySelector('section input[type="text"]')`, container: `CONTROL.closest('section')`, draft: 'Raw_Draft.pdf',
    copy: locale => [tr(locale, 'artifacts.uploadTitle'), tr(locale, 'artifacts.nameLabel'), tr(locale, 'common.cancel')],
    checks: () => [['selected File and file-input node retained', `document.querySelector('input[type="file"]') === window.__w4dFileInput && window.__w4dFileInput.files[0] === window.__w4dSelected && window.__w4dSelected.name === 'Raw_Selected.pdf'`, true], ['upload control retained', `window.__w4dUploadButton.isConnected && window.__w4dUploadButton.closest('section') === window.__w4aBox.deref()`, true]],
    shot: 'zh-artifacts-upload-switch-1440',
  },
  {
    id: 'dream-drawer-accept', path: `/orgs/${ORG}/dreams`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('DREAM-0011'), 'feed');
      await h.clickSrc(page, DREAM_CARD('DREAM-0011'));
      await h.waitTrue(page, bodyHas('Spanish after-hours routing'), 'drawer content');
    },
    control: `[...document.querySelectorAll('[role="dialog"] button')].find((b) => b.getAttribute('aria-label') === ${JSON.stringify(tr('en', 'dreams.candidate.accept'))} || b.getAttribute('aria-label') === ${JSON.stringify(tr('zh-CN', 'dreams.candidate.accept'))})`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    copy: (locale) => [tr(locale, 'dreams.candidate.pending', { agent: 'product_lead' }), tr(locale, 'dreams.candidate.accept'), tr(locale, 'dreams.rail.candidates')],
    shot: 'zh-dream-drawer-switch-1440',
  },
  {
    // Recurring Edit dialog: the "Repeat every" number input. `open` clears it via the
    // React-compatible native setter so the typed draft equals the control value exactly.
    id: 'todo-edit-dialog', path: `/orgs/${ORG}/todos/SCHEDULE-120`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('Review the portfolio on the second Monday every other month.'), 'detail');
      const EDIT = `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(tr('en', 'todos.action.edit'))})`;
      await h.clickSrc(page, EDIT);
      await h.waitTrue(page, `Boolean(document.getElementById('edit-recurrence-interval'))`, 'edit dialog');
      await h.evaluate(page, `(() => {
        const i = document.getElementById('edit-recurrence-interval');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, '');
        i.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      })()`);
    },
    control: `document.getElementById('edit-recurrence-interval')`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    draft: '3',
    copy: (locale) => [
      tr(locale, 'todos.edit.title'), tr(locale, 'todos.edit.repeatEvery'), tr(locale, 'todos.edit.monthlyPattern'),
      tr(locale, 'todos.edit.namedWeekday'), tr(locale, 'todos.edit.save'), tr(locale, 'todos.edit.rephase'),
    ],
    shot: 'zh-todo-edit-switch-1440',
  },
  {
    // TierEditorDialog (team tier, empty interval so the typed draft equals the value).
    id: 'work-hours-tier-editor', path: `/orgs/${ORG}/work-hours/dev_agent`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('window.timezone'), 'detail table');
      await h.clickSrc(page, `[...document.querySelectorAll('button')].find((b) => b.textContent === ${JSON.stringify(tr('en', 'workHours.editTeam', { team: 'eng' }))})`);
      await h.waitTrue(page, `Boolean(document.querySelector('[role="dialog"] input[placeholder="2h"]'))`, 'tier dialog');
    },
    control: `document.querySelector('[role="dialog"] input[placeholder="2h"]')`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    draft: '5h',
    copy: (locale) => [
      tr(locale, 'workHours.editTeam', { team: 'eng' }),
      tr(locale, 'workHours.tier.description', { tier: tr(locale, 'workHours.tier.kind.team') }),
      tr(locale, 'workHours.tier.inheritedGhost', { value: '09:00', source: tr(locale, 'workHours.provenance.org') }),
      tr(locale, 'workHours.dialog.reviewImpact'),
    ],
    shot: 'zh-work-hours-tier-editor-switch-1440',
  },
  {
    // Shared EligibilityEditorDialog, mounted by Settings > Organization. Draft = a toggled
    // include chip (no text input), so no `draft`; the toggled state is checked via copy
    // (the live result line counts the toggled agent).
    id: 'eligibility-editor', path: `/orgs/${ORG}/settings/organization`,
    open: async (page, h) => {
      const editLabel = (l) => tr(l, 'settings.organization.operating.editEligibility');
      await h.waitTrue(page, `[...document.querySelectorAll('button')].some((b) => b.textContent === ${JSON.stringify(editLabel('en'))})`, 'org section');
      await h.clickSrc(page, `[...document.querySelectorAll('button')].find((b) => b.textContent === ${JSON.stringify(editLabel('en'))})`);
      const CHIP = `[...document.querySelectorAll('[role="dialog"] button[aria-pressed]')].find((b) => b.textContent === 'support_bot')`;
      await h.waitTrue(page, `Boolean(${CHIP})`, 'eligibility dialog chip');
      await sleep(400); // let the dialog open animation settle so the click lands on the chip
      // toggle support_bot OFF the exclude list (mode 'all' => only the exclude picker renders)
      await h.clickSrc(page, CHIP);
      await h.waitTrue(page, `${CHIP}.getAttribute('aria-pressed') === 'false'`, 'support_bot toggled');
    },
    control: `[...document.querySelectorAll('[role="dialog"] button[aria-pressed]')].find((b) => b.textContent === 'support_bot')`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    copy: (locale) => [
      tr(locale, 'workHours.eligibilityEditor.title'),
      tr(locale, 'workHours.eligibilityEditor.description'),
      tr(locale, 'workHours.eligibilityEditor.exclude'),
      // W4c added the `lead` manager to the shared roster: mode 'all' with support_bot
      // toggled off the exclude list makes EVERY roster agent eligible (was 2, now 3).
      tr(locale, 'workHours.eligibilityEditor.liveResult', { count: ROSTER.length, n: ROSTER.length }),
      tr(locale, 'workHours.dialog.reviewImpact'),
    ],
    shot: 'zh-eligibility-editor-switch-1440',
  },
  {
    // AddAgentDialog (Agents page "New agent"): the agent-name input.
    id: 'add-agent-dialog', path: `/orgs/${ORG}/agents`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('support_bot'), 'agents roster');
      await h.clickSrc(page, `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(tr('en', 'agents.page.newAgent'))})`);
      await h.waitTrue(page, `Boolean(document.getElementById('agent-name'))`, 'add agent dialog');
      await sleep(400);
    },
    control: `document.getElementById('agent-name')`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    draft: 'new_bot',
    copy: (locale) => [tr(locale, 'agents.add.title'), tr(locale, 'agents.add.name'), tr(locale, 'agents.add.team'), tr(locale, 'agents.add.role'), tr(locale, 'agents.add.create')],
    shot: 'zh-add-agent-dialog-switch-1440',
  },
  {
    // Team escalation policy draft: the "What to escalate" textarea, cleared via the
    // React-compatible native setter so the typed draft equals the control value exactly.
    id: 'agent-policy-draft', path: `/orgs/${ORG}/agents/lead/team-escalation-policy`,
    open: async (page, h) => {
      await h.waitTrue(page, `Boolean(document.querySelector('[data-testid="team-escalation-policy"] textarea'))`, 'policy editor');
      await h.evaluate(page, `(() => {
        const i = document.querySelector('[data-testid="team-escalation-policy"] textarea');
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(i, '');
        i.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      })()`);
    },
    control: `document.querySelector('[data-testid="team-escalation-policy"] textarea')`,
    container: `(CONTROL || { closest: () => null }).closest('[data-testid="team-escalation-policy"]')`,
    draft: 'Escalate draft «raw» change.',
    copy: (locale) => [tr(locale, 'agents.policy.title'), tr(locale, 'agents.policy.whatTo'), tr(locale, 'agents.policy.whatNot'), tr(locale, 'agents.policy.save')],
    shot: 'zh-agent-policy-draft-switch-1440',
  },
];

const selectedSlice = arg('slice', 'all');
if (!['all', 'kb-artifacts'].includes(selectedSlice)) throw new Error('unknown --slice');
const ACTIVE_VIEWS = selectedSlice === 'all' ? VIEW_ROUTES : VIEW_ROUTES.filter(row => row.id.startsWith('kb-') || row.id.startsWith('artifacts-'));
const ACTIVE_SWITCHES = selectedSlice === 'all' ? SWITCH_ROUTES : SWITCH_ROUTES.filter(row => row.id.startsWith('artifacts-'));

/** Resolve a VIEW_ROUTES key spec; a param value '@key' is itself translated. */
function expected(locale, spec) {
  const [key, params = {}] = Array.isArray(spec) ? spec : [spec];
  const resolved = Object.fromEntries(Object.entries(params).map(([k, v]) => [k, typeof v === 'string' && v.startsWith('@') ? tr(locale, v.slice(1)) : v]));
  return { key, text: tr(locale, key, resolved) };
}

async function main() {
  if (!dist || !existsSync(join(dist, 'index.html'))) throw new Error('missing --dist <dir> (ordinary build)');
  mkdirSync(outDir, { recursive: true });
  const cases = [];
  const screenshots = [];
  let current = null;
  const beginCase = (id, title) => { current = { id, title, checks: [], pass: false }; cases.push(current); console.log(`\n=== ${id}: ${title}`); };
  const endCase = () => { current.pass = current.checks.length > 0 && current.checks.every((c) => c.ok); console.log(`--- ${current.id} ${current.pass ? 'PASS' : 'FAIL'}`); };
  function check(name, actual, expectedValue) {
    const ok = JSON.stringify(actual) === JSON.stringify(expectedValue);
    current.checks.push({ name, actual, expected: expectedValue, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expectedValue)}`}`);
  }

  const joined = distJs(dist).map((f) => readFileSync(f, 'utf8')).join('\n');
  const bundleKeys = [...new Set(ACTIVE_VIEWS.flatMap((row) => row.keys.map((spec) => (Array.isArray(spec) ? spec[0] : spec))))];
  // Template text before the first placeholder is what a minifier keeps verbatim.
  const literal = (key) => { const v = CATALOG['zh-CN'][key]; return (typeof v === 'string' ? v : v.other).split('{')[0].trim(); };
  const fingerprint = {
    indexHtmlSha256: sha256(readFileSync(join(dist, 'index.html'))),
    zhCatalogLiterals: Object.fromEntries(bundleKeys.map((key) => [key, literal(key) === '' || joined.includes(JSON.stringify(literal(key)).slice(1, -1)) || joined.includes(literal(key))])),
  };
  const { server, url: base } = await startServer(dist);
  const userDataDir = `/tmp/w4a-${process.pid}`; // short: Chrome's singleton socket path limit
  rmSync(userDataDir, { recursive: true, force: true });
  mkdirSync(userDataDir, { recursive: true });
  let chrome;
  let cdp;
  let chromeVersion = null;
  try {
    chrome = spawn(chromeBin, [
      '--headless=new', '--lang=zh-CN', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
      '--no-sandbox', '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-dev-shm-usage',
      '--disable-extensions', '--disable-background-networking', '--hide-scrollbars', '--disable-crash-reporter',
      '--disable-background-timer-throttling', '--disable-renderer-backgrounding', 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'ignore'], env: { ...process.env, HOME: userDataDir, TMPDIR: userDataDir } });
    const devtools = await waitForDevTools(userDataDir);
    chromeVersion = (await (await fetch(`http://127.0.0.1:${devtools.port}/json/version`)).json()).Browser;
    cdp = new CDP(`ws://127.0.0.1:${devtools.port}${devtools.path}`);
    await cdp.ready;

    async function openPage(url, { init = '', width = 1440, height = 900 } = {}) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      for (const domain of ['Page', 'Runtime', 'Network']) await cdp.send(`${domain}.enable`, {}, sessionId);
      // Offline isolation: every non-loopback request (the external webfont
      // CSS/woff2) is refused, so a hung third-party fetch can never stall the
      // load event. Fonts fall back to local faces; recorded in the receipt.
      await cdp.send('Network.setBlockedURLs', { urls: EXTERNAL_BLOCK }, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
      if (init) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: init }, sessionId);
      const loaded = cdp.waitFor('Page.loadEventFired', { sessionId });
      await cdp.send('Page.navigate', { url }, sessionId);
      await loaded;
      return { targetId, sessionId };
    }
    const closePage = async (page) => { try { await cdp.send('Target.closeTarget', { targetId: page.targetId }); } catch { /* closed */ } };
    async function evaluate(page, expression) {
      const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, page.sessionId);
      if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.exception?.description || result.exceptionDetails.text}`);
      return result.result.value;
    }
    async function waitTrue(page, expression, label, timeout = 15000) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        if ((await evaluate(page, expression).catch(() => false)) === true) return true;
        await sleep(100);
      }
      console.log(`  (timeout waiting for ${label})`);
      return false;
    }
    async function clickSrc(page, src) {
      const box = await evaluate(page, `(() => { const el = (${src}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!box) throw new Error(`clickSrc: not found ${src.slice(0, 120)}`);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
        await cdp.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
      }
    }
    async function capture(page, name, meta) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      writeFileSync(join(outDir, `${name}.png`), buffer);
      screenshots.push({ name, file: `${name}.png`, sha256: sha256(buffer), bytes: buffer.length, ...meta });
    }
    /** Second same-origin tab writes the preference -> `storage` event in `page`. */
    async function crossTabSwitch(page, locale) {
      const other = await openPage(`${base}/__w4a_blank`);
      await evaluate(other, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); return true; })()`);
      await closePage(other);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await waitTrue(page, langIs(locale), `storage-event switch to ${locale}`, 5000);
      await sleep(600); // quiescence: any switch-caused request lands in the ledger
    }
    const h = { clickSrc, waitTrue, evaluate };

    // ============================================================ G
    beginCase('G', 'ordinary bundle (no build flag) carries the zh-CN catalog copy of every V route');
    for (const [key, present] of Object.entries(fingerprint.zhCatalogLiterals)) check(`G ordinary JS contains zh-CN ${key}`, present, true);
    endCase();

    // ============================================================ V
    beginCase('V', `${ACTIVE_VIEWS.map((r) => r.id).join(' + ')}, en/zh-CN, 1440x900 + 390x844`);
    for (const locale of ['en', 'zh-CN']) {
      const short = locale === 'en' ? 'en' : 'zh';
      for (const [w, ht] of [[1440, 900], [390, 844]]) {
        for (const row of ACTIVE_VIEWS) {
          const page = await openPage(`${base}${row.path}`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: ht });
          await waitTrue(page, row.ready(locale), `${row.id} content`);
          if (row.prep) await row.prep(page, h);
          await sleep(400);
          check(`V ${row.id} ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const spec of row.keys) {
            const { key, text } = expected(locale, spec);
            check(`V ${row.id} ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(text)), true);
          }
          for (const value of row.verbatim) check(`V ${row.id} ${locale} ${w} verbatim ${value}`, await evaluate(page, bodyHas(value)), true);
          check(`V ${row.id} ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          if (row.contained) check(`V ${row.id} ${locale} ${w} cards contain their rows`, await evaluate(page, row.contained()), { cards: true, over: [] });
          for (const [label, expression, result] of row.checks?.(locale, w) ?? []) {
            check(`V ${row.id} ${locale} ${w} ${label}`, await evaluate(page, expression), result);
          }
          await capture(page, `${short}-${row.id}-${w}`, { viewport: `${w}x${ht}`, locale, route: row.route });
          await closePage(page);
        }
      }
    }
    endCase();

    // ============================================================ S
    beginCase('S', `${ACTIVE_SWITCHES.map((r) => r.id).join(' + ')}: en -> zh-CN -> en keeps nodes, focus (and draft), zero /api requests`);
    for (const row of ACTIVE_SWITCHES) {
      const page = await openPage(`${base}${row.path}`, { init: `${seedLocale('en')}\n${CHINESE_NAVIGATOR}` });
      await row.open(page, h);
      await sleep(400);
      const CONTROL = `(${row.control})`;
      const CONTAINER = row.container.replace('CONTROL', CONTROL);
      await waitTrue(page, `Boolean(${CONTROL})`, `${row.id} control`);
      await evaluate(page, `(() => { ${CONTROL}.focus(); return true; })()`);
      if (row.draft) { await cdp.send('Input.insertText', { text: row.draft }, page.sessionId); await sleep(200); }
      await evaluate(page, `(() => { window.__w4aBox = new WeakRef(${CONTAINER}); window.__w4aCtl = new WeakRef(${CONTROL}); return true; })()`);
      const state = `(() => {
        const box = window.__w4aBox.deref(); const ctl = window.__w4aCtl.deref();
        return {
          sameContainer: Boolean(box && box === ${CONTAINER} && box.isConnected && box.contains(ctl)),
          sameControl: Boolean(ctl && ctl === ${CONTROL} && ctl.isConnected),
          value: ctl && 'value' in ctl && ctl.tagName !== 'BUTTON' ? ctl.value : null,
          focused: document.activeElement === ctl, lang: document.documentElement.lang,
        };
      })()`;
      check(`S ${row.id} before switch: focus`, await evaluate(page, `document.activeElement === window.__w4aCtl.deref()`), true);
      for (const locale of ['zh-CN', 'en']) {
        const from = LEDGER.length;
        await crossTabSwitch(page, locale);
        const s = await evaluate(page, state);
        check(`S ${row.id} -> ${locale} same container node`, s.sameContainer, true);
        check(`S ${row.id} -> ${locale} same control node`, s.sameControl, true);
        if (row.draft) check(`S ${row.id} -> ${locale} draft value kept`, s.value, row.draft);
        for (const [label, expression, result] of row.checks?.(locale) ?? []) check(`S ${row.id} -> ${locale} ${label}`, await evaluate(page, expression), result);
        check(`S ${row.id} -> ${locale} focus kept`, s.focused, true);
        for (const text of row.copy(locale)) check(`S ${row.id} -> ${locale} shows "${text}"`, await evaluate(page, bodyHas(text)), true);
        check(`S ${row.id} -> ${locale} <html lang>`, s.lang, locale);
        check(`S ${row.id} -> ${locale} zero /api requests in switch window`, LEDGER.slice(from), []);
        if (locale === 'zh-CN' && row.shot) await capture(page, row.shot, { viewport: '1440x900', locale, state: `${row.id} after en->zh-CN switch` });
      }
      await closePage(page);
    }
    endCase();
  } finally {
    for (const response of HUNG) { try { response.destroy(); } catch { /* gone */ } }
    if (cdp) cdp.close();
    if (chrome && !chrome.killed) chrome.kill('SIGKILL');
    server.closeAllConnections?.();
    server.close();
    rmSync(userDataDir, { recursive: true, force: true });
  }

  const failed = cases.filter((c) => !c.pass);
  const receipt = {
    head, generatedAt: new Date().toISOString(), node: process.version,
    chrome: { path: chromeBin, version: chromeVersion, lang: 'zh-CN (Chrome --lang + navigator override: environment never defaults the locale)' },
    externalRequestsBlocked: EXTERNAL_BLOCK,
    dist: fingerprint,
    routes: { view: ACTIVE_VIEWS.map((r) => r.id), switch: ACTIVE_SWITCHES.map((r) => r.id), api: Object.keys(API_ROUTES) },
    summary: cases.map((c) => ({ id: c.id, title: c.title, pass: c.pass, checks: c.checks.length, failedChecks: c.checks.filter((x) => !x.ok).map((x) => x.name) })),
    cases, screenshots, ledger: LEDGER, passed: cases.length - failed.length, failed: failed.length,
  };
  writeFileSync(join(outDir, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
  writeFileSync(join(outDir, 'SHA256SUMS'), screenshots.map((s) => `${s.sha256}  ${s.file}`).join('\n') + '\n');
  console.log(`\n${receipt.passed}/${cases.length} cases passed; ${screenshots.length} screenshots in ${outDir}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});

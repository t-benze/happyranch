#!/usr/bin/env node
/**
 * W4a browser-evidence harness (THR-118 W4a-1 health + dreams; W4b todos + work-hours + audit; W4c agents + skills; W4d KB/Artifacts + Usage; later W4 slices add rows).
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
import { runLocaleActivationCases } from './w5-locale-browser-cases.mjs';
import { runHeaderLanguageCases } from './header-language-browser-cases.mjs';
import { runWorkHoursCases, workHoursFixture } from './work-hours-browser-cases.mjs';
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
    this.ws.addEventListener('close', () => {
      for (const pending of this.pending.values()) pending.reject(new Error('CDP websocket closed'));
      this.pending.clear();
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
    const promise = new Promise((ok, fail) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        fail(new Error(`CDP command timed out: ${method} (${sessionId || 'browser'})`));
      }, 30000);
      this.pending.set(id, {
        resolve: value => { clearTimeout(timer); ok(value); },
        reject: error => { clearTimeout(timer); fail(error); },
      });
    });
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
let retirementSettingsGate = 'loaded';

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

// Focused shipping Pane fixture. A valid revision is required for actual editing.
const PROMPT_DRAFT = '# 指令 🐎\n\n正文  \n\n    code\n\n```text\n例子\n```\n';
const SAFEGUARD_ROSTER = ROSTER.map(agent => ({ ...agent, revision: 'a'.repeat(64) }));
let safeguardPrompt = { system_prompt: W4C_LEAD.system_prompt, revision: 'a'.repeat(64) };
let safeguardReadback = null;
const SAFEGUARD_TASKS = ['delegated', 'blocked_on_job'].map((block_kind, index) => ({
  task_id: `TASK-CASE-${index ? 'B' : 'A'}`, assigned_agent: 'lead', team: 'eng',
  brief: 'Raw task brief / 原文', status: 'in_progress', block_kind,
  created_at: iso(3600e3), updated_at: iso(5 * 60e3),
  revisit_of_task_id: 'TASK-CASE-P', direct_revisits: ['TASK-CASE-R'],
}));

/** pathname -> payload (or (search) => payload). Later W4 slices add rows here. */
const W4D_KB = { slug: 'raw-knowledge', title: 'Authored «raw» Knowledge', type: 'RAW_Type', topic: 'Raw_Topic', tags: ['Raw_Tag'], body: 'Authored «raw» KB body.', authored_by: 'Raw_Agent', source_task: 'TASK-0042', related_entries: [], updated_at: iso(3600e3) };
const W4D_ARTIFACTS = [
  { name: 'Raw_Agent-2026-06-16-THR-042-Raw_Title.pdf', size_bytes: 1536, modified_at: '2026-06-20T14:30:00Z' },
  { name: 'Raw_Folder/Raw_File.txt', size_bytes: 512, modified_at: '' },
];
// W4d-2 Usage response fixtures: windows are org wall-clock parts, not viewer instants.
const USAGE_META = {
  generated_at: '2026-09-29T06:03:00Z', data_through: '2026-09-29T06:03:00Z', timezone: 'Asia/Shanghai',
  current_window: { start_utc: '2026-09-22T06:03:00Z', end_utc: '2026-09-29T06:03:00Z', start_local: '2026-09-22T14:03:00+08:00', end_local: '2026-09-29T14:03:00+08:00' },
  previous_window: { start_utc: '2026-09-15T06:03:00Z', end_utc: '2026-09-22T06:03:00Z', start_local: '2026-09-15T14:03:00+08:00', end_local: '2026-09-22T14:03:00+08:00' },
};
const USAGE_WORKLOAD = { ...USAGE_META, agents: [{ agent: 'Raw_Agent',
  current: { task_runs: 12345, thread_wakes: 62, recorded_runtime: { seconds: 15000, known: 71, total: 72 }, deliveries: 2, delivery_unclassified_results: 1, replies: 22, reply_outcome_coverage: { recorded: 22, total_consumed: 24 } },
  previous: { task_runs: 12340, thread_wakes: 62, recorded_runtime: { seconds: 15000, known: 71, total: 72 }, deliveries: 0, delivery_unclassified_results: 0, replies: 22, reply_outcome_coverage: { recorded: 22, total_consumed: 24 } },
  deltas: { task_runs: { kind: 'absolute', value: 5, withheld_reason: null }, thread_wakes: { kind: 'no_change', value: 0, withheld_reason: null }, deliveries: { kind: 'new_from_zero', value: 2, withheld_reason: null }, replies: { kind: 'withheld', value: null, withheld_reason: 'reply_outcome_not_recorded' } },
}] };
const USAGE_COHORTS = [null, 'Raw_Model', 'Other_Model'].map(model => ({ executor: 'Raw_CLI', model, model_unpinned: model === null, current_runs: 20, previous_runs: 20 }));
const USAGE_UNATTRIBUTED = { worker_task: 0, manager_decision: 0, thread_reply: 0, thread_followup: 0, dream: 0, task_unclassified: 0, recovery: 1 };
const USAGE_ROWS = ['worker_task', 'manager_decision', 'thread_reply', 'thread_followup', 'dream'].map(run_type => {
  const period = { runs: 20, usage_coverage: { known: 20, total: 20, ratio: 1 }, fresh_input: { value: 12000, n_reported: 20, partial_count: 0 }, reread: { value: 54000, n_reported: 20, partial_count: 0 }, output: { value: 4100, n_reported: 20, partial_count: 0 }, decline_waste: run_type.startsWith('thread_') ? { state: 'no_declines', declined: 0, total: 20, rate: 0, usage_known: 0, fresh_input: { value: null, n_reported: 0 }, reread: { value: null, n_reported: 0 }, output: { value: null, n_reported: 0 } } : null };
  return { run_type, current: period, previous: period, deltas: { runs: { kind: 'absolute', value: 0, withheld_reason: null }, fresh_input: { kind: 'percent', value: -0.4, withheld_reason: null } } };
});
const USAGE_EFFICIENCY = search => ({ ...USAGE_META, cohorts: USAGE_COHORTS, rows: new URLSearchParams(search).has('executor') ? USAGE_ROWS : [], unattributed: { current: USAGE_UNATTRIBUTED, previous: USAGE_UNATTRIBUTED } });
const USAGE_REFRESH_FAILED = new Set();

const API_ROUTES = {
  [`/api/v1/orgs/${ORG}/usage/workload`]: USAGE_WORKLOAD,
  [`/api/v1/orgs/${ORG}/usage/efficiency`]: USAGE_EFFICIENCY,
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
  [`/api/v1/orgs/${ORG}/settings/daemon-capacity`]: {
    running_at_daemon_start: { queue_workers: 6, host_global_session_cap: 13 },
    running_provenance: 'Resolved when the HappyRanch service started',
    persisted_yaml: { queue_workers: 6, host_global_session_cap: 13 },
    next_start: { queue_workers: 6, host_global_session_cap: 13 },
    environment_shadowed: [], environment_warning: null,
    producer_envelope: 13,
    producer_components: { task_workers: 6, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1 },
    effective_admission_cap: 13,
    effective_admission_reason: 'Startup-loaded host supervisor policy',
    warnings: [], revision: `sha256:${'a'.repeat(64)}`,
    restart_required: false, restart_pending: false,
    guidance: { queue_workers: 'Empirical worker guidance', host_global_session_cap: 'Empirical cap guidance', enforced: false },
    authorization: 'Local operator; daemon bearer required.',
  },
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
          // Usage-only scenario fixtures, selected by the evidence page URL.
          // These exercise ordinary shipping fetches; no app/bundle seam is added.
          const ref = new URL(request.headers.referer || 'http://127.0.0.1');
          if (selectedSlice === 'header-language' && p === `/api/v1/orgs/${ORG}/tasks/roots`) {
            response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
            response.end(JSON.stringify({ tasks: [], next_cursor: null })); return;
          }
          if (['agents-safeguard', 'header-language'].includes(selectedSlice)) {
            const state = ref.searchParams.get('agentsFixture') || 'populated';
            if (p === `/api/v1/orgs/${ORG}/agents/lead/system-prompt` && request.method === 'PUT') {
              const row = LEDGER.at(-1);
              let body = '';
              request.on('data', chunk => { body += chunk; });
              request.on('end', () => {
                row.body = JSON.parse(body);
                safeguardPrompt = { system_prompt: row.body.system_prompt, revision: 'b'.repeat(64) };
                response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
                response.end(JSON.stringify({ agent: 'lead', ...safeguardPrompt }));
              });
              return;
            }
            if (p === `/api/v1/orgs/${ORG}/agents`) {
              const payload = { agents: SAFEGUARD_ROSTER.map(agent => agent.name === 'lead' ? { ...agent, ...safeguardPrompt } : agent) };
              if (url.searchParams.has('_prompt_readback')) {
                LEDGER.at(-1).cacheControl = request.headers['cache-control'];
                safeguardReadback = () => {
                  response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
                  response.end(JSON.stringify(payload));
                  safeguardReadback = null;
                };
                HUNG.push(response); return;
              }
              response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
              response.end(JSON.stringify(payload)); return;
            }
            if (p === `/api/v1/orgs/${ORG}/tasks`) {
              if (state === 'loading') { HUNG.push(response); return; }
              response.writeHead(state === 'error' ? 500 : 200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
              response.end(JSON.stringify(state === 'error' ? { detail: 'Raw diagnostic' } : { tasks: state === 'empty' ? [] : SAFEGUARD_TASKS, next_cursor: null })); return;
            }
          }
          if (selectedSlice === 'assistant-retirement') {
            if (p.includes('/assistant')) {
              response.writeHead(404, { 'content-type': 'application/json' });
              response.end(JSON.stringify({ detail: 'retired synthetic fixture' })); return;
            }
            if (p === `/api/v1/orgs/${ORG}/tasks/roots`) {
              response.writeHead(200, { 'content-type': 'application/json' });
              response.end(JSON.stringify({ tasks: [], next_cursor: null })); return;
            }
            if (p === `/api/v1/orgs/${ORG}/settings`) {
              const gate = retirementSettingsGate;
              LEDGER.at(-1).settingsGate = gate;
              if (gate === 'loading') { HUNG.push(response); return; }
              if (gate === 'error' || gate === 'no-data') {
                response.writeHead(gate === 'error' ? 503 : 200, { 'content-type': 'application/json' });
                response.end(JSON.stringify(gate === 'error' ? { detail: 'retirement fixture error' } : null)); return;
              }
            }
          }
          if (selectedSlice === 'locale-activation' && p === '/api/v1/orgs' && ref.searchParams.has('localeStartup')) { HUNG.push(response); return; }
          if (selectedSlice === 'work-hours-reachability') {
            const fixture = workHoursFixture(p, ref, { org: ORG, settings: WH_SETTINGS, roster: ROSTER });
            if (fixture !== undefined) { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(fixture)); return; }
          }
          const mode = ref.searchParams.get('usageFixture');
          if (p.includes('/usage/') && mode === 'loading') { HUNG.push(response); return; }
          if (p.includes('/usage/') && (mode === 'error' || (mode === 'stale' && USAGE_REFRESH_FAILED.has(p + url.search + ref.search)))) {
            response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
            response.end(JSON.stringify({ detail: 'Raw diagnostic' })); return;
          }
          if (p.includes('/usage/') && mode === 'stale') USAGE_REFRESH_FAILED.add(p + url.search + ref.search);
          let payload = api(p, url.search);
          if (p.includes('/usage/') && mode === 'empty-zone') payload = { ...payload, timezone: '' };
          if (p.includes('/usage/') && mode === 'empty') payload = p.endsWith('/workload') ? { ...USAGE_META, agents: [] } : { ...USAGE_META, cohorts: [], rows: [], unattributed: { current: USAGE_UNATTRIBUTED, previous: null } };
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(payload));
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
const USAGE_BUTTON = label => `[...document.querySelectorAll('#usage-efficiency-heading ~ * button, section[aria-labelledby="usage-efficiency-heading"] button')].find(b => b.textContent.trim() === ${JSON.stringify(label)})`;
async function chooseUsage(page, h) {
  await h.waitTrue(page, bodyHas('Raw_CLI'), 'Usage cohort options');
  await h.clickSrc(page, USAGE_BUTTON('Raw_CLI'));
  // The default response inserts a table above/below the same controls. Finish
  // that layout change before measuring a named-model button for a real click.
  await h.waitTrue(page, `document.querySelectorAll('section table').length === 2`, 'default Efficiency response');
  await h.waitTrue(page, `Boolean(${USAGE_BUTTON('Raw_Model')})`, 'named model option');
  await h.clickSrc(page, USAGE_BUTTON('Raw_Model'));
  await h.waitTrue(page, `${USAGE_BUTTON('Raw_Model')}.getAttribute('aria-pressed') === 'true' && document.querySelectorAll('section table').length === 2`, 'selected named Efficiency response');
}
const usageGeometry = (locale, width) => [['labelled scroll regions, contained sections, sticky identity and narrow hint', `(() => {
  const sections = [...document.querySelectorAll('section[aria-labelledby^="usage-"]')];
  const regions = sections.flatMap(s => [...s.querySelectorAll('[role="region"]')]);
  const hints = regions.map(r => r.previousElementSibling);
  return { sections: sections.length, tables: document.querySelectorAll('section table').length, regions: regions.length,
    contained: sections.every(s => s.scrollWidth <= s.clientWidth + 1),
    labelled: regions.every(r => r.getAttribute('aria-label') === (r.closest('section').getAttribute('aria-labelledby').includes('workload') ? ${JSON.stringify(tr(locale, 'usage.scrollLabel', { label: tr(locale, 'usage.workloadTable') }))} : ${JSON.stringify(tr(locale, 'usage.scrollLabel', { label: tr(locale, 'usage.efficiencyTable') }))}) && r.tabIndex === 0),
    sticky: regions.every(r => [...r.querySelectorAll('th:first-child')].every(th => getComputedStyle(th).position === 'sticky')),
    hint: hints.every(p => p.textContent === ${JSON.stringify(tr(locale, 'usage.scrollHint'))} && (getComputedStyle(p).display !== 'none') === ${width < 768}),
    overflow: regions.every(r => r.scrollWidth > r.clientWidth) };
})()`, { sections: 2, tables: 2, regions: 2, contained: true, labelled: true, sticky: true, hint: true, overflow: width === 390 }]];

const VIEW_ROUTES = [
  {
    id: 'usage-unselected', route: 'usage', path: `/orgs/${ORG}/usage`, ready: () => bodyHas('Raw_Agent'),
    keys: ['usage.chooseCohort', 'usage.chooseCliFirst', 'usage.workload', 'usage.efficiency'], verbatim: ['Raw_Agent', 'Raw_CLI', 'Asia/Shanghai'],
    checks: () => [['first load retains no selected CLI/model and no Efficiency figures', `document.querySelectorAll('section table').length === 1 && document.querySelector('section[aria-labelledby="usage-efficiency-heading"]').querySelectorAll('button[aria-pressed="true"]').length === 0`, true]],
  },
  {
    id: 'usage-populated', route: 'usage', path: `/orgs/${ORG}/usage`, ready: () => bodyHas('Raw_Agent'), prep: chooseUsage,
    keys: ['usage.title', 'usage.compare', 'usage.workload', 'usage.efficiency', 'usage.workloadQuestion', 'usage.efficiencyQuestion', 'usage.agent', 'usage.taskRuns', 'usage.threadWakes', 'usage.runtime', 'usage.deliveries', 'usage.replies', 'usage.runType', 'usage.runs', 'usage.freshMedian', 'usage.rereadMedian', 'usage.outputMedian', 'usage.declineWaste', 'usage.workerTask', 'usage.managerDecision', 'usage.threadReply', 'usage.threadFollowup', 'usage.dream', 'usage.unpinned', 'usage.runtimeFootnote', 'usage.deliveryFootnote', 'usage.countFootnote', 'usage.freshFootnote', 'usage.rereadFootnote', 'usage.outputFootnote', 'usage.declineFootnote', 'usage.medianFootnote', 'usage.usageMissingFootnote'],
    verbatim: ['Raw_Agent', 'Raw_CLI', 'Raw_Model', 'Asia/Shanghai', '12,345'], checks: usageGeometry,
  },
  {
    id: 'usage-empty', route: 'usage', path: `/orgs/${ORG}/usage?usageFixture=empty`, ready: locale => bodyHas(tr(locale, 'usage.workloadEmpty')),
    keys: ['usage.workloadEmpty', 'usage.efficiencyEmpty', 'usage.taskRunsDefinition', 'usage.threadWakesDefinition', 'usage.runtimeDefinition', 'usage.deliveriesDefinition', 'usage.repliesDefinition'], verbatim: ['Asia/Shanghai'],
    checks: locale => [['localized Workload definitions list accessible name', `document.querySelector('section[aria-labelledby="usage-workload-heading"] ul').getAttribute('aria-label')`, tr(locale, 'usage.workloadDefinitions')]],
  },
  {
    id: 'usage-loading', route: 'usage', path: `/orgs/${ORG}/usage?usageFixture=loading`, ready: () => `document.querySelectorAll('[data-testid="usage-skeleton"]').length === 2`,
    keys: ['usage.workloadLoading', 'usage.efficiencyOptionsLoading', 'usage.taskRuns', 'usage.freshMedian'], verbatim: [],
    checks: () => [['two loading sections remain busy without figures', `document.querySelectorAll('[aria-busy="true"]').length`, 2]],
  },
  {
    id: 'usage-error', route: 'usage', path: `/orgs/${ORG}/usage?usageFixture=error`, ready: locale => bodyHas(tr(locale, 'usage.loadError', { view: tr(locale, 'usage.efficiency') })),
    keys: [['usage.loadError', { view: '@usage.workload' }], ['usage.loadError', { view: '@usage.efficiency' }], 'usage.retry', 'usage.workloadFailed', 'usage.efficiencyOptionsFailed'], verbatim: [],
    checks: locale => [['independent retry controls and Compare survive', `(() => { const sections = [...document.querySelectorAll('section[aria-labelledby^="usage-"]')]; return sections.length === 2 && sections.every(s => { const buttons = [...s.querySelectorAll('button')]; return buttons.length === 1 && buttons[0].textContent.trim() === ${JSON.stringify(tr(locale, 'usage.retry'))}; }) && Boolean(document.querySelector('[role="switch"]')); })()`, true]],
  },
  {
    id: 'usage-stale', route: 'usage', path: `/orgs/${ORG}/usage?usageFixture=stale`, ready: () => bodyHas('Raw_Agent'),
    prep: async (page, h) => {
      await chooseUsage(page, h);
      await h.clickSrc(page, `document.querySelector('[role="switch"]')`);
      await h.waitTrue(page, bodyHas('−0.4%'), 'comparison');
      await sleep(31000);
      await h.clickSrc(page, `document.querySelector('[role="switch"]')`);
      const stale = tr(await h.evaluate(page, 'document.documentElement.lang'), 'usage.stale');
      await h.waitTrue(page, `(() => { const sections = [...document.querySelectorAll('section[aria-labelledby^="usage-"]')]; return sections.length === 2 && sections.every(s => s.textContent.includes(${JSON.stringify(stale)})); })()`, 'both stale Usage responses');
    },
    keys: ['usage.stale', 'usage.retry'], verbatim: ['Raw_Agent', 'Raw_Model', 'Asia/Shanghai'],
    checks: (locale) => [['stale data retains original timestamp and cohort', `(() => { const sections = [...document.querySelectorAll('section[aria-labelledby^="usage-"]')]; return sections.length === 2 && sections.every(s => s.textContent.includes(${JSON.stringify(tr(locale, 'usage.dataThrough', { stamp: locale === 'en' ? 'Sep 29, 14:03 (Asia/Shanghai)' : '9月29日 14:03 (Asia/Shanghai)' }))})) && ${USAGE_BUTTON('Raw_Model')}.getAttribute('aria-pressed') === 'true'; })()`, true]],
  },
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
    // Bind the
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
    id: 'usage-cohort-compare', path: `/orgs/${ORG}/usage`,
    open: async (page, h) => { await chooseUsage(page, h); await h.clickSrc(page, `document.querySelector('[role="switch"]')`); await h.waitTrue(page, bodyHas('−0.4%'), 'comparison response'); await h.evaluate(page, `window.__usageCompare = document.querySelector('[role="switch"]'); window.__usageTable = document.querySelectorAll('section table')[1]; true`); },
    control: USAGE_BUTTON('Raw_Model'), container: `CONTROL.closest('section')`,
    copy: locale => [tr(locale, 'usage.efficiencyLoaded', { executor: 'Raw_CLI', model: 'Raw_Model' }), tr(locale, 'usage.unpinned'), tr(locale, 'usage.noChange'), tr(locale, 'usage.withheldReply').trim(), locale === 'en' ? '12.0K' : '1.2万', locale === 'en' ? 'Sep 29, 14:03' : '9月29日 14:03'],
    checks: () => [['Compare, cohort, table and route kept', `window.__usageCompare === document.querySelector('[role="switch"]') && window.__usageCompare.getAttribute('aria-checked') === 'true' && window.__w4aCtl.deref().getAttribute('aria-pressed') === 'true' && window.__usageTable === document.querySelectorAll('section table')[1] && location.pathname === '/orgs/${ORG}/usage'`, true], ['exact token tooltip kept', `window.__usageTable.querySelector('[title="12,000"]') !== null`, true]],
    // Normal action after both measured switches: change the model and observe its real request.
    after: async (page, h) => { await h.clickSrc(page, USAGE_BUTTON('Other_Model')); await h.waitTrue(page, `${USAGE_BUTTON('Other_Model')}.getAttribute('aria-pressed') === 'true'`, 'normal action'); },
    shot: 'zh-usage-cohort-compare-switch-1440',
  },
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
if (!['all', 'kb-artifacts', 'usage', 'usage-fallback', 'work-hours-reachability', 'locale-activation', 'agents-safeguard', 'header-language', 'assistant-retirement'].includes(selectedSlice)) throw new Error('unknown --slice');
// F1 repair: reuse the real populated and switch cases with only malformed
// response metadata. Keep the independent raw-string oracle out of formatters.
const emptyZoneChecks = () => [['both Usage sections retain raw Data-through and Generated timestamps', `(() => { const sections = [...document.querySelectorAll('section[aria-labelledby^="usage-"]')]; return sections.length === 2 && sections.every(s => s.textContent.split('2026-09-29T06:03:00Z').length - 1 === 2); })()`, true]];
const populatedUsage = VIEW_ROUTES.find(row => row.id === 'usage-populated');
const switchUsage = SWITCH_ROUTES.find(row => row.id === 'usage-cohort-compare');
const EMPTY_ZONE_VIEW = {
  ...populatedUsage, id: 'usage-empty-zone', path: `/orgs/${ORG}/usage?usageFixture=empty-zone`,
  verbatim: populatedUsage.verbatim.filter(value => value !== 'Asia/Shanghai').concat('2026-09-29T06:03:00Z'),
  checks: (locale, width) => [...populatedUsage.checks(locale, width), ...emptyZoneChecks()],
};
const EMPTY_ZONE_SWITCH = {
  ...switchUsage, id: 'usage-empty-zone-switch', path: EMPTY_ZONE_VIEW.path,
  copy: locale => switchUsage.copy(locale).map(value => value === (locale === 'en' ? 'Sep 29, 14:03' : '9月29日 14:03') ? '2026-09-29T06:03:00Z' : value),
  checks: locale => [...switchUsage.checks(locale), ...emptyZoneChecks()],
  shot: 'zh-usage-empty-zone-switch-1440',
};
const ACTIVE_VIEWS = ['work-hours-reachability', 'locale-activation', 'header-language', 'assistant-retirement'].includes(selectedSlice) ? [] : selectedSlice === 'usage-fallback' ? [EMPTY_ZONE_VIEW] : selectedSlice === 'all' ? VIEW_ROUTES : VIEW_ROUTES.filter(row => selectedSlice === 'usage' ? row.id.startsWith('usage-') : row.id.startsWith('kb-') || row.id.startsWith('artifacts-'));
const ACTIVE_SWITCHES = ['work-hours-reachability', 'locale-activation', 'header-language', 'assistant-retirement'].includes(selectedSlice) ? [] : selectedSlice === 'usage-fallback' ? [EMPTY_ZONE_SWITCH] : selectedSlice === 'all' ? SWITCH_ROUTES : SWITCH_ROUTES.filter(row => row.id.startsWith(selectedSlice === 'usage' ? 'usage-' : 'artifacts-'));

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
    // Keep singleton paths short while allocating
    // anonymous font mappings on an available task-owned temp filesystem.
    chrome = spawn(chromeBin, [
      '--headless=new', '--lang=zh-CN', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
      '--no-sandbox', '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-dev-shm-usage',
      '--disable-extensions', '--disable-background-networking', '--hide-scrollbars', '--disable-crash-reporter',
      '--disable-background-timer-throttling', '--disable-renderer-backgrounding', 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'ignore'], env: { ...process.env, TMPDIR: arg('chrome-temp', userDataDir) } });
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
      let initializationScript;
      if (init) initializationScript = (await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: init }, sessionId)).identifier;
      const loaded = cdp.waitFor('Page.loadEventFired', { sessionId });
      await cdp.send('Page.navigate', { url }, sessionId);
      await loaded;
      return { targetId, sessionId, initializationScript };
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
      await cdp.send('Page.bringToFront', {}, page.sessionId);
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

    if (selectedSlice === 'agents-safeguard') {
      // Observe all HTTP methods and transport activity, including sockets whose
      // messages would not appear in the synthetic HTTP ledger.
      const transport = [];
      for (const name of ['webSocketCreated', 'webSocketClosed', 'webSocketFrameSent', 'webSocketFrameReceived', 'eventSourceMessageReceived']) {
        cdp.handlers.set(`Network.${name}`, [message => transport.push({ name, sessionId: message.sessionId, params: message.params })]);
      }
      const HTTP = [];
      cdp.handlers.set('Network.requestWillBeSent', [message => HTTP.push({ sessionId: message.sessionId, method: message.params.request.method, url: message.params.request.url, type: message.params.type })]);
      const EDIT = locale => `[...document.querySelectorAll('main button')].find(b => b.textContent.trim() === ${JSON.stringify(tr(locale, 'agents.prompt.edit'))})`;
      const TEXTAREA = `document.querySelector('main textarea')`;
      // Actual element/text bounds, clipping ancestors and hit testing after
      // scrolling the original control into view; screenshots remain human evidence.
      const reachable = expression => `(() => {
        const el = (${expression}); if (!el) return { found: false };
        el.scrollIntoView({ block: 'center', inline: 'nearest' });
        const r = el.getBoundingClientRect(), css = getComputedStyle(el);
        const clips = [];
        for (let p = el.parentElement; p; p = p.parentElement) {
          const s = getComputedStyle(p), b = p.getBoundingClientRect();
          if (/(hidden|clip|auto|scroll)/.test(s.overflowX) && (r.left < b.left - 1 || r.right > b.right + 1)) clips.push(p.tagName + ':x');
          if (/(hidden|clip|auto|scroll)/.test(s.overflowY) && (r.top < b.top - 1 || r.bottom > b.bottom + 1)) clips.push(p.tagName + ':y');
        }
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const text = document.createRange(); text.selectNodeContents(el);
        const textBounds = el.matches('button') || (el.matches('a') && !el.querySelector('div,p'))
          ? [...text.getClientRects()].every(b => b.left >= r.left - 1 && b.right <= r.right + 1 && b.left >= 0 && b.right <= innerWidth + 1) : true;
        return { found: true, bounds: r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth + 1 && r.top >= 0 && r.bottom <= innerHeight + 1,
          clips, textBounds, readable: Number.parseFloat(css.fontSize) >= 10 && css.visibility === 'visible' && css.opacity !== '0' && css.color !== 'rgba(0, 0, 0, 0)',
          reachable: hit === el || el.contains(hit) };
      })()`;
      const reachExpected = { found: true, bounds: true, clips: [], textBounds: true, readable: true, reachable: true };
      // Human evidence for the recent-task labels/state messages themselves:
      // every occurrence of each target text (its innermost element in <main>)
      // is scrolled into view and captured, never the Edit control. Sequential
      // shots continue until every occurrence has been visible in one; the set
      // measured immediately before AND after each shot (no scroll between)
      // is recorded with the page target id, so DOM and image agree.
      const targetVisibility = texts => `(() => {
        const main = document.querySelector('main'), out = [];
        ${JSON.stringify(texts)}.forEach((t, ti) => [...main.querySelectorAll('*')]
          .filter(e => e.textContent.includes(t) && ![...e.children].some(c => c.textContent.includes(t)))
          .forEach((e, oi) => {
            const r = e.getBoundingClientRect(), css = getComputedStyle(e);
            let visible = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1
              && css.visibility === 'visible' && css.opacity !== '0';
            for (let p = e.parentElement; visible && p; p = p.parentElement) {
              const s = getComputedStyle(p), b = p.getBoundingClientRect();
              if (/(hidden|clip|auto|scroll)/.test(s.overflowX) && (r.left < b.left - 1 || r.right > b.right + 1)) visible = false;
              if (/(hidden|clip|auto|scroll)/.test(s.overflowY) && (r.top < b.top - 1 || r.bottom > b.bottom + 1)) visible = false;
            }
            const hit = visible && document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            out.push({ key: ti + ':' + oi, text: t, visible: Boolean(hit) && (hit === e || e.contains(hit)) });
          }));
        return out;
      })()`;
      async function captureTargets(page, name, texts, meta) {
        const all = await evaluate(page, targetVisibility(texts));
        const pending = new Set(all.map(row => row.key));
        const missing = texts.filter((t, ti) => !all.some(row => row.key.startsWith(ti + ':')));
        const shots = [];
        while (pending.size && shots.length < all.length) {
          const [ti, oi] = [...pending][0].split(':').map(Number);
          await evaluate(page, `(() => { const t = ${JSON.stringify(texts)}[${ti}]; const e = [...document.querySelectorAll('main *')]
            .filter(e => e.textContent.includes(t) && ![...e.children].some(c => c.textContent.includes(t)))[${oi}]; e.scrollIntoView({ block: 'center', inline: 'nearest' }); return true; })()`);
          await sleep(150);
          const before = (await evaluate(page, targetVisibility(texts))).filter(row => row.visible).map(row => row.key);
          if (!before.some(key => pending.has(key))) break;
          const file = shots.length ? `${name}-${shots.length + 1}` : name;
          await capture(page, file, { ...meta, pageTarget: page.targetId, visibleTargets: before });
          const after = (await evaluate(page, targetVisibility(texts))).filter(row => row.visible).map(row => row.key);
          shots.push({ file, before, after });
          for (const key of before) pending.delete(key);
        }
        return { missing, uncaptured: [...pending], shotsStable: shots.every(s => JSON.stringify(s.before) === JSON.stringify(s.after)), shots: shots.length };
      }
      // Recent-task card header (shared TaskCard): the owned ID, status pill,
      // waiting qualifier and age must each read as ONE line (not a phrase
      // stacked glyph by glyph), and every header text line box must stay inside
      // its element, the card, the viewport and clipping ancestors on both axes.
      const headerLines = (id, owned) => `(() => {
        const owned = ${JSON.stringify(owned)};
        const link = document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-${id}"]');
        const header = link && link.firstElementChild, card = link && link.parentElement;
        if (!header) return { found: false, errors: [] };
        header.scrollIntoView({ block: 'center', inline: 'nearest' });
        const c = card.getBoundingClientRect(), errors = [], seen = [];
        const lineCount = el => { const range = document.createRange(); range.selectNodeContents(el);
          const boxes = [...range.getClientRects()].filter(b => b.width > 0).sort((x, y) => x.top - y.top);
          let lines = 0, bottom = -Infinity;
          for (const b of boxes) { if (b.top >= bottom - 1) { lines += 1; bottom = b.bottom; } else bottom = Math.max(bottom, b.bottom); }
          return { boxes, lines }; };
        for (const el of header.querySelectorAll('span:not([aria-hidden])')) {
          const r = el.getBoundingClientRect(), { boxes, lines } = lineCount(el), name = JSON.stringify(el.textContent);
          const out = b => b.left < c.left - 1 || b.right > c.right + 1 || b.top < c.top - 1 || b.bottom > c.bottom + 1 || b.left < 0 || b.top < 0 || b.right > innerWidth + 1 || b.bottom > innerHeight + 1;
          if (!r.width || !r.height || out(r)) errors.push(name + ' element outside card/viewport');
          if (boxes.some(b => out(b) || b.left < r.left - 1 || b.right > r.right + 1 || b.top < r.top - 1 || b.bottom > r.bottom + 1)) errors.push(name + ' text line box outside element/card/viewport');
          for (let p = el.parentElement; p; p = p.parentElement) {
            const s = getComputedStyle(p), b = p.getBoundingClientRect();
            if (/(hidden|clip|auto|scroll)/.test(s.overflowX) && (r.left < b.left - 1 || r.right > b.right + 1)) errors.push(name + ' x-clipped by ' + p.tagName);
            if (/(hidden|clip|auto|scroll)/.test(s.overflowY) && (r.top < b.top - 1 || r.bottom > b.bottom + 1)) errors.push(name + ' y-clipped by ' + p.tagName);
          }
          const label = owned.find(t => el.textContent.trim() === t);
          const pill = owned.some(t => t.startsWith('· ') && [...el.children].some(k => k.textContent.trim() === t));
          if (label || pill) { seen.push(label || 'status pill'); if (lines !== 1) errors.push(name + ' split across ' + lines + ' lines'); }
        }
        return { found: true, owned: new Set(seen).size, errors };
      })()`;
      const headerOwned = (locale, id) => [`TASK-CASE-${id}`, `· ${tr(locale, id === 'A' ? 'tasks.waiting.subtasks' : 'tasks.waiting.jobs')}`, tr(locale, 'tasks.age.minutes', { count: 5 })];
      const headerExpected = { found: true, owned: 4, errors: [] };
      if (arg('agents-case', 'all') !== 'switch') {
      beginCase('C9-V', 'C9 focused Agents populated/loading/empty/error en/zh-CN at 390/1440');
      for (const locale of ['en', 'zh-CN']) for (const [width, height] of [[390, 844], [1440, 900]]) {
        let comfortablePad = null;
        for (const state of ['populated', 'loading', 'empty', 'error']) {
          const page = await openPage(`${base}/orgs/${ORG}/agents/lead?agentsFixture=${state}`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}\nDate.now = () => ${now};`, width, height });
          if (!await waitTrue(page, `Boolean(${EDIT(locale)}) && !${EDIT(locale)}.disabled`, 'valid enabled Pane editor')) throw new Error('C9 fixture prerequisite: enabled editor absent');
          const stateCopy = state === 'populated' ? 'Raw task brief / 原文' : tr(locale, state === 'loading' ? 'agents.detail.loadingTasks' : 'agents.detail.noTasks');
          check(`C9 Agents ${state} ${locale} ${width} task state copy`, await waitTrue(page, bodyHas(stateCopy), 'recent task state'), true);
          check(`C9 Agents ${state} ${locale} ${width} html lang`, await evaluate(page, langIs(locale)), true);
          check(`C9 Agents ${state} ${locale} ${width} document bounds`, await evaluate(page, noOverflow), true);
          if (state === 'populated') {
            for (const text of [tr(locale, 'tasks.waiting.subtasks'), tr(locale, 'tasks.waiting.jobs'), tr(locale, 'tasks.age.minutes', { count: 5 }), tr(locale, 'tasks.row.supersedes', { id: 'TASK-CASE-P' }), tr(locale, 'tasks.row.supersededBy', { id: 'TASK-CASE-R' })]) {
              check(`C9 Agents ${locale} ${width} ${text}`, await evaluate(page, bodyHas(text)), true);
            }
            for (const id of ['A', 'B', 'P', 'R']) {
              const link = `document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-${id}"]')`;
              check(`C9 Agents ${locale} ${width} raw ${id} original link/control bounds`, await evaluate(page, reachable(link)), reachExpected);
              check(`C9 Agents ${locale} ${width} ${id} sibling lineage`, await evaluate(page, `Boolean(${link}) && !${link}.parentElement.closest('a')`), true);
            }
            comfortablePad = await evaluate(page, `Number.parseFloat(getComputedStyle(document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-A"]').parentElement).paddingLeft)`);
            for (const id of ['A', 'B']) check(`C9 Agents ${locale} ${width} ${id} recent-task header labels one line, inside card/viewport`, await evaluate(page, headerLines(id, headerOwned(locale, id))), headerExpected);
          }
          const targets = state === 'populated'
            ? [tr(locale, 'tasks.waiting.subtasks'), tr(locale, 'tasks.waiting.jobs'), tr(locale, 'tasks.age.minutes', { count: 5 }), tr(locale, 'tasks.row.supersedes', { id: 'TASK-CASE-P' }), tr(locale, 'tasks.row.supersededBy', { id: 'TASK-CASE-R' })]
            : [stateCopy];
          const captured = await captureTargets(page, `c9-${locale}-${state}-${width}`, targets, { locale, state, viewport: `${width}x${height}` });
          check(`C9 Agents ${state} ${locale} ${width} recent-task targets visibly captured`, { missing: captured.missing, uncaptured: captured.uncaptured, stable: captured.shotsStable, shot: captured.shots > 0 }, { missing: [], uncaptured: [], stable: true, shot: true });
          check(`C9 Agents ${state} ${locale} ${width} Edit readable/reachable`, await evaluate(page, reachable(EDIT(locale))), reachExpected);
          if (state === 'populated') {
            await clickSrc(page, `document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-A"]')`);
            check(`C9 Agents ${locale} ${width} original task navigation action`, await waitTrue(page, `location.pathname === '/orgs/${ORG}/tasks/TASK-CASE-A'`, 'original task navigation'), true);
          }
          await closePage(page);
        }
        // The same header at the user's compact density (narrower card padding).
        const compact = await openPage(`${base}/orgs/${ORG}/agents/lead?agentsFixture=populated`, { init: `${seedLocale(locale)}\ntry { localStorage.setItem('happyranch.density', 'compact'); } catch (e) {}\n${CHINESE_NAVIGATOR}\nDate.now = () => ${now};`, width, height });
        check(`C9 Agents compact ${locale} ${width} populated rows`, await waitTrue(compact, `Boolean(document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-B"]'))`, 'compact recent tasks'), true);
        const compactPad = await evaluate(compact, `Number.parseFloat(getComputedStyle(document.querySelector('main a[href="/orgs/${ORG}/tasks/TASK-CASE-A"]').parentElement).paddingLeft)`);
        check(`C9 Agents compact ${locale} ${width} compact card padding rendered`, { compactPad, narrower: compactPad < comfortablePad }, { compactPad, narrower: true });
        for (const id of ['A', 'B']) check(`C9 Agents compact ${locale} ${width} ${id} recent-task header labels one line, inside card/viewport`, await evaluate(compact, headerLines(id, headerOwned(locale, id))), headerExpected);
        const compactShots = await captureTargets(compact, `c9-${locale}-populated-compact-${width}`, headerOwned(locale, 'A').concat(headerOwned(locale, 'B').slice(0, 2)), { locale, state: 'populated', density: 'compact', viewport: `${width}x${height}` });
        check(`C9 Agents compact ${locale} ${width} header targets visibly captured`, { missing: compactShots.missing, uncaptured: compactShots.uncaptured, stable: compactShots.shotsStable, shot: compactShots.shots > 0 }, { missing: [], uncaptured: [], stable: true, shot: true });
        // Density persists per origin: restore the default before later comfortable pages.
        check(`C9 Agents compact ${locale} ${width} density preference restored`, await evaluate(compact, `(() => { localStorage.removeItem('happyranch.density'); return localStorage.getItem('happyranch.density'); })()`), null);
        await closePage(compact);
      }
      endCase();
      }
      beginCase('C9', 'C9 Agents SystemPromptEditor locale preservation');
      for (const [width, height] of [[390, 844], [1440, 900]]) {
        safeguardPrompt = { system_prompt: W4C_LEAD.system_prompt, revision: 'a'.repeat(64) };
        const page = await openPage(`${base}/orgs/${ORG}/agents/lead`, { init: `${seedLocale('en')}\n${CHINESE_NAVIGATOR}\nDate.now = () => ${now};`, width, height });
        if (!await waitTrue(page, `Boolean(${EDIT('en')}) && !${EDIT('en')}.disabled`, 'valid enabled pre-switch editor')) throw new Error('C9 fixture prerequisite: enabled editor absent');
        await clickSrc(page, EDIT('en'));
        if (!await waitTrue(page, `Boolean(${TEXTAREA}) && !${TEXTAREA}.disabled`, 'actual prompt editor')) throw new Error('C9 fixture prerequisite: editable prompt absent');
        await evaluate(page, `(() => { const el = ${TEXTAREA}; el.focus(); el.select(); return true; })()`);
        await cdp.send('Input.insertText', { text: PROMPT_DRAFT }, page.sessionId);
        await sleep(200);
        await evaluate(page, `(() => { const el = ${TEXTAREA}; el.setSelectionRange(3, 8); window.__c9Editor = el; window.__c9Section = el.closest('section'); return true; })()`);
        check(`C9 ${width} valid focused pre-switch draft/selection`, await evaluate(page, `(() => { const el = window.__c9Editor; return [el.value, el.disabled, document.activeElement === el, el.selectionStart, el.selectionEnd]; })()`), [PROMPT_DRAFT, false, true, 3, 8]);
        await sleep(600);
        for (const locale of ['zh-CN', 'en']) {
          const from = LEDGER.length, networkFrom = HTTP.length, transportFrom = transport.length;
          await crossTabSwitch(page, locale);
          // FIRST read is the retained DOM node; never look up/refocus/reopen it
          // before the causal remount assertion has observed its connectedness.
          const retained = await evaluate(page, `window.__c9Editor.isConnected && window.__c9Section.isConnected && window.__c9Section.contains(window.__c9Editor)`);
          check(`C9 ${width} -> ${locale} retained-DOM same-node/connected`, retained, true);
          if (!retained) break;
          check(`C9 ${width} -> ${locale} same actual editor node`, await evaluate(page, `window.__c9Editor === ${TEXTAREA}`), true);
          check(`C9 ${width} -> ${locale} draft/focus/selection`, await evaluate(page, `(() => { const el = window.__c9Editor; return [el.value, document.activeElement === el, el.selectionStart, el.selectionEnd]; })()`), [PROMPT_DRAFT, true, 3, 8]);
          check(`C9 ${width} -> ${locale} zero HTTP any method`, LEDGER.slice(from), []);
          check(`C9 ${width} -> ${locale} zero page HTTP any method`, HTTP.slice(networkFrom).filter(row => row.sessionId === page.sessionId), []);
          check(`C9 ${width} -> ${locale} zero WS/SSE restart/messages`, transport.slice(transportFrom).filter(row => row.sessionId === page.sessionId), []);
          check(`C9 ${width} -> ${locale} accessible prompt label`, await evaluate(page, `window.__c9Editor.getAttribute('aria-label')`), tr(locale, 'agents.field.systemPrompt'));
          for (const key of ['common.cancel', 'agents.prompt.save']) {
            const button = `[...window.__c9Section.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(tr(locale, key))})`;
            check(`C9 ${width} -> ${locale} ${key} readable/reachable`, await evaluate(page, reachable(button)), reachExpected);
          }
          check(`C9 ${width} -> ${locale} editor readable/reachable`, await evaluate(page, reachable('window.__c9Editor')), reachExpected);
          await capture(page, `c9-draft-${locale}-${width}`, { locale, viewport: `${width}x${height}`, state: 'authored multiline, selection 3:8' });
        }
        if (await evaluate(page, `window.__c9Editor.isConnected`).catch(() => false)) {
          for (const key of ['common.cancel', 'agents.prompt.save']) {
            await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 }, page.sessionId);
            await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 }, page.sessionId);
            check(`C9 ${width} original ${key} keyboard reachable`, await evaluate(page, `document.activeElement.textContent.trim()`), tr('en', key));
          }
          const before = LEDGER.length;
          await clickSrc(page, `[...window.__c9Section.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(tr('en', 'common.cancel'))})`);
          check(`C9 ${width} original Agents Cancel closes editor`, await waitTrue(page, `!${TEXTAREA}`, 'Cancel closes editor'), true);
          check(`C9 ${width} original Agents Cancel no PUT`, LEDGER.slice(before).filter(row => row.method === 'PUT'), []);
          // Original Agents action, distinct from an unrelated Usage GET.
          // Reuse the prompt owner's payload/readback contract; no mismatch matrix.
          await clickSrc(page, EDIT('en'));
          if (!await waitTrue(page, `Boolean(${TEXTAREA}) && !${TEXTAREA}.disabled`, 'fresh actual editor after Cancel')) throw new Error('C9 fresh editor prerequisite absent');
          await evaluate(page, `(() => { const el = ${TEXTAREA}; el.focus(); el.select(); return true; })()`);
          await cdp.send('Input.insertText', { text: PROMPT_DRAFT }, page.sessionId);
          await sleep(200);
          const actionFrom = LEDGER.length;
          await clickSrc(page, `[...${TEXTAREA}.closest('section').querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(tr('en', 'agents.prompt.save'))})`);
          if (!await waitTrue(page, bodyHas(tr('en', 'agents.prompt.saving')), 'original Save enters pending')) throw new Error('C9 original Save did not enter pending');
          const deadline = Date.now() + 5000;
          while (!safeguardReadback && Date.now() < deadline) await sleep(50);
          if (!safeguardReadback) throw new Error('C9 actual owned fresh GET not reached');
          check(`C9 ${width} original Save prompt-only PUT/frozen base`, LEDGER.slice(actionFrom).filter(row => row.method === 'PUT').map(row => ({ path: row.path, body: row.body })),
            [{ path: `/api/v1/orgs/${ORG}/agents/lead/system-prompt`, body: { system_prompt: PROMPT_DRAFT, expected_revision: 'a'.repeat(64) } }]);
          check(`C9 ${width} original Save fresh readback GET`, LEDGER.slice(actionFrom).filter(row => row.path === `/api/v1/orgs/${ORG}/agents` && row.method === 'GET').map(row => [new URLSearchParams(row.search).has('_prompt_readback'), row.cacheControl]), [[true, 'no-cache, no-store']]);
          check(`C9 ${width} Saved absent while matching GET held`, await evaluate(page, `!${bodyHas(tr('en', 'agents.prompt.saved'))}`), true);
          check(`C9 ${width} editor disabled through readback`, await evaluate(page, `${TEXTAREA}.disabled`), true);
          safeguardReadback();
          check(`C9 ${width} original Save completes after matching body/revision`, await waitTrue(page, bodyHas(tr('en', 'agents.prompt.saved')), 'matching GET releases Saved'), true);
          check(`C9 ${width} authored body after Save`, await evaluate(page, `[...document.querySelectorAll('main pre')].some(el => el.textContent === ${JSON.stringify(PROMPT_DRAFT)})`), true);
        }
        await closePage(page);
      }
      endCase();
    } else if (selectedSlice === 'assistant-retirement') {
      // Ordinary compiled SPA plus synthetic API only. The full ledger and
      // actual browser transport observations never prove backend route absence.
      const transport = [];
      for (const name of ['webSocketCreated', 'eventSourceMessageReceived']) {
        cdp.handlers.set(`Network.${name}`, [message => transport.push({ name, sessionId: message.sessionId, params: message.params })]);
      }
      const absent = `!document.querySelector('[data-assistant-open], [data-testid*="assistant"], [aria-label="Open assistant"], [aria-label="打开助手"]')`;
      const nav = `([...document.querySelectorAll('[data-testid="settings-content"] aside a')].map(a => a.getAttribute('href').split('/').at(-1)))`;
      const expectedNav = ['daemon-capacity', 'organization', 'executors', 'preferences'];
      const root = `/orgs/${ORG}/settings`;
      for (const locale of ['en', 'zh-CN']) for (const width of [390, 1440]) {
        retirementSettingsGate = 'loaded';
        const options = { init: seedLocale(locale), width, height: width === 390 ? 844 : 900 };
        for (const [section, heading] of [
          ['daemon-capacity', 'settings.capacity.title'], ['organization', 'settings.panel.organization.title'],
          ['executors', 'settings.panel.executors.title'], ['preferences', 'settings.panel.preferences.title'],
        ]) {
          beginCase(`retirement-${locale}-${width}-${section}`, 'ordinary Settings, shell controls, removed launchers and keyboard');
          const page = await openPage(`${base}${root}/${section}`, options);
          check('surviving Settings heading', await waitTrue(page, `([...document.querySelectorAll('main h2')].some(e => e.textContent === ${JSON.stringify(tr(locale, heading))}))`, 'surviving heading'), true);
          if (section === 'daemon-capacity') {
            check('loaded Capacity fields survive', await waitTrue(page, `document.getElementById('capacity-workers')?.value === '6' && document.getElementById('capacity-cap')?.value === '13'`, 'loaded Capacity'), true);
          }
          check('four surviving navigation links', await evaluate(page, nav), expectedNav);
          check('requested locale', await evaluate(page, 'document.documentElement.lang'), locale);
          check('no Assistant launcher or dock', await evaluate(page, absent), true);
          check('no Assistant settings link', await evaluate(page, `!document.querySelector('a[href$="/settings/assistant"]')`), true);
          check('ordinary Tasks navigation survives', await evaluate(page, `Boolean(document.querySelector('a[href="/orgs/${ORG}/tasks"]'))`), true);
          check('header language selector survives moved main', await evaluate(page, `Boolean(document.querySelector('[role="combobox"][aria-label=${JSON.stringify(tr(locale, 'common.language'))}]'))`), true);
          await capture(page, `retirement-${locale}-${width}-${section}`, { locale, viewport: `${width}x${options.height}`, scope: 'ordinary-dist/synthetic-api' });
          await cdp.send('Page.bringToFront', {}, page.sessionId);
          await evaluate(page, `(() => { window.__retirementKeys = []; document.addEventListener('keydown', e => { if (e.code === 'KeyK') queueMicrotask(() => window.__retirementKeys.push({trusted:e.isTrusted,prevented:e.defaultPrevented,ctrl:e.ctrlKey,meta:e.metaKey})); }); document.body.focus(); return true; })()`);
          const locationBefore = await evaluate(page, 'location.pathname');
          for (const modifiers of [2, 4]) {
            await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers }, page.sessionId);
            await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers }, page.sessionId);
          }
          await sleep(200);
          check('real Ctrl-K and Meta-K are unbound', await evaluate(page, 'window.__retirementKeys'), [
            { trusted: true, prevented: false, ctrl: true, meta: false },
            { trusted: true, prevented: false, ctrl: false, meta: true },
          ]);
          check('chords preserve route', await evaluate(page, 'location.pathname'), locationBefore);
          check('chords create no dock', await evaluate(page, absent), true);
          if (section === 'preferences') {
            check('Preferences language inputs survive', await evaluate(page, `document.querySelectorAll('input[name="happyranch-ui-language"]').length`), 2);
            const themeBefore = await evaluate(page, 'document.documentElement.dataset.theme');
            check('ordinary theme starts with a valid attribute', ['light', 'dark'].includes(themeBefore), true);
            const themeAfter = themeBefore === 'dark' ? 'light' : 'dark';
            await clickSrc(page, `document.querySelector('button[aria-label=${JSON.stringify(tr(locale, themeBefore === 'dark' ? 'shell.switchToLight' : 'shell.switchToDark'))}]')`);
            check('ordinary theme control operates', await waitTrue(page, `document.documentElement.dataset.theme === ${JSON.stringify(themeAfter)}`, 'theme change'), true);
            const next = locale === 'en' ? 'zh-CN' : 'en';
            await clickSrc(page, `document.querySelector('input[name="happyranch-ui-language"][value="${next}"]')`);
            check('Preferences changes client language', await waitTrue(page, langIs(next), 'Preferences language switch'), true);
          }
          await closePage(page); endCase();
        }
        for (const gate of ['loaded', 'loading', 'error', 'no-data']) for (const suffix of ['', '/assistant', '/unknown-retirement']) {
          beginCase(`retirement-fallback-${locale}-${width}-${gate}-${suffix || 'default'}`, 'default/legacy/unknown redirect outside data gate');
          retirementSettingsGate = gate;
          const gateFrom = LEDGER.length;
          const page = await openPage(`${base}${root}${suffix}?settingsFixture=${gate}`, options);
          check('Capacity redirect without waiting for Settings data', await waitTrue(page, `location.pathname === ${JSON.stringify(root + '/daemon-capacity')}`, 'Capacity redirect'), true);
          check('no Assistant controls after fallback', await evaluate(page, absent), true);
          const gateDeadline = Date.now() + 5000;
          while (!LEDGER.slice(gateFrom).some(row => row.path.endsWith('/settings') && row.settingsGate === gate) && Date.now() < gateDeadline) await sleep(50);
          check('requested Settings gate exercised', LEDGER.slice(gateFrom).some(row => row.path.endsWith('/settings') && row.settingsGate === gate), true);
          const gateReady = gate === 'loaded'
            ? `document.getElementById('capacity-workers')?.value === '6' && document.getElementById('capacity-cap')?.value === '13'`
            : gate === 'loading' ? bodyHas(tr(locale, 'settings.page.loading'))
            : gate === 'error' ? bodyHas(tr(locale, 'settings.page.loadError'))
            : `!document.querySelector('[data-testid="settings-content"]') && !(${bodyHas(tr(locale, 'settings.page.loading'))}) && !(${bodyHas(tr(locale, 'settings.page.loadError'))})`;
          check('requested Settings gate renders', await waitTrue(page, gateReady, `${gate} Settings gate`), true);
          await capture(page, `retirement-fallback-${locale}-${width}-${gate}-${suffix ? suffix.slice(1) : 'default'}`, { locale, viewport: `${width}x${options.height}`, gate, scope: 'ordinary-dist/synthetic-api' });
          await closePage(page); endCase();
        }
      }
      beginCase('retirement-network', 'no retired browser HTTP/socket activity across all cases; synthetic API only');
      check('zero retired HTTP requests', LEDGER.filter(row => /\/assistant(?:\/|$)/.test(row.path)), []);
      check('zero retired transport activity', transport.filter(row => JSON.stringify(row.params).includes('/assistant')), []);
      check('nonvacuous ordinary Settings HTTP', LEDGER.some(row => row.path.endsWith('/settings')), true);
      endCase();
    } else if (selectedSlice === 'header-language') {
      await runHeaderLanguageCases({ ...h, openPage, closePage, capture, check, beginCase, endCase, cdp, ledger: LEDGER, base, tr, seedLocale, chineseNavigator: CHINESE_NAVIGATOR, selectedCase: arg('header-case', 'all') });
    } else if (selectedSlice === 'locale-activation') {
      await runLocaleActivationCases({ ...h, openPage, closePage, capture, check, beginCase, endCase, cdp, ledger: LEDGER, base, tr, startupOnly: arg('locale-case', 'all') === 'startup', geometryOnly: arg('locale-case', 'all') === 'geometry' });
    } else if (selectedSlice === 'work-hours-reachability') {
      await runWorkHoursCases({ ...h, openPage, closePage, capture, check, beginCase, endCase, crossTabSwitch, cdp, ledger: LEDGER, base, tr, seedLocale, chineseNavigator: CHINESE_NAVIGATOR }, { org: ORG, geometryOnly: arg('work-hours-case', 'all') === 'geometry', entryOnly: arg('work-hours-case', 'all') === 'entry', longOnly: arg('work-hours-case', 'all') === 'long-cell', editorOnly: ['editor', 'editor-keyboard'].includes(arg('work-hours-case', 'all')), editorRed: arg('work-hours-case', 'all') === 'editor-red', editorKeyboard: arg('work-hours-case', 'all') === 'editor-keyboard' });
    } else {
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
          if (row.id === 'usage-stale') USAGE_REFRESH_FAILED.clear();
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
      if (row.after) {
        const from = LEDGER.length;
        await row.after(page, h); await sleep(400);
        check(`S ${row.id} ordinary user action executes after switches`, LEDGER.slice(from).some(r => r.path.endsWith('/usage/efficiency') && new URLSearchParams(r.search).get('model') === 'Other_Model'), true);
      }
      await closePage(page);
    }
    endCase();
    }
  } catch (error) {
    writeFileSync(join(outDir, 'incomplete-receipt.json'), JSON.stringify({ head, incomplete: true, error: String(error), cases, screenshots, ledger: LEDGER }, null, 2) + '\n');
    throw error;
  } finally {
    for (const response of HUNG) { try { response.destroy(); } catch { /* gone */ } }
    if (cdp) cdp.close();
    if (chrome && chrome.exitCode === null && chrome.signalCode === null) {
      // kill() only sends the signal; wait before removing a writable profile.
      await new Promise((resolve, reject) => {
        const deadline = setTimeout(() => reject(new Error('Chrome teardown timed out')), 5000);
        chrome.once('close', () => { clearTimeout(deadline); resolve(); });
        chrome.kill('SIGKILL');
      });
    }
    server.closeAllConnections?.();
    server.close();
    rmSync(userDataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }

  const failed = cases.filter((c) => !c.pass);
  const receipt = {
    head, generatedAt: new Date().toISOString(), node: process.version,
    chrome: { path: chromeBin, version: chromeVersion, lang: 'Chrome --lang=zh-CN; per-case actual navigator read-back in locale-activation receipt' },
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

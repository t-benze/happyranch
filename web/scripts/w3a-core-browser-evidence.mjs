#!/usr/bin/env node
/**
 * W3a Dashboard + Threads browser-evidence harness (THR-118).
 *
 * Drives ONE isolated headless Chrome over the DevTools Protocol (no new
 * dependency: Node 24's built-in WebSocket + the installed Chrome) against the
 * ORDINARY production SPA bundle served same-origin next to a synthetic
 * `/api/v1` stub whose every request is recorded in a server-side ledger. The
 * app carries NO evidence instrumentation: locale switches use the supported
 * `happyranch.ui.locale` preference written by a second same-origin tab (the
 * `storage` event path), because the Preferences selector stays gated closed.
 * Node/tag identity probes are injected by CDP at run time only.
 *
 * Expected copy is read from the real typed catalogs (Node 24 strips the TS
 * types), so every predicate compares against the shipped en/zh-CN strings.
 *
 *   --dist          ordinary shipping build (all W3a cases)
 *   --preview-dist  VITE_ENABLE_I18N_PREFERENCES=true build — ONLY the positive
 *                   control that the Preferences markers exist when the gate is
 *                   opened (the ordinary dist must lack them)
 *
 * Cases (receipt.json; exit 1 if any fails):
 *   G   gate: ordinary JS lacks Preferences/evidence markers (preview control has
 *       the Preferences markers); /settings/preferences redirects; an unset
 *       preference under a Chinese navigator renders English;
 *   D   Dashboard loading / error→Retry / first-run empty / populated in en and
 *       zh-CN; an en→zh-CN→en and zh-CN→en→zh-CN switch keeps tagged nodes,
 *       entity/ID and raw audit event_kind bytes and issues no mutation or
 *       settings request;
 *   L   Threads list loading / error→Retry / empty / filter-empty / populated
 *       (pinned section, selection) in both locales;
 *   T   thread detail: authored title, Markdown, names, IDs, filename, href
 *       byte-identical in zh-CN; no-messages; detail error;
 *   C   NewThreadDialog create journey in both directions: subject/recipient/
 *       body/attachment/focus/open state/node identity kept, ZERO /api
 *       requests in every switch window, then exactly one upload + one create;
 *   R   reply Composer journey in both directions: Markdown draft/attachment/
 *       focus/node identity kept, ZERO /api requests per window, then exactly
 *       one upload + one send;
 *   E   composer errors: a mapped daemon code re-translates in place without a
 *       resend; a raw `HTTP 500` diagnostic stays byte-exact;
 *   N   causal negatives: the SAME predicates fail for a remounted (cloned)
 *       node, a wrong <html lang>, a translated raw diagnostic and an injected
 *       in-window request;
 *   V   visual matrix screenshots (1440x900 + 390x844, light/dark, en/zh-CN)
 *       with a sha256 manifest.
 *
 * Build + run (from web/):
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   VITE_ENABLE_I18N_PREFERENCES=true ./node_modules/.bin/vite build --outDir <tmp>/dist-preview
 *   node scripts/w3a-core-browser-evidence.mjs --dist <tmp>/dist-ordinary \
 *     --preview-dist <tmp>/dist-preview --out <evidence dir> --head <sha>
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

const ordinaryDist = arg('dist') && resolve(arg('dist'));
const previewDist = arg('preview-dist') && resolve(arg('preview-dist'));
const outDir = resolve(arg('out', '.w3a-core-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
// Preferences gate markers (W2c) + W3a/W2c evidence identifiers that must never ship.
const GATED_STRINGS = ['settings-preferences', 'happyranch-ui-language'];
const EVIDENCE_MARKERS = ['w3a-core-browser-evidence', '__hrRefs', '__hrTag', '__w3a_blank', 'w2c-preferences-browser-evidence', 'I18N_BROWSER_EVIDENCE'];
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon',
};


// ------------------------------------------------------------------ dist inspection
function distJsFiles(dir) {
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

function distFingerprint(dir) {
  const html = readFileSync(join(dir, 'index.html'), 'utf8');
  const mainRel = (html.match(/<script[^>]+src="([^"]+\.js)"/) || [])[1];
  const mainPath = mainRel ? join(dir, mainRel.replace(/^\//, '')) : null;
  const files = distJsFiles(dir);
  const joined = files.map((f) => readFileSync(f, 'utf8')).join('\n');
  return {
    dir,
    indexHtmlSha256: sha256(readFileSync(join(dir, 'index.html'))),
    mainJs: mainRel,
    mainJsSha256: mainPath && existsSync(mainPath) ? sha256(readFileSync(mainPath)) : null,
    jsFileCount: files.length,
    gatedStrings: Object.fromEntries(GATED_STRINGS.map((s) => [s, joined.includes(s)])),
  };
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
// Authored/raw fixture bytes that must survive every locale unchanged.
const AUTH = {
  title: 'Ship the v2 importer — «raw» <b>ok</b> #42',
  md: 'Plan: **bold step** then `npm run build` — see [the spec](https://example.com/spec?id=THR-101&x=1).',
  mdText: 'bold step',
  href: 'https://example.com/spec?id=THR-101&x=1',
  agentReply: 'Ack from dev_agent: ETA 2h; path /srv/data/v2.csv',
  file: 'Q3-report_final.v2.csv',
  secondTitle: 'Release notes draft (empty)',
};
const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const SYNTH = { dashboard: 'ok', threads: 'ok', detail: 'ok', send: 'ok', ledger: [], hung: [], created: [], sent: [], uploads: 0 };

const AGENTS = [
  { name: 'dev_agent', team: 'engineering', role: 'worker', executor: 'claude', description: '', repos: {}, system_prompt: '' },
  { name: 'qa_engineer', team: 'engineering', role: 'worker', executor: 'codex', description: '', repos: {}, system_prompt: '' },
];
function threadRecord(id, subject, extra = {}) {
  return {
    thread_id: id, subject, status: 'open', started_at: iso(3 * 3600e3), archived_at: null,
    forwarded_from_id: null, forwarded_from_kind: null, turn_cap: 10, turns_used: 2, summary: null,
    transcript_path: null, composed_from_dream_id: null, last_speaker: 'dev_agent', pinned: false,
    pinned_at: null, last_activity_at: iso(5 * 60e3), participants: ['dev_agent', 'qa_engineer'], ...extra,
  };
}
const T1 = threadRecord('THR-101', AUTH.title, { pinned: true, pinned_at: iso(3600e3) });
const T2 = threadRecord('THR-102', AUTH.secondTitle, { participants: ['qa_engineer'], last_speaker: null });
const MSGS_T1 = [
  { seq: 1, speaker: 'founder', kind: 'message', body_markdown: AUTH.md, decline_reason: null, system_payload: null,
    attachments: [{ artifact_name: `threads/THR-101/${AUTH.file}`, display_name: AUTH.file, size_bytes: 2048, content_type: 'text/csv', uploaded_by: 'founder', thread_attachment_id: 'TA-1' }],
    created_at: iso(3 * 3600e3), responder_status: [] },
  { seq: 2, speaker: 'dev_agent', kind: 'message', body_markdown: AUTH.agentReply, decline_reason: null, system_payload: null,
    attachments: [], created_at: iso(2 * 3600e3), responder_status: [] },
];
function threadList() {
  if (SYNTH.threads === 'empty') return { threads: [] };
  return { threads: [T1, T2, ...SYNTH.created.map((c) => c.record)] };
}
function threadDetail(id) {
  const created = SYNTH.created.find((c) => c.record.thread_id === id);
  if (created) return { ...created.record, messages: created.messages, reply_delivery: [] };
  if (id === 'THR-101') return { ...T1, messages: [...MSGS_T1, ...SYNTH.sent], reply_delivery: [] };
  if (id === 'THR-102') return { ...T2, messages: [], reply_delivery: [] };
  return null;
}
const DASH_POPULATED = {
  heartbeat: Array.from({ length: 24 }, (_, hour) => ({ hour, steps: hour % 5, failed: hour === 7 ? 1 : 0, tier: hour === 7 ? 'warn' : 'ok' })),
  narrative_counts: { completed_today: 3, failed_today: 1, escalated_open: 1, kb_added_today: 2, agents_active_now: 2, spend_today_usd: 1.25 },
  escalations: [{ task_id: 'TASK-9001', agent: 'dev_agent', team: 'engineering', question: 'Should I migrate table `jobs_v2`? (authored)', raised_at: iso(40 * 60e3), age_seconds: 2400, flavor: null }],
  pending_review_jobs: [{ id: 'JOB-777', task_id: 'TASK-9002', agent_name: 'qa_engineer', title: 'Run nightly e2e (authored title)', created_at: iso(20 * 60e3) }],
  active_by_team: [{ team: 'engineering', count: 2, task_ids: ['TASK-9003', 'TASK-9004'] }],
  recent_activity: [
    { timestamp: iso(10 * 60e3), who: 'dev_agent', event_kind: 'task_completed', task_id: 'TASK-9003', verdict: 'ok' },
    { timestamp: iso(30 * 60e3), who: 'qa_engineer', event_kind: 'task_failed', task_id: 'TASK-9005', verdict: 'fail' },
  ],
  updates_this_week: [{ marker: 'ok', text: 'Importer v2 shipped (authored update)', meta: 'engineering · PR #901', timestamp: iso(86400e3) }],
  org_pulse: [{ team: 'engineering', acceptance_pct: 82, trend_delta: 4, sparkline: [60, 70, 75, 82], members: 2, lead: 'dev_agent' }],
  org_age_days: 12,
  server_now: new Date(now).toISOString(),
  generated_at: new Date(now).toISOString(),
};
const DASH_EMPTY = {
  heartbeat: Array.from({ length: 24 }, (_, hour) => ({ hour, steps: 0, failed: 0, tier: 'ok' })),
  narrative_counts: { completed_today: 0, failed_today: 0, escalated_open: 0, kb_added_today: 0, agents_active_now: 0, spend_today_usd: 0 },
  escalations: [], pending_review_jobs: [], active_by_team: [], recent_activity: [], updates_this_week: [], org_pulse: [],
  org_age_days: 0, server_now: new Date(now).toISOString(), generated_at: null,
};

function apiBody(pathname, search) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w3a-evidence-token' };
  if (pathname === '/api/v1/orgs') return { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] };
  if (pathname === `/api/v1/orgs/${ORG}/agents`) return { agents: AGENTS };
  if (pathname === `/api/v1/orgs/${ORG}/teams`) return { teams: [{ name: 'engineering', manager: 'dev_agent', members: ['dev_agent', 'qa_engineer'] }] };
  if (pathname === `/api/v1/orgs/${ORG}/tokens`) return search.includes('group_by') ? { rollup: [] } : { rows: [] };
  if (pathname === `/api/v1/orgs/${ORG}/settings`) return { system: {}, org: {} };
  return {};
}

function startServer(root, label) {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        const p = url.pathname;
        const json = (status, body) => {
          response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(body));
        };
        if (p.startsWith('/api/')) {
          SYNTH.ledger.push({ dist: label, method: request.method, path: p, search: url.search, t: Date.now() });
          if (p.endsWith('/events') || p.includes('/stream') || p.endsWith('/tail')) {
            response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
            SYNTH.hung.push(response);
            return;
          }
          const hang = () => { SYNTH.hung.push(response); };
          if (p === `/api/v1/orgs/${ORG}/dashboard/summary`) {
            if (SYNTH.dashboard === 'loading') return hang();
            if (SYNTH.dashboard === 'error') return json(500, { detail: 'dashboard exploded: raw-ID-77' });
            return json(200, SYNTH.dashboard === 'empty' ? DASH_EMPTY : DASH_POPULATED);
          }
          if (p === `/api/v1/orgs/${ORG}/threads` && request.method === 'GET') {
            if (SYNTH.threads === 'loading') return hang();
            if (SYNTH.threads === 'error') return json(500, { detail: 'threads exploded: raw-ID-88' });
            return json(200, threadList());
          }
          if (p === `/api/v1/orgs/${ORG}/artifacts` && request.method === 'POST') {
            request.resume();
            request.on('end', () => {
              SYNTH.uploads += 1;
              json(200, { name: url.searchParams.get('name') || `upload-${SYNTH.uploads}`, size_bytes: 12, modified_at: new Date().toISOString() });
            });
            return;
          }
          if (p === `/api/v1/orgs/${ORG}/threads` && request.method === 'POST') {
            let raw = '';
            request.on('data', (c) => { raw += c; });
            request.on('end', () => {
              const body = JSON.parse(raw || '{}');
              const id = `THR-${201 + SYNTH.created.length}`;
              const record = threadRecord(id, body.subject, { participants: body.recipients || [], turns_used: 0 });
              const messages = [{ seq: 1, speaker: 'founder', kind: 'message', body_markdown: body.body_markdown, decline_reason: null, system_payload: null,
                attachments: (body.attachments || []).map((a, i) => ({ artifact_name: a.artifact_name, display_name: a.display_name || a.artifact_name, size_bytes: 12, content_type: 'text/plain', uploaded_by: 'founder', thread_attachment_id: `TA-C${i}` })),
                created_at: new Date().toISOString(), responder_status: [] }];
              SYNTH.created.push({ record, messages, request: body });
              json(200, { thread_id: id, started_at: record.started_at, pending_replies: 1 });
            });
            return;
          }
          const m = p.match(new RegExp(`^/api/v1/orgs/${ORG}/threads/(THR-\\d+)(/[a-z-]+)?$`));
          if (m) {
            const [, id, sub] = m;
            if (!sub && request.method === 'GET') {
              if (SYNTH.detail === 'error' && id === 'THR-101') return json(500, { detail: 'detail exploded: raw-ID-99' });
              const d = threadDetail(id);
              return d ? json(200, d) : json(404, { detail: { code: 'not_found' } });
            }
            if (sub === '/messages') {
              const d = threadDetail(id);
              return json(200, { messages: d ? d.messages : [], has_more: false, next_since_seq: d ? d.messages.length : 0, reply_delivery: [] });
            }
            if (sub === '/tasks') return json(200, []);
            if (sub === '/send' && request.method === 'POST') {
              let raw = '';
              request.on('data', (c) => { raw += c; });
              request.on('end', () => {
                const body = JSON.parse(raw || '{}');
                if (SYNTH.send === 'mapped') return json(409, { detail: { code: 'thread_not_open', message: 'thread is archived' } });
                if (SYNTH.send === 'raw') return json(500, { detail: { code: 'w3a_unmapped_code', message: 'boom' } });
                const seq = MSGS_T1.length + SYNTH.sent.length + 1;
                SYNTH.sent.push({ seq, speaker: 'founder', kind: 'message', body_markdown: body.body_markdown, decline_reason: null, system_payload: null,
                  attachments: (body.attachments || []).map((a, i) => ({ artifact_name: a.artifact_name, display_name: a.display_name || a.artifact_name, size_bytes: 12, content_type: 'text/plain', uploaded_by: 'founder', thread_attachment_id: `TA-S${seq}-${i}` })),
                  created_at: new Date().toISOString(), responder_status: [] });
                json(200, { seq, thread_id: id });
              });
              return;
            }
            return json(200, {});
          }
          return json(200, apiBody(p, url.search));
        }
        if (p === '/__w3a_blank') {
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
          response.end('<!doctype html><title>w3a second tab</title>');
          return;
        }
        const relative = decodeURIComponent(p);
        let file = relative === '/' ? join(root, 'index.html') : join(root, relative);
        if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
        response.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
        response.end(readFileSync(file));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.on('upgrade', (request, socket) => {
      SYNTH.ledger.push({ dist: label, method: 'WS-UPGRADE', path: new URL(request.url, 'http://x').pathname, t: Date.now() });
      socket.destroy();
    });
    server.on('error', rejectPromise);
    server.listen(0, '127.0.0.1', () => resolvePromise({ server, url: `http://127.0.0.1:${server.address().port}` }));
  });
}

function fingerprint(dir) {
  const fp = distFingerprint(dir);
  const joined = distJsFiles(dir).map((f) => readFileSync(f, 'utf8')).join('\n');
  fp.evidenceMarkers = Object.fromEntries(EVIDENCE_MARKERS.map((s) => [s, joined.includes(s)]));
  return fp;
}

// ------------------------------------------------------------------ init scripts
const seedLocale = (locale) => (locale ? `try { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); } catch (e) {}` : `try { localStorage.removeItem(${JSON.stringify(LOCALE_KEY)}); } catch (e) {}`);
const seedTheme = (theme) => `try { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); } catch (e) {}`;
const CHINESE_NAVIGATOR = `(() => {
  Object.defineProperty(Navigator.prototype, 'language', { get: () => 'zh-CN', configurable: true });
  Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
})();`;

// Shared predicates: positive cases and causal negatives run the SAME expressions.
const PRED = {
  lang: (expected) => `document.documentElement.lang === ${JSON.stringify(expected)}`,
  identity: (key) => `(() => {
    const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}];
    const el = ref ? ref.deref() : null;
    const sel = window.__hrSel && window.__hrSel[${JSON.stringify(key)}];
    const current = sel ? sel() : null;
    return Boolean(el && current && el === current && el.isConnected && el.__hrTag === ${JSON.stringify(key)});
  })()`,
  focused: (key) => `(() => { const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}]; return Boolean(ref && ref.deref() && document.activeElement === ref.deref()); })()`,
  text: (key, expected) => `(() => { const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}]; const el = ref && ref.deref(); return Boolean(el) && el.textContent === ${JSON.stringify(expected)}; })()`,
  value: (key, expected) => `(() => { const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}]; const el = ref && ref.deref(); return Boolean(el) && el.value === ${JSON.stringify(expected)}; })()`,
  bodyHas: (s) => `document.body.textContent.includes(${JSON.stringify(s)})`, // textContent: CSS text-transform must not hide catalog bytes
};
const zeroApiPredicate = (delta) => delta.length === 0;

/**
 * Tag nodes: `specs` is { key: selectorFnSource } where the source is an
 * expression returning an element. Stores a WeakRef + a re-query function so
 * identity compares the tagged node with a FRESH query after the switch.
 */
const tagNodes = (specs) => `(() => {
  window.__hrRefs = window.__hrRefs || {}; window.__hrSel = window.__hrSel || {};
  const out = {};
  ${Object.entries(specs).map(([key, src]) => `{
    const sel = () => (${src});
    const el = sel();
    if (el) { el.__hrTag = ${JSON.stringify(key)}; window.__hrRefs[${JSON.stringify(key)}] = new WeakRef(el); window.__hrSel[${JSON.stringify(key)}] = sel; }
    out[${JSON.stringify(key)}] = Boolean(el);
  }`).join('\n')}
  return out;
})()`;

// DOM query sources (evaluated in the page).
const q = {
  dialog: `document.querySelector('[role="dialog"][data-state="open"]') || [...document.querySelectorAll('[role="dialog"]')].find((d) => d.querySelector('input[type="file"]'))`,
  byLabel: (labelText) => `(() => { const l = [...document.querySelectorAll('label')].find((x) => x.textContent.trim() === ${JSON.stringify(labelText)}); return l ? document.getElementById(l.htmlFor) : null; })()`,
  buttonText: (scope, text) => `[...(${scope} || document).querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(text)}) || null`,
  aria: (label) => `document.querySelector('[aria-label=${JSON.stringify(label).replace(/'/g, "\\'")}]')`,
};

// ------------------------------------------------------------------ main
async function main() {
  if (!ordinaryDist || !existsSync(join(ordinaryDist, 'index.html'))) throw new Error('missing --dist <dir> (ordinary build)');
  if (!previewDist || !existsSync(join(previewDist, 'index.html'))) throw new Error('missing --preview-dist <dir> (Preferences gate positive control)');
  mkdirSync(outDir, { recursive: true });

  const cases = [];
  const screenshots = [];
  const notes = [];
  let current = null;
  function beginCase(id, title) {
    current = { id, title, checks: [], ledgerFrom: SYNTH.ledger.length, requests: [], pass: false };
    cases.push(current);
    console.log(`\n=== ${id}: ${title}`);
  }
  function endCase() {
    current.requests = SYNTH.ledger.slice(current.ledgerFrom);
    current.pass = current.checks.length > 0 && current.checks.every((c) => c.ok);
    delete current.ledgerFrom;
    console.log(`--- ${current.id} ${current.pass ? 'PASS' : 'FAIL'}`);
  }
  function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    current.checks.push({ name, actual, expected, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`}`);
    return ok;
  }
  function negative(name, predicateValue, controlValue) {
    const ok = controlValue === true && predicateValue === false;
    current.checks.push({ name, controlBeforeFault: controlValue, predicateAfterFault: predicateValue, ok, verdict: ok ? 'negative control detected' : 'NOT detected' });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name} — ${ok ? 'negative control detected' : `control=${controlValue} after=${predicateValue}`}`);
  }

  const fpOrdinary = fingerprint(ordinaryDist);
  const fpPreview = fingerprint(previewDist);
  const ordinary = await startServer(ordinaryDist, 'ordinary');
  const preview = await startServer(previewDist, 'preview');
  const base = ordinary.url;
  const userDataDir = `/tmp/w3a-${process.pid}`; // short: Chrome's singleton socket path limit
  rmSync(userDataDir, { recursive: true, force: true });
  mkdirSync(userDataDir, { recursive: true });
  let chrome;
  let chromeStderr = '';
  let cdp;
  let chromeVersion = null;
  try {
    chrome = spawn(chromeBin, [
      '--headless=new', '--lang=zh-CN', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
      '--no-sandbox', '--disable-setuid-sandbox', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
      '--disable-dev-shm-usage', '--disable-extensions', '--disable-background-networking', '--hide-scrollbars',
      '--disable-crash-reporter', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
      '--disable-backgrounding-occluded-windows', 'about:blank',
    ], {
      stdio: ['ignore', 'ignore', 'pipe'],
      env: { ...process.env, HOME: userDataDir, TMPDIR: userDataDir, TMP: userDataDir, TEMP: userDataDir },
    });
    chrome.stderr.on('data', (chunk) => { chromeStderr += chunk.toString(); });
    const devtools = await waitForDevTools(userDataDir);
    chromeVersion = (await (await fetch(`http://127.0.0.1:${devtools.port}/json/version`)).json()).Browser;
    cdp = new CDP(`ws://127.0.0.1:${devtools.port}${devtools.path}`);
    await cdp.ready;

    // ---------------------------------------------------------- page helpers
    async function openPage({ url, init = '', width = 1440, height = 900 }) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      for (const domain of ['Page', 'Runtime', 'DOM', 'CSS']) await cdp.send(`${domain}.enable`, {}, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
      if (init.trim()) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: init }, sessionId);
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
    async function waitForValue(page, expression, label, timeout = 15000) {
      const deadline = Date.now() + timeout;
      let last;
      while (Date.now() < deadline) {
        try { last = await evaluate(page, expression); } catch (error) { last = `error: ${error.message}`; }
        if (last === true || (last && typeof last !== 'string') || (typeof last === 'string' && !last.startsWith('error:') && last)) return last;
        await sleep(100);
      }
      throw new Error(`timeout waiting for ${label}; last=${JSON.stringify(last)}`);
    }

    async function mouseClick(page, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, page.sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, page.sessionId);
      if (!nodeId) throw new Error(`mouseClick: not found ${selector}`);
      await cdp.send('DOM.scrollIntoViewIfNeeded', { nodeId }, page.sessionId);
      const { model } = await cdp.send('DOM.getBoxModel', { nodeId }, page.sessionId);
      const x = (model.border[0] + model.border[4]) / 2;
      const y = (model.border[1] + model.border[5]) / 2;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, page.sessionId);
    }
    /** Real mouse click on the first `selector` match whose trimmed text is `text`. */
    async function clickByText(page, selector, text) {
      const box = await evaluate(page, `(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find((e) => e.textContent.trim() === ${JSON.stringify(text)}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!box) throw new Error(`clickByText: no ${selector} with text ${text}`);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
    }
    const KEYS = {
      Space: { key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' ' },
      Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 },
      ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38 },
      ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 },
      Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 },
      Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
    };
    async function pressKey(page, name) {
      const { text, ...k } = KEYS[name];
      await cdp.send('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', ...k, ...(text ? { text } : {}) }, page.sessionId);
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...k }, page.sessionId);
    }

    // ---------------------------------------------------------- W3a helpers
    async function capture(page, name, meta) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, buffer);
      screenshots.push({ name, file: `${name}.png`, sha256: sha256(buffer), bytes: buffer.length, ...meta });
    }
    async function setViewport(page, width, height) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, page.sessionId);
      await sleep(300);
    }
    async function setTheme(page, theme) {
      await evaluate(page, `(() => { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)}); return true; })()`);
      await sleep(200);
    }
    async function open(path, { locale, theme = 'light', width = 1440, height = 900, until, label }) {
      const page = await openPage({ url: `${base}${path}`, init: `${seedLocale(locale)}\n${seedTheme(theme)}\n${CHINESE_NAVIGATOR}`, width, height });
      if (until) await waitForValue(page, until, label || path, 15000).catch((e) => { notes.push({ path, wait: String(e.message) }); });
      await sleep(400);
      return page;
    }
    /** Second same-origin tab writes the preference → `storage` event in `page`. */
    async function crossTabSwitch(page, locale) {
      const other = await openPage({ url: `${base}/__w3a_blank` });
      await evaluate(other, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); return true; })()`);
      await closePage(other);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await waitForValue(page, PRED.lang(locale), `storage-event switch to ${locale}`, 5000).catch(() => null);
      await sleep(500); // quiescence: any switch-caused request lands in the ledger
    }
    /** Window = only requests from THIS page's origin between from..now (the blank tab issues none). */
    const windowDelta = (from) => SYNTH.ledger.slice(from);
    async function typeInto(page, src, text) {
      await evaluate(page, `(() => { const el = (${src}); el.focus(); return true; })()`);
      await cdp.send('Input.insertText', { text }, page.sessionId);
      await sleep(150);
    }
    async function setFile(page, src, name, content) {
      // Real file-chooser path: DOM.setFileInputFiles on the actual input.
      const tmp = join(outDir, '.upload');
      mkdirSync(tmp, { recursive: true });
      const path = join(tmp, name);
      writeFileSync(path, content);
      const { result } = await cdp.send('Runtime.evaluate', { expression: `(${src})` }, page.sessionId);
      if (!result.objectId) throw new Error(`setFile: input not found ${src.slice(0, 120)}`);
      await cdp.send('DOM.setFileInputFiles', { objectId: result.objectId, files: [path] }, page.sessionId);
      await sleep(250);
    }
    async function clickSrc(page, src) {
      const box = await evaluate(page, `(() => { const el = (${src}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!box) throw new Error(`clickSrc: not found ${src.slice(0, 120)}`);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
    }
    const noOverflow = `document.documentElement.scrollWidth <= innerWidth + 1`;
    const other = (l) => (l === 'en' ? 'zh-CN' : 'en');
    const short = (l) => (l === 'en' ? 'en' : 'zh');
    const composerSrc = (l) => `document.querySelector('textarea[aria-label=${JSON.stringify(tr(l, 'threads.page.composer.textareaAria'))}]')`;

    // ============================================================ G: gate closed
    beginCase('G', 'ordinary build keeps Preferences closed, excludes evidence markers; unset + Chinese navigator stays English');
    {
      for (const s of GATED_STRINGS) {
        check(`G ordinary JS lacks "${s}"`, fpOrdinary.gatedStrings[s], false);
        check(`G preview JS contains "${s}" (positive control)`, fpPreview.gatedStrings[s], true);
      }
      for (const s of EVIDENCE_MARKERS) check(`G ordinary JS lacks evidence marker "${s}"`, fpOrdinary.evidenceMarkers[s], false);
      const page = await open(`/orgs/${ORG}/settings/preferences`, { locale: null, until: `location.pathname.endsWith('/settings/assistant')` });
      await sleep(500);
      const r = await evaluate(page, `({ path: location.pathname, lang: document.documentElement.lang, navLang: navigator.language, radios: document.querySelectorAll('input[name="happyranch-ui-language"]').length, prefsLinks: [...document.querySelectorAll('a')].filter((a) => (a.getAttribute('href') || '').endsWith('/settings/preferences')).length, stored: localStorage.getItem(${JSON.stringify(LOCALE_KEY)}) })`);
      check('G direct /settings/preferences redirected', r.path, `/orgs/${ORG}/settings/assistant`);
      check('G navigator is Chinese (precondition)', r.navLang, 'zh-CN');
      check('G unset preference renders <html lang=en>', r.lang, 'en');
      check('G no selector radios / Preferences link', [r.radios, r.prefsLinks], [0, 0]);
      check('G no preference written', r.stored, null);
      await closePage(page);
      const dash = await open(`/orgs/${ORG}/dashboard`, { locale: null, until: PRED.bodyHas(tr('en', 'dashboard.today.title')) });
      check('G unset + Chinese navigator: dashboard English', await evaluate(dash, `${PRED.lang('en')} && ${PRED.bodyHas(tr('en', 'dashboard.today.title'))} && !${PRED.bodyHas(tr('zh-CN', 'dashboard.today.title'))}`), true);
      await closePage(dash);
    }
    endCase();

    // ============================================================ D: dashboard states
    beginCase('D-states', 'Dashboard loading / error→Retry / first-run / populated in en and zh-CN');
    for (const locale of ['en', 'zh-CN']) {
      SYNTH.dashboard = 'loading';
      let page = await open(`/orgs/${ORG}/dashboard`, { locale, until: PRED.bodyHas(tr(locale, 'dashboard.loading')) });
      check(`D ${locale} loading copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'dashboard.loading'))), true);
      await capture(page, `${short(locale)}-dashboard-loading-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'dashboard-loading' });
      await closePage(page);
      SYNTH.dashboard = 'error';
      page = await open(`/orgs/${ORG}/dashboard`, { locale, until: PRED.bodyHas(tr(locale, 'dashboard.error')) });
      check(`D ${locale} error copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'dashboard.error'))), true);
      await capture(page, `${short(locale)}-dashboard-error-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'dashboard-error' });
      SYNTH.dashboard = 'ok';
      const before = SYNTH.ledger.filter((x) => x.path.endsWith('/dashboard/summary')).length;
      await clickSrc(page, q.buttonText('document', tr(locale, 'dashboard.retry')));
      await waitForValue(page, PRED.bodyHas(tr(locale, 'dashboard.today.title')), 'retry → populated', 8000).catch(() => null);
      check(`D ${locale} Retry refetched and rendered populated`, [SYNTH.ledger.filter((x) => x.path.endsWith('/dashboard/summary')).length > before, await evaluate(page, PRED.bodyHas(tr(locale, 'dashboard.today.title')))], [true, true]);
      await closePage(page);
      SYNTH.dashboard = 'empty';
      page = await open(`/orgs/${ORG}/dashboard`, { locale, until: PRED.bodyHas(tr(locale, 'dashboard.firstRun.title')) });
      check(`D ${locale} first-run copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'dashboard.firstRun.title'))), true);
      await capture(page, `${short(locale)}-dashboard-first-run-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'dashboard-first-run-empty' });
      await closePage(page);
      SYNTH.dashboard = 'ok';
    }
    endCase();

    beginCase('D-switch', 'Dashboard populated: both switch directions keep nodes + entity bytes, zero /api requests');
    for (const first of ['en', 'zh-CN']) {
      const page = await open(`/orgs/${ORG}/dashboard`, { locale: first, until: PRED.bodyHas(tr(first, 'dashboard.today.title')) });
      const titleSrc = (l) => `[...document.querySelectorAll('h1,h2,h3,h4,div,span')].find((e) => e.children.length === 0 && e.textContent.trim() === ${JSON.stringify(tr(l, 'dashboard.today.title'))})`;
      await evaluate(page, tagNodes({ today: titleSrc(first), main: `document.querySelector('main')` }));
      const entities = ['TASK-9001', 'dev_agent', 'qa_engineer', 'engineering', 'JOB-777'];
      for (const target of [other(first), first]) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        const delta = windowDelta(from);
        check(`D ${first}→${target} <html lang>`, await evaluate(page, PRED.lang(target)), true);
        check(`D ${first}→${target} product copy in target locale`, await evaluate(page, `${PRED.bodyHas(tr(target, 'dashboard.today.title'))} && ${PRED.bodyHas(tr(target, 'dashboard.waiting.title'))}`), true);
        check(`D ${first}→${target} "Today" heading node retained + retranslated`, await evaluate(page, `(() => { const r = window.__hrRefs.today.deref(); return Boolean(r && r.isConnected && r.textContent.trim() === ${JSON.stringify(tr(target, 'dashboard.today.title'))}); })()`), true);
        check(`D ${first}→${target} <main> node retained`, await evaluate(page, PRED.identity('main')), true);
        check(`D ${first}→${target} entity/ID bytes present`, await evaluate(page, `[${entities.map((e) => JSON.stringify(e)).join(',')}].every((e) => document.body.textContent.includes(e))`), true);
        check(`D ${first}→${target} raw audit event_kind leaf byte-exact (task_completed, never spaced)`, await evaluate(page, `(() => { const leaves = [...document.querySelectorAll('main li span')].filter((e) => e.children.length === 0).map((e) => e.textContent); return leaves.includes('task_completed') && leaves.includes('task_failed') && !leaves.includes('task completed'); })()`), true);
        check(`D ${first}→${target} zero /api requests in switch window`, zeroApiPredicate(delta), true);
        current.switchWindows = [...(current.switchWindows || []), { label: `${first}→${target}`, delta }];
      }
      await closePage(page);
    }
    endCase();

    // ============================================================ L: threads list states
    beginCase('L', 'Threads list loading / error→Retry / empty / filter-empty / populated (pinned + selection) in en and zh-CN');
    for (const locale of ['en', 'zh-CN']) {
      SYNTH.threads = 'loading';
      let page = await open(`/orgs/${ORG}/threads`, { locale, until: PRED.lang(locale) });
      await sleep(600);
      check(`L ${locale} loading: no list rows yet`, await evaluate(page, `!document.body.textContent.includes(${JSON.stringify(AUTH.title)})`), true);
      await capture(page, `${short(locale)}-threads-loading-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'threads-list-loading' });
      await closePage(page);
      SYNTH.threads = 'error';
      page = await open(`/orgs/${ORG}/threads`, { locale, until: PRED.bodyHas(tr(locale, 'threads.page.list.errorTitle')) });
      check(`L ${locale} error copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'threads.page.list.errorTitle'))), true);
      await capture(page, `${short(locale)}-threads-error-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'threads-list-error' });
      SYNTH.threads = 'ok';
      await clickSrc(page, q.buttonText('document', tr(locale, 'threads.page.retry')));
      await waitForValue(page, PRED.bodyHas(AUTH.title), 'retry → list', 8000).catch(() => null);
      check(`L ${locale} Retry renders the list`, await evaluate(page, PRED.bodyHas(AUTH.title)), true);
      await closePage(page);
      SYNTH.threads = 'empty';
      page = await open(`/orgs/${ORG}/threads`, { locale, until: PRED.bodyHas(tr(locale, 'threads.page.list.emptyTitle')) });
      check(`L ${locale} empty copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'threads.page.list.emptyTitle'))), true);
      await capture(page, `${short(locale)}-threads-empty-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'threads-list-empty' });
      await closePage(page);
      SYNTH.threads = 'ok';
      page = await open(`/orgs/${ORG}/threads`, { locale, until: PRED.bodyHas(AUTH.title) });
      check(`L ${locale} pinned section + authored title`, await evaluate(page, `${PRED.bodyHas(tr(locale, 'threads.page.list.pinnedSection'))} && ${PRED.bodyHas(AUTH.title)} && ${PRED.bodyHas(AUTH.secondTitle)}`), true);
      await typeInto(page, `document.querySelector('input[placeholder=${JSON.stringify(tr(locale, 'threads.page.filterPlaceholder'))}]')`, 'zzz-no-match');
      await sleep(300);
      check(`L ${locale} filter-empty copy`, await evaluate(page, PRED.bodyHas(tr(locale, 'threads.page.list.filterEmpty'))), true);
      await capture(page, `${short(locale)}-threads-filter-empty-1440-light`, { viewport: '1440x900', theme: 'light', locale, state: 'threads-list-filter-empty' });
      await closePage(page);
    }
    endCase();

    // ============================================================ T: detail + authored bytes + selection across switch
    beginCase('T', 'thread detail: authored/ID bytes exact in zh-CN; selection/scroll kept across switches; no-messages; detail error');
    {
      const page = await open(`/orgs/${ORG}/threads/THR-101`, { locale: 'zh-CN', until: `${PRED.bodyHas(AUTH.mdText)} && ${PRED.bodyHas(AUTH.agentReply)}` });
      const r = await evaluate(page, `({
        title: document.body.textContent.includes(${JSON.stringify(AUTH.title)}),
        bold: [...document.querySelectorAll('strong')].some((s) => s.textContent === ${JSON.stringify(AUTH.mdText)}),
        code: [...document.querySelectorAll('code')].some((s) => s.textContent === 'npm run build'),
        href: [...document.querySelectorAll('a')].some((a) => a.getAttribute('href') === ${JSON.stringify(AUTH.href)}),
        reply: document.body.textContent.includes(${JSON.stringify(AUTH.agentReply)}),
        file: document.body.textContent.includes(${JSON.stringify(AUTH.file)}),
        ids: ['THR-101', 'dev_agent', 'qa_engineer'].every((s) => document.body.textContent.includes(s)),
        path: location.pathname,
      })`);
      check('T zh-CN authored title/Markdown/href/reply/filename/IDs verbatim', r, { title: true, bold: true, code: true, href: true, reply: true, file: true, ids: true, path: `/orgs/${ORG}/threads/THR-101` });
      check('T zh-CN product chrome localized (detail rail + composer)', await evaluate(page, `${PRED.lang('zh-CN')} && ${PRED.bodyHas(tr('zh-CN', 'threads.page.rail.participants'))} && Boolean(${composerSrc('zh-CN')})`), true);
      await evaluate(page, tagNodes({ strong: `[...document.querySelectorAll('strong')].find((s) => s.textContent === ${JSON.stringify(AUTH.mdText)})` }));
      for (const target of ['en', 'zh-CN']) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        check(`T →${target} selection/path kept`, await evaluate(page, 'location.pathname'), `/orgs/${ORG}/threads/THR-101`);
        check(`T →${target} rendered Markdown node retained`, await evaluate(page, PRED.identity('strong')), true);
        check(`T →${target} authored bytes unchanged`, await evaluate(page, `${PRED.bodyHas(AUTH.title)} && ${PRED.bodyHas(AUTH.agentReply)} && ${PRED.bodyHas(AUTH.file)}`), true);
        check(`T →${target} zero /api requests in switch window`, zeroApiPredicate(windowDelta(from)), true);
      }
      await closePage(page);
      const p2 = await open(`/orgs/${ORG}/threads/THR-102`, { locale: 'zh-CN', until: PRED.bodyHas(tr('zh-CN', 'threads.page.detail.noMessages')) });
      check('T zh-CN no-messages copy', await evaluate(p2, PRED.bodyHas(tr('zh-CN', 'threads.page.detail.noMessages'))), true);
      await capture(p2, 'zh-thread-no-messages-1440-light', { viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'thread-detail-no-messages' });
      await closePage(p2);
      SYNTH.detail = 'error';
      const p3 = await open(`/orgs/${ORG}/threads/THR-101`, { locale: 'zh-CN', until: PRED.bodyHas(tr('zh-CN', 'threads.page.detail.error')) });
      check('T zh-CN detail error copy', await evaluate(p3, PRED.bodyHas(tr('zh-CN', 'threads.page.detail.error'))), true);
      await capture(p3, 'zh-thread-detail-error-1440-light', { viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'thread-detail-error' });
      await closePage(p3);
      SYNTH.detail = 'ok';
    }
    endCase();

    // ============================================================ C: NewThreadDialog create journey
    const DRAFT = { subject: 'Kickoff — «subject» #7 <i>raw</i>', recipient: 'dev_agent', body: '## Plan\n- **ship** `v2`\n- see /srv/x.csv' };
    const dlg = q.dialog;
    const fieldSrc = (l, key) => q.byLabel(tr(l, key));
    for (const first of ['en', 'zh-CN']) {
      beginCase(`C-${short(first)}-first`, `NewThreadDialog: open + draft + attachment + focus; ${first}→${other(first)}→${first}; then one real create`);
      const page = await open(`/orgs/${ORG}/threads`, { locale: first, until: PRED.bodyHas(AUTH.title) });
      await clickSrc(page, q.buttonText('document', tr(first, 'threads.page.newThread')));
      await waitForValue(page, `Boolean(${fieldSrc(first, 'threads.newThread.subjectLabel')})`, 'new-thread dialog open', 5000).catch(() => null);
      await typeInto(page, fieldSrc(first, 'threads.newThread.subjectLabel'), DRAFT.subject);
      await typeInto(page, fieldSrc(first, 'threads.newThread.recipientsLabel'), DRAFT.recipient);
      await pressKey(page, 'Escape').catch(() => null); // close any mention/recipient suggestion popup (not the dialog)
      await sleep(150);
      const dialogOpen = await evaluate(page, `Boolean(${fieldSrc(first, 'threads.newThread.subjectLabel')})`);
      if (!dialogOpen) {
        await clickSrc(page, q.buttonText('document', tr(first, 'threads.page.newThread')));
        await sleep(300);
        await typeInto(page, fieldSrc(first, 'threads.newThread.subjectLabel'), DRAFT.subject);
        await typeInto(page, fieldSrc(first, 'threads.newThread.recipientsLabel'), DRAFT.recipient);
      }
      await setFile(page, `(${dlg}).querySelector('input[type="file"]')`, AUTH.file, 'a,b\n1,2\n');
      await typeInto(page, fieldSrc(first, 'threads.newThread.bodyLabel'), DRAFT.body);
      const chipSrc = `[...(${dlg}).querySelectorAll('span')].find((s) => s.children.length === 0 && s.textContent === ${JSON.stringify(AUTH.file)})`;
      const tagged = await evaluate(page, tagNodes({
        dialog: dlg,
        subject: `(${dlg}).querySelector('input[type="text"]')`,
        body: `(${dlg}).querySelector('textarea')`,
        chip: chipSrc,
      }));
      check(`C ${first} nodes tagged (dialog/subject/body/chip)`, tagged, { dialog: true, subject: true, body: true, chip: true });
      const uploadsBefore = SYNTH.uploads;
      const createdBefore = SYNTH.created.length;
      for (const target of [other(first), first]) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        const delta = windowDelta(from);
        check(`C ${first}: →${target} <html lang>`, await evaluate(page, PRED.lang(target)), true);
        check(`C ${first}: →${target} dialog title localized`, await evaluate(page, `(${dlg}).textContent.includes(${JSON.stringify(tr(target, 'threads.newThread.title'))})`), true);
        check(`C ${first}: →${target} field labels localized`, await evaluate(page, `Boolean(${fieldSrc(target, 'threads.newThread.subjectLabel')}) && Boolean(${fieldSrc(target, 'threads.newThread.bodyLabel')})`), true);
        for (const key of ['dialog', 'subject', 'body', 'chip']) check(`C ${first}: →${target} ${key} node identity retained`, await evaluate(page, PRED.identity(key)), true);
        check(`C ${first}: →${target} subject/body values exact`, await evaluate(page, `${PRED.value('subject', DRAFT.subject)} && ${PRED.value('body', DRAFT.body)}`), true);
        check(`C ${first}: →${target} recipient value exact`, await evaluate(page, `(${fieldSrc(target, 'threads.newThread.recipientsLabel')}).value.replace(/[ ,]+$/, '')`), DRAFT.recipient);
        check(`C ${first}: →${target} attachment filename exact`, await evaluate(page, PRED.text('chip', AUTH.file)), true);
        check(`C ${first}: →${target} focus retained on body`, await evaluate(page, PRED.focused('body')), true);
        check(`C ${first}: →${target} zero /api requests (no upload/compose/nav/settings)`, zeroApiPredicate(delta), true);
        current.switchWindows = [...(current.switchWindows || []), { label: `→${target}`, delta }];
      }
      check(`C ${first}: no upload/create during switches`, [SYNTH.uploads - uploadsBefore, SYNTH.created.length - createdBefore], [0, 0]);
      if (first === 'zh-CN') await capture(page, 'zh-new-thread-draft-1440-light', { viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'new-thread-draft-after-round-trip' });
      const from = SYNTH.ledger.length;
      await clickSrc(page, q.buttonText(`(${dlg})`, tr(first, 'threads.newThread.send')));
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline && SYNTH.created.length === createdBefore) await sleep(100);
      await sleep(1200);
      const posts = windowDelta(from).filter((x) => x.method === 'POST');
      check(`C ${first}: exactly one upload + one create`, [posts.filter((x) => x.path.endsWith('/artifacts')).length, posts.filter((x) => x.path === `/api/v1/orgs/${ORG}/threads`).length], [1, 1]);
      const created = SYNTH.created[SYNTH.created.length - 1];
      check(`C ${first}: create request carries exact authored bytes`, created ? [created.request.subject, created.request.body_markdown, (created.request.recipients || []).join(','), (created.request.attachments || []).length] : null, [DRAFT.subject, DRAFT.body, DRAFT.recipient, 1]);
      await waitForValue(page, `location.pathname.endsWith(${JSON.stringify(created ? created.record.thread_id : 'x')})`, 'navigated to created thread', 5000).catch(() => null);
      await sleep(600);
      check(`C ${first}: landed on the created thread with authored title`, await evaluate(page, `location.pathname.endsWith(${JSON.stringify(created ? created.record.thread_id : 'x')}) && ${PRED.bodyHas(DRAFT.subject)}`), true);
      await capture(page, `${short(first)}-thread-created-1440-light`, { viewport: '1440x900', theme: 'light', locale: first, state: 'create-journey-success' });
      await closePage(page);
      endCase();
    }

    // ============================================================ R: reply composer journey
    const REPLY = '**Update** — `step 2` done; see <THR-101> & /srv/y.csv';
    for (const first of ['en', 'zh-CN']) {
      beginCase(`R-${short(first)}-first`, `reply Composer: Markdown draft + attachment + focus; ${first}→${other(first)}→${first}; then one real reply`);
      const page = await open(`/orgs/${ORG}/threads/THR-101`, { locale: first, until: `Boolean(${composerSrc(first)})` });
      await setFile(page, `document.querySelector('input[type="file"][aria-label=${JSON.stringify(tr(first, 'threads.page.composer.attach'))}]') || [...document.querySelectorAll('input[type="file"]')].pop()`, 'reply-notes.txt', 'notes');
      await typeInto(page, composerSrc(first), REPLY);
      const chip = `[...document.querySelectorAll('span')].find((s) => s.children.length === 0 && s.textContent === 'reply-notes.txt')`;
      const tagged = await evaluate(page, tagNodes({ composer: composerSrc(first), chip, title: `[...document.querySelectorAll('*')].find((e) => e.children.length === 0 && e.textContent === ${JSON.stringify(AUTH.title)} && e.closest('main'))` }));
      check(`R ${first} nodes tagged`, tagged, { composer: true, chip: true, title: true });
      const sentBefore = SYNTH.sent.length;
      const uploadsBefore = SYNTH.uploads;
      for (const target of [other(first), first]) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        const delta = windowDelta(from);
        check(`R ${first}: →${target} <html lang>`, await evaluate(page, PRED.lang(target)), true);
        check(`R ${first}: →${target} composer aria-label localized on the SAME node`, await evaluate(page, `(() => { const r = window.__hrRefs.composer.deref(); return Boolean(r && r.isConnected && r.getAttribute('aria-label') === ${JSON.stringify(tr(target, 'threads.page.composer.textareaAria'))}); })()`), true);
        check(`R ${first}: →${target} draft Markdown exact`, await evaluate(page, PRED.value('composer', REPLY)), true);
        check(`R ${first}: →${target} chip node + filename exact`, await evaluate(page, `${PRED.text('chip', 'reply-notes.txt')} && window.__hrRefs.chip.deref().isConnected`), true);
        check(`R ${first}: →${target} authored title node retained`, await evaluate(page, `(() => { const r = window.__hrRefs.title.deref(); return Boolean(r && r.isConnected && r.textContent === ${JSON.stringify(AUTH.title)}); })()`), true);
        check(`R ${first}: →${target} focus retained in composer`, await evaluate(page, PRED.focused('composer')), true);
        check(`R ${first}: →${target} path unchanged`, await evaluate(page, 'location.pathname'), `/orgs/${ORG}/threads/THR-101`);
        check(`R ${first}: →${target} zero /api requests (no send/upload/nav/settings)`, zeroApiPredicate(delta), true);
        current.switchWindows = [...(current.switchWindows || []), { label: `→${target}`, delta }];
      }
      check(`R ${first}: nothing sent during switches`, [SYNTH.sent.length - sentBefore, SYNTH.uploads - uploadsBefore], [0, 0]);
      const from = SYNTH.ledger.length;
      await clickSrc(page, `document.querySelector('button[aria-label=${JSON.stringify(tr(first, 'threads.page.composer.send'))}]')`);
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline && SYNTH.sent.length === sentBefore) await sleep(100);
      await sleep(1200);
      const posts = windowDelta(from).filter((x) => x.method === 'POST');
      check(`R ${first}: exactly one upload + one send`, [posts.filter((x) => x.path.endsWith('/artifacts')).length, posts.filter((x) => x.path.endsWith('/send')).length], [1, 1]);
      check(`R ${first}: sent body is the exact draft`, SYNTH.sent.length ? SYNTH.sent[SYNTH.sent.length - 1].body_markdown : null, REPLY);
      await waitForValue(page, `[...document.querySelectorAll('strong')].some((s) => s.textContent === 'Update')`, 'reply rendered', 6000).catch(() => null);
      check(`R ${first}: reply rendered in the thread`, await evaluate(page, `[...document.querySelectorAll('strong')].some((s) => s.textContent === 'Update')`), true);
      await capture(page, `${short(first)}-thread-replied-1440-light`, { viewport: '1440x900', theme: 'light', locale: first, state: 'reply-journey-success' });
      await closePage(page);
      endCase();
    }

    // ============================================================ E: composer mapped vs raw error + N negatives
    beginCase('E', 'composer error: mapped daemon code re-translates in place without resend; raw HTTP 500 stays byte-exact; causal negatives');
    {
      const page = await open(`/orgs/${ORG}/threads/THR-101`, { locale: 'en', until: `Boolean(${composerSrc('en')})` });
      const mappedEn = tr('en', 'threads.error.threadNotOpen');
      const mappedZh = tr('zh-CN', 'threads.error.threadNotOpen');
      const errSrc = (text) => `[...document.querySelectorAll('main *')].find((e) => e.children.length === 0 && e.textContent.includes(${JSON.stringify(text)}))`;
      SYNTH.send = 'mapped';
      await typeInto(page, composerSrc('en'), 'mapped-error draft');
      let sendsBefore = SYNTH.ledger.filter((x) => x.path.endsWith('/send')).length;
      await clickSrc(page, `document.querySelector('button[aria-label=${JSON.stringify(tr('en', 'threads.page.composer.send'))}]')`);
      await waitForValue(page, `Boolean(${errSrc(mappedEn)})`, 'mapped error visible', 6000).catch(() => null);
      await evaluate(page, tagNodes({ mapped: errSrc(mappedEn) }));
      check('E mapped error shown in en', await evaluate(page, `Boolean(window.__hrRefs.mapped && window.__hrRefs.mapped.deref())`), true);
      for (const [target, text] of [['zh-CN', mappedZh], ['en', mappedEn]]) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        check(`E mapped →${target} re-translated in place (same node)`, await evaluate(page, `(() => { const r = window.__hrRefs.mapped.deref(); return Boolean(r && r.isConnected && r.textContent.includes(${JSON.stringify(text)})); })()`), true);
        check(`E mapped →${target} zero /api requests (no resend)`, zeroApiPredicate(windowDelta(from)), true);
      }
      check('E exactly one send attempt for the mapped error', SYNTH.ledger.filter((x) => x.path.endsWith('/send')).length - sendsBefore, 1);
      await crossTabSwitch(page, 'zh-CN');
      await capture(page, 'zh-composer-mapped-error-1440-light', { viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'composer-mapped-error-retranslated' });
      // Raw unmapped code → "HTTP 500" verbatim in both locales.
      SYNTH.send = 'raw';
      sendsBefore = SYNTH.ledger.filter((x) => x.path.endsWith('/send')).length;
      await clickSrc(page, `document.querySelector('button[aria-label=${JSON.stringify(tr('zh-CN', 'threads.page.composer.send'))}]')`);
      const rawPred = `Boolean(${errSrc('HTTP 500')}) && !document.querySelector('main').textContent.includes(${JSON.stringify(mappedZh)})`;
      await waitForValue(page, rawPred, 'raw error visible', 6000).catch(() => null);
      await evaluate(page, tagNodes({ raw: errSrc('HTTP 500') }));
      const RAW_PRED = `(() => { const r = window.__hrRefs.raw && window.__hrRefs.raw.deref(); return Boolean(r && r.isConnected && r.textContent.includes('HTTP 500')); })()`;
      for (const target of ['en', 'zh-CN']) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, target);
        check(`E raw →${target} "HTTP 500" byte-exact on the same node`, await evaluate(page, RAW_PRED), true);
        check(`E raw →${target} zero /api requests`, zeroApiPredicate(windowDelta(from)), true);
      }
      check('E exactly one send attempt for the raw error', SYNTH.ledger.filter((x) => x.path.endsWith('/send')).length - sendsBefore, 1);
      await capture(page, 'zh-composer-raw-error-1440-light', { viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'composer-raw-http-500-verbatim' });
      SYNTH.send = 'ok';

      // N: causal negatives on this page — the SAME predicates must go false.
      const rawControl = await evaluate(page, RAW_PRED);
      await evaluate(page, `(() => { const r = window.__hrRefs.raw.deref(); r.textContent = r.textContent.replace('HTTP 500', 'HTTP 服务器错误'); return true; })()`);
      negative('N translated raw diagnostic fails the raw predicate', await evaluate(page, RAW_PRED), rawControl);
      await evaluate(page, tagNodes({ composer: composerSrc('zh-CN') }));
      const idControl = await evaluate(page, PRED.identity('composer'));
      await evaluate(page, `(() => { const r = window.__hrRefs.composer.deref(); const c = r.cloneNode(true); r.replaceWith(c); return true; })()`);
      negative('N remounted (cloned) composer fails the node-identity predicate', await evaluate(page, PRED.identity('composer')), idControl);
      const langControl = await evaluate(page, PRED.lang('zh-CN'));
      await evaluate(page, `(() => { document.documentElement.lang = 'en'; return true; })()`);
      negative('N wrong <html lang> fails the locale predicate', await evaluate(page, PRED.lang('zh-CN')), langControl);
      const from = SYNTH.ledger.length;
      const zeroControl = zeroApiPredicate(windowDelta(from));
      await evaluate(page, `fetch('/api/v1/orgs/${ORG}/threads/THR-101/send', { method: 'POST', body: '{}' }).then(() => true, () => true)`);
      await sleep(300);
      negative('N an in-window request fails the zero-request predicate', zeroApiPredicate(windowDelta(from)), zeroControl);
      SYNTH.sent = SYNTH.sent.filter((m) => m.body_markdown !== undefined);
      await closePage(page);
    }
    endCase();

    // ============================================================ V: visual matrix
    beginCase('V', 'visual matrix: dashboard + thread detail × 1440/390 × light/dark × en/zh-CN (populated), no document overflow');
    for (const locale of ['en', 'zh-CN']) {
      for (const theme of ['light', 'dark']) {
        for (const [w, h] of [[1440, 900], [390, 844]]) {
          for (const [route, name, until] of [
            [`/orgs/${ORG}/dashboard`, 'dashboard', PRED.bodyHas(tr(locale, 'dashboard.today.title'))],
            [`/orgs/${ORG}/threads/THR-101`, 'thread', `${PRED.bodyHas(AUTH.mdText)}`],
            [`/orgs/${ORG}/threads`, 'threads-list', PRED.bodyHas(AUTH.title)],
          ]) {
            if (name === 'threads-list' && theme === 'dark' && w === 1440) continue; // list is also inside the thread shots
            const page = await open(route, { locale, theme, width: w, height: h, until });
            await setTheme(page, theme);
            const ok = await evaluate(page, `${PRED.lang(locale)} && ${until}`);
            check(`V ${name} ${locale} ${theme} ${w} rendered in locale`, ok, true);
            check(`V ${name} ${locale} ${theme} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
            await capture(page, `${short(locale)}-${name}-${w}-${theme}`, { viewport: `${w}x${h}`, theme, locale, state: `${name}-populated` });
            await closePage(page);
          }
        }
      }
    }
    // Narrow journey success states.
    for (const locale of ['en', 'zh-CN']) {
      const page = await open(`/orgs/${ORG}/threads/${SYNTH.created.length ? SYNTH.created[0].record.thread_id : 'THR-101'}`, { locale, theme: 'dark', width: 390, height: 844, until: PRED.lang(locale) });
      await setTheme(page, 'dark');
      await sleep(400);
      check(`V created thread ${locale} 390 dark no overflow`, await evaluate(page, noOverflow), true);
      await capture(page, `${short(locale)}-thread-created-390-dark`, { viewport: '390x844', theme: 'dark', locale, state: 'create-journey-success' });
      await closePage(page);
    }
    endCase();
  } finally {
    for (const response of SYNTH.hung) { try { response.destroy(); } catch { /* gone */ } }
    if (cdp) cdp.close();
    if (chrome && !chrome.killed) chrome.kill('SIGKILL');
    for (const s of [ordinary, preview]) { s.server.closeAllConnections?.(); s.server.close(); }
    rmSync(userDataDir, { recursive: true, force: true });
    rmSync(join(outDir, '.upload'), { recursive: true, force: true });
  }

  const failed = cases.filter((c) => !c.pass);
  const receipt = {
    head,
    generatedAt: new Date().toISOString(),
    node: process.version,
    chrome: { path: chromeBin, version: chromeVersion, lang: 'zh-CN (Chrome --lang + navigator override: environment never defaults the locale)' },
    dists: { ordinary: fpOrdinary, preview: fpPreview },
    gatedStringsChecked: GATED_STRINGS,
    evidenceMarkersChecked: EVIDENCE_MARKERS,
    notes: [...notes, ...(chromeStderr.trim() ? [{ chromeStderrTail: chromeStderr.slice(-1500) }] : [])],
    summary: cases.map((c) => ({ id: c.id, title: c.title, pass: c.pass, checks: c.checks.length, failedChecks: c.checks.filter((x) => !x.ok).map((x) => x.name) })),
    cases,
    screenshots,
    ledger: SYNTH.ledger,
    passed: cases.length - failed.length,
    failed: failed.length,
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

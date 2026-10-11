/** Built-app THR289 browser assertions. Compose ModeA; never start a daemon.
 * Usage: node .../shot-thr289-thread-list.mjs <output-dir> <short-profile>
 * Fixed HTTP fixtures own no SQL or client pagination algorithm. D/A tests
 * independently own server truth; this driver owns DOM, layout and requests.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const out = resolve(process.argv[2]);
const logs = resolve(process.env.THR289_BROWSER_LOGDIR || out);
const profile = resolve(process.argv[3]);
const session = 'task10008-thread-list';
await mkdir(out, { recursive: true });
await mkdir(logs, { recursive: true });
const ledger = [], streams = new Set(), pending = new Set();
let mode = 'large', hold = null, failure = null;
const row = (i, status = 'archived') => ({
  thread_id: `THR-${String(i).padStart(3, '0')}`, subject: `Fixture conversation ${i}`,
  status, started_at: new Date(Date.UTC(2026, 0, 1, 0, 600 - i)).toISOString(),
  archived_at: null, forwarded_from_id: null, forwarded_from_kind: null,
  turn_cap: 500, turns_used: 0, summary: null, transcript_path: null,
  composed_by: 'founder', composed_from_task_id: null,
  composed_from_dream_id: i <= 9 ? 'DREAM-FIXTURE' : null,
  last_speaker: null, pinned: false, pinned_at: null, last_activity_at: null, participants: [],
});
const fixtures = {
  large: Array.from({ length: 551 }, (_, i) => row(i + 1)),
  mixed: Array.from({ length: 551 }, (_, i) => row(i + 1, i % 2 ? 'archived' : 'open')),
  pins: Array.from({ length: 151 }, (_, i) => ({ ...row(i + 1, 'open'), pinned: i < 61, pinned_at: i < 61 ? '2026-01-01T00:00:00Z' : null })),
  filter: Array.from({ length: 151 }, (_, i) => ({ ...row(i + 1), subject: i < 50 || i === 150 ? `literal match ${i + 1}` : `Other subject ${i + 1}` })),
  empty: [],
  beta: [{ ...row(1, 'open'), subject: 'BETA isolated subject' }],
};
const pages = new Map();
for (const [name, rows] of Object.entries(fixtures)) {
  const totals = { open: rows.filter(r => r.status === 'open').length, archived: rows.filter(r => r.status === 'archived').length, all: rows.length, dream_origin: rows.filter(r => r.composed_from_dream_id).length };
  for (const bucket of ['all', 'open', 'archived']) {
    // Hand-seeded pin order is independent of the production comparator.
    const selected = name === 'pins' && bucket === 'open'
      ? [...rows.slice(0, 61).reverse(), ...rows.slice(61)]
      : rows.filter(r => bucket === 'all' || r.status === bucket);
    const fixed = new Map();
    for (let offset = 0; offset < Math.max(1, selected.length); offset += 50) {
      fixed.set(String(offset), { threads: selected.slice(offset, offset + 50), totals,
        has_more: offset + 50 < selected.length, next_cursor: offset + 50 < selected.length ? String(offset + 50) : null,
        sampled_at: '2026-10-07T03:30:00Z' });
    }
    pages.set(`${name}/${bucket}`, { fixed, expected: selected.map(r => r.thread_id), totals });
  }
}
const send = (res, value, status = 200) => { if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); } };
const emit = () => { for (const res of streams) if (!res.destroyed) res.write('data: {"thread_id":"THR-001","kind":"message","seq":1}\n\n'); };
const srv = await createServer({ root: findDist(), api: [
  ...defaultApiRoutes({ token: 'task10008-fixture-only', orgs: [{ slug: 'alpha', root: '/tmp/alpha-fixture' }, { slug: 'beta', root: '/tmp/beta-fixture' }] }),
  { path: '/api/__thr289/control', handler: async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    const action = JSON.parse(body || '{}');
    if (action.mode) { mode = action.mode; ledger.length = 0; }
    if ('hold' in action) hold = action.hold;
    if ('failure' in action) failure = action.failure;
    if (action.release) { hold = null; for (const entry of pending) { entry(); pending.delete(entry); } }
    if (action.emit) { for (let i = 0; i < action.emit; i++) emit(); }
    send(res, { mode, hold, failure, ledger, pending: pending.size });
  } },
  { path: '/api/__thr289/ledger', handler: (_req, res) => send(res, { ledger, pending: pending.size }) },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/agents$/, json: { agents: [] } },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/dashboard\/summary$/, json: { tasks: {}, threads: {}, agents: {} } },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/threads\/events$/, handler: (req, res) => {
    ledger.push({ kind: 'sse', url: req.url });
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    res.write(': synthetic fixture\n\n'); streams.add(res); res.on('close', () => streams.delete(res));
  } },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/threads$/, handler: (req, res) => {
    const url = new URL(req.url, srv.url), org = url.pathname.split('/')[4];
    const bucket = url.searchParams.get('status') || 'all', cursor = url.searchParams.get('cursor') || '0';
    const selected = pages.get(`${org === 'beta' ? 'beta' : mode}/${bucket}`);
    ledger.push({ kind: 'list', org, bucket, cursor, page_size: url.searchParams.get('page_size'), mode });
    assert.equal(url.searchParams.get('page_size'), '50');
    assert.equal(url.searchParams.has('limit'), false);
    const value = selected.fixed.get(cursor);
    assert.ok(value, `Unplanned fixture cursor ${mode}/${bucket}/${cursor}`);
    const reply = () => {
      if (failure === cursor || failure === 'all') { if (failure !== 'all') failure = null; send(res, { detail: 'synthetic page failure' }, 503); }
      else send(res, value);
    };
    if (hold === cursor || hold === 'all') pending.add(reply); else reply();
  } },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/threads\/THR-\d+\/tasks$/, json: [] },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/threads\/THR-\d+\/messages$/, json: { messages: [], has_more: false, next_seq: null } },
  { path: /^\/api\/v1\/orgs\/(alpha|beta)\/threads\/THR-\d+$/, handler: (req, res) => {
    const id = req.url.split('/').at(-1), thread = fixtures[mode].find(r => r.thread_id === id);
    send(res, { ...thread, participants: [], messages: [], pending_replies: [], reply_delivery: [] });
  } },
] });

async function cli(args, marker) {
  const result = await new Promise((done, reject) => {
    const proc = spawn('playwright-cli', [`-s=${session}`, ...args], { cwd: out, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = ''; proc.stdout.on('data', b => stdout += b); proc.stderr.on('data', b => stderr += b);
    proc.on('error', reject); proc.on('close', code => done({ code, stdout, stderr }));
  });
  await writeFile(join(logs, `${args[0]}-${Date.now()}.json`), JSON.stringify(result, null, 2));
  if (result.code !== 0 || result.stdout.includes('### Error') || marker && !result.stdout.includes(marker)) throw new Error(JSON.stringify(result));
  return result;
}

async function browserCase(page, cfg) {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const equal = (actual, expected, message) => assert(JSON.stringify(actual) === JSON.stringify(expected), `${message}: expected ${JSON.stringify(expected)}, observed ${JSON.stringify(actual)}`);
  const control = async value => (await page.request.post(cfg.url + '/api/__thr289/control', { data: value })).json();
  const ledger = async () => (await page.request.get(cfg.url + '/api/__thr289/ledger')).json();
  const wait = async (predicate, message) => {
    for (let i = 0; i < 100; i++) { if (await predicate()) return; await page.waitForTimeout(50); }
    throw new Error(`Timeout: ${message}`);
  };
  const links = () => page.locator('a[href^="/orgs/alpha/threads/THR-"]');
  const ids = () => links().evaluateAll(nodes => nodes.map(n => n.getAttribute('href').split('/').at(-1)));
  const shot = async name => page.screenshot({ path: `${cfg.out}/${cfg.tag}-${name}.png` });
  const tabs = page.getByRole('tab');
  const owner = async (target = 'bottom') => page.evaluate(target => {
    const link = document.querySelector('a[href^="/orgs/alpha/threads/THR-"]');
    const el = link?.closest('.overflow-y-auto');
    if (!el) throw new Error('No actual ContentWrap owner');
    if (target === 'bottom') el.scrollTop = el.scrollHeight;
    else if (typeof target === 'number') el.scrollTop = target;
    el.dispatchEvent(new Event('scroll', { bubbles: true }));
    return { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight };
  }, target);
  const route = async path => page.evaluate(path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }, path);
  await page.goto(cfg.url + '/api/__thr289/blank');
  await page.evaluate(({ locale, theme }) => { localStorage.clear(); sessionStorage.clear(); localStorage.setItem('happyranch.ui.locale', locale); localStorage.setItem('happyranch.theme', theme); }, cfg);
  await page.setViewportSize(cfg.viewport);
  await control({ mode: cfg.mode, hold: cfg.kind === 'states' ? 'all' : null, failure: null });
  await page.goto(cfg.url + '/orgs/alpha/threads');
  await tabs.first().waitFor();
  equal(await page.locator('html').getAttribute('lang'), cfg.locale, 'fixture locale applied before assertions');
  equal(await page.locator('html').getAttribute('data-theme'), cfg.theme, 'fixture theme applied before assertions');
  if (cfg.kind === 'states') {
    await wait(async () => (await tabs.nth(1).textContent()).includes('…'), 'initial unknown');
    await shot('unknown'); await control({ release: true });
  }
  if (cfg.bucket !== 'open') await tabs.nth(cfg.bucket === 'archived' ? 2 : 0).click();
  await wait(async () => (await ids()).length >= Math.min(50, cfg.expected.length), 'initial prefix');
  await wait(async () => !(await tabs.nth(0).textContent()).includes('…'), 'authoritative summary');
  const countText = await tabs.allTextContents();
  for (const [i, key] of ['all', 'open', 'archived'].entries()) assert(countText[i].includes(String(cfg.totals[key])), `authoritative ${key} count missing: ${countText}`);
  assert((await page.locator('header').allTextContents()).join(' ').includes(String(cfg.totals.dream_origin)), 'dream total missing');
  equal(await ids(), cfg.expected.slice(0, 50), 'initial returned order');
  const pinHeading = page.getByRole('heading', { name: cfg.locale === 'en' ? 'Pinned' : '已置顶', exact: true });
  if (cfg.mode === 'pins' && cfg.bucket === 'open') assert(await pinHeading.count() === 1, 'Open-only Pinned heading missing');
  else assert(await pinHeading.count() === 0, 'Pinned heading outside qualifying Open');
  if (cfg.kind === 'empty') {
    equal(await ids(), [], 'genuine successful empty set');
    equal(cfg.totals, { open: 0, archived: 0, all: 0, dream_origin: 0 }, 'genuine empty totals');
    await shot('empty');
  } else if (cfg.kind === 'filter') {
    await control({ hold: '100' });
    await page.getByRole('textbox').first().fill(cfg.needle);
    await wait(async () => (await ledger()).pending > 0, 'filter continuation gate');
    await page.getByRole('status').filter({ hasText: 'Searching remaining threads' }).waitFor();
    await shot('searching'); await control({ release: true });
    await wait(async () => JSON.stringify(await ids()) === JSON.stringify(cfg.matches), 'complete literal match set');
    await wait(async () => !(await page.getByRole('button', { name: /Load more threads/ }).count()), 'filter exhaustion');
    equal(await ids(), cfg.matches, 'literal predicate and serial exhaustion');
    await shot('filtered');
  } else if (cfg.kind === 'scope') {
    await control({ hold: '50' }); await owner();
    await wait(async () => (await ledger()).pending > 0, 'deferred alpha continuation');
    await route('/orgs/beta/threads');
    // The list retains its active bucket on org switch. This beta fixture's
    // row is Open; select that bucket while alpha's continuation stays held.
    await tabs.nth(1).click();
    await page.getByRole('link', { name: /BETA isolated subject/ }).waitFor();
    await control({ release: true }); await page.waitForTimeout(250);
    equal(await page.locator('a[href^="/orgs/beta/threads/THR-"]').allTextContents(), [await page.getByRole('link', { name: /BETA isolated subject/ }).textContent()], 'beta remains isolated');
    assert((await tabs.nth(0).textContent()).includes('1'), 'stale alpha total overwrote beta');
    await shot('beta-isolated');
  } else if (cfg.kind === 'states') {
    await shot('partial'); await control({ hold: '50', failure: '100' });
    await owner(); await wait(async () => (await ledger()).pending > 0, 'loading continuation gate');
    await page.getByRole('button', { name: cfg.locale === 'en' ? 'Loading more threads…' : '正在加载更多话题…', exact: true }).waitFor();
    await shot('loading-more'); await control({ release: true });
    await wait(async () => (await ids()).length === 100, 'second page');
    await owner(); const retry = page.getByRole('button', { name: cfg.locale === 'en' ? 'Retry' : '重试', exact: true });
    await retry.waitFor(); equal(await ids(), cfg.expected.slice(0, 100), 'next failure keeps usable rows');
    await shot('next-error'); const before = (await ledger()).ledger.filter(r => r.kind === 'list').length;
    for (let i = 0; i < 3; i++) { await owner(); await page.waitForTimeout(80); }
    equal((await ledger()).ledger.filter(r => r.kind === 'list').length, before, 'failed cursor storm');
    await retry.click(); await wait(async () => (await ids()).length === 150, 'explicit retry'); await shot('retried');
    await control({ hold: '0', emit: 1 });
    await page.getByRole('status').filter({ hasText: cfg.locale === 'en' ? 'Updating threads' : '正在更新话题' }).waitFor();
    equal(await ids(), cfg.expected.slice(0, 150), 'refresh exposes no staging'); await shot('updating');
    await control({ failure: 'all', release: true }); await retry.waitFor();
    await page.getByRole('status').filter({ hasText: cfg.locale === 'en' ? 'last recorded' : '上次获取' }).waitFor();
    equal(await ids(), cfg.expected.slice(0, 150), 'failed refresh retains rows'); await shot('stale');
    await control({ failure: null }); await retry.click();
    await wait(async () => !(await retry.count()), 'refresh retry');
    await control({ emit: 5 }); await wait(async () => !(await page.getByRole('status').filter({ hasText: cfg.locale === 'en' ? 'Updating threads' : '正在更新话题' }).count()), 'coalesced SSE settles');
    equal(await ids(), cfg.expected.slice(0, 150), 'quiescent coherent refresh');
    for (let i = 0; i < 20 && (await ids()).length < cfg.expected.length; i++) {
      const previous = (await ids()).length; await owner();
      await wait(async () => (await ids()).length > previous, 'post-retry sentinel continuation');
    }
    equal(await ids(), cfg.expected, 'post-retry complete quiescent DOM set');
    await shot('full');
  } else {
    for (let i = 0; i < 20 && (await ids()).length < cfg.expected.length; i++) {
      const previous = (await ids()).length; const bounds = await owner();
      assert(bounds.height > bounds.client, 'ContentWrap lacks real overflow');
      await wait(async () => (await ids()).length > previous, 'actual sentinel continuation');
    }
    equal(await ids(), cfg.expected, 'complete unique ordered DOM set');
    const before = (await ledger()).ledger.filter(r => r.kind === 'list' && r.bucket === cfg.bucket && r.cursor !== '0').length;
    for (let i = 0; i < 3; i++) { await owner(); await page.waitForTimeout(80); }
    equal((await ledger()).ledger.filter(r => r.kind === 'list' && r.bucket === cfg.bucket && r.cursor !== '0').length, before, 'terminal made extra request');
    const continuation = (await ledger()).ledger.filter(r => r.kind === 'list' && r.bucket === cfg.bucket && r.cursor !== '0').map(r => r.cursor);
    equal(continuation, Array.from({ length: Math.ceil(cfg.expected.length / 50) - 1 }, (_, i) => String((i + 1) * 50)), 'exact positive cursor ledger');
    await shot('full');
    if (cfg.kind === 'scroll') {
      await owner(8000); const target = links().nth(120); await target.scrollIntoViewIfNeeded();
      const saved = (await owner('read')).top; await target.click();
      assert(page.url().endsWith('/THR-121'), 'deep selection wrong destination');
      await page.goBack(); await wait(async () => (await ids()).length === cfg.expected.length, 'cached rows restored');
      await wait(async () => Math.abs((await owner('read')).top - saved) < 3, 'cached deep scroll restored');
      await page.reload();
      // Reload resets route-local bucket state to Open. Re-enter the saved
      // bucket through its real control before asserting that scope's depth.
      await tabs.first().waitFor();
      if (cfg.bucket !== 'open') await tabs.nth(cfg.bucket === 'archived' ? 2 : 0).click();
      await wait(async () => (await ids()).length >= 150, 'cold depth recovery');
      await wait(async () => Math.abs((await owner('read')).top - saved) < 3, 'cold deep scroll restored'); await shot('cold-restored');
    }
  }
  const result = { marker: 'THR289_BROWSER_CASE_OK', case: cfg.tag, viewport: page.viewportSize(), locale: await page.locator('html').getAttribute('lang'), theme: await page.locator('html').getAttribute('data-theme'), ids: await ids(), ledger: await ledger() };
  assert(result.locale === cfg.locale && result.theme === cfg.theme, 'locale/theme did not apply');
  return result;
}

const receipts = [];
try {
  await mkdir(profile, { recursive: true });
  await cli(['open', `--profile=${profile}`]);
  const run = async cfg => {
    const selected = pages.get(`${cfg.mode}/${cfg.bucket}`);
    const input = { url: srv.url, out, viewport: { width: 1440, height: 900 }, locale: 'en', theme: 'light', ...cfg, expected: selected.expected, totals: selected.totals };
    const result = await cli(['run-code', `async (page) => (${browserCase.toString()})(page, ${JSON.stringify(input)})`], 'THR289_BROWSER_CASE_OK');
    receipts.push({ case: cfg.tag, result }); await writeFile(join(logs, 'receipts.json'), JSON.stringify({ receipts, ledger }, null, 2));
    console.log(`THR289_BROWSER_CASE_OK ${cfg.tag}`);
  };
  for (let repetition = 1; repetition <= 5; repetition++) {
    await run({ tag: `C2-archived-${repetition}`, mode: 'large', bucket: 'archived', kind: repetition === 1 ? 'scroll' : 'traverse' });
    await run({ tag: `C2-mixed-all-${repetition}`, mode: 'mixed', bucket: 'all', kind: 'traverse' });
  }
  await run({ tag: 'C5-deferred-org', mode: 'large', bucket: 'archived', kind: 'scope' });
  for (const [name, needle, matches] of [
    ['subject', 'match 151', ['THR-151']], ['ID', 'THR-151', ['THR-151']],
    ['populated', 'literal match', [...fixtures.filter.slice(0, 50).map(r => r.thread_id), 'THR-151']],
  ]) await run({ tag: `C5-${name}`, mode: 'filter', bucket: 'archived', kind: 'filter', needle, matches });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    for (const locale of ['en', 'zh-CN']) for (const theme of ['light', 'dark']) {
      await run({ tag: `states-${viewport.width}-${locale}-${theme}`, mode: 'large', bucket: 'archived', kind: 'states', viewport, locale, theme });
      await run({ tag: `pins-${viewport.width}-${locale}-${theme}`, mode: 'pins', bucket: 'open', kind: 'traverse', viewport, locale, theme });
      await run({ tag: `empty-${viewport.width}-${locale}-${theme}`, mode: 'empty', bucket: 'open', kind: 'empty', viewport, locale, theme });
    }
  }
  console.log('THR289_BROWSER_ALL_OK');
} finally {
  try { await cli(['close']); } finally {
    for (const res of streams) res.end(); srv.server.closeAllConnections(); await srv.close();
    await writeFile(join(logs, 'final-ledger.json'), JSON.stringify(ledger, null, 2));
  }
}

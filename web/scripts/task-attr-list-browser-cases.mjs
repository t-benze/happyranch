/**
 * THR-390 PR8 Tasks list attribute filters + bounded export rows for
 * `w4a-browser-evidence.mjs --slice task-attr-list` (evidence tooling only;
 * not a required check, not wired into CI).
 *
 * Venue M: the real built SPA (`npm run build:local`, task_attributes_ui=true)
 * on the real route, served same-origin next to a synthetic `/api/v1` stub.
 * The roots/export handlers below evaluate the synthetic fixture with PR5
 * semantics (status/agent/attr_* AND, absent != present, root-only, pages of
 * the requested limit with a last-id cursor), so outputs follow from inputs.
 * Scenario overrides (`fixture.mode`) are set in-process by each case BEFORE
 * its triggering action; every assertion reads the server-side ledger.
 */
const DEFS = [
  ['comment', 'string', 'Comment', false],
  ['effort', 'numeric', 'Effort', false],
  ['fx_delta', 'numeric', 'Delta', false],
  ['legacy_risk', 'numeric', 'Legacy risk', true],
  ['ready', 'boolean', 'Ready', false],
  ['risk', 'numeric', 'Risk', false],
].map(([key, type, name, archived], index) => ({
  definition_id: `def-${key}`, version: 1, key, type, name, description: null, constraints: {},
  created_at: '2026-11-20T00:00:00Z', recorded_at: '2026-11-20T00:00:00Z', first_used_at: null,
  archived_at: archived ? '2026-11-21T00:00:00Z' : null, archived, _order: index,
}));
const EDITABILITY = { can_create: false, can_edit_metadata: false, can_edit_structure: false, can_archive: false, refusal_reason: null };

const iso = (minutesAgo) => new Date(Date.UTC(2026, 10, 25, 12, 0) - minutesAgo * 60e3).toISOString();
let seq = 0;
function root(id, { status = 'in_progress', agent = 'agent-a', values = {}, parent = null, brief } = {}) {
  seq += 1;
  return {
    task_id: id, assigned_agent: agent, team: 'eng', brief: brief ?? `Synthetic root ${id}`, status,
    parent_task_id: parent, created_at: iso(seq), updated_at: iso(seq), values,
  };
}
const M1V = { fx_delta: 0, ready: false, comment: '', risk: 2, effort: 4 };
const omit = (obj, key) => Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));
/** Fixture B (EM ruling 1: fx_delta own key, min -5 max 10) plus 51 page roots and attention roots. */
export function buildRoots() {
  seq = 0;
  const rows = [
    root('TASK-M1', { values: M1V }),
    root('TASK-D1', { status: 'completed', values: M1V }),
    root('TASK-D2', { agent: 'agent-b', values: M1V }),
    root('TASK-D3', { values: { ...M1V, fx_delta: -1 } }),
    root('TASK-D4', { values: omit(M1V, 'fx_delta') }),
    root('TASK-D5', { values: omit(M1V, 'ready') }),
    root('TASK-D6', { values: omit(M1V, 'comment') }),
    root('TASK-D7', { values: omit(M1V, 'risk') }),
    root('TASK-D7C', { values: { ...M1V, risk: 1 }, parent: 'TASK-D7' }),
    root('TASK-T3', { status: 'pending', values: { comment: '<img src=x onerror=alert(1)>' } }),
    root('TASK-T7', { status: 'pending', values: { comment: 'مرحبا ‮abc' } }),
    root('TASK-T8', { status: 'pending', values: { comment: 'x'.repeat(4000), legacy_risk: 8 } }),
    root('TASK-ESC1', { status: 'escalated', values: { effort: 9 } }),
  ];
  for (let i = 0; i < 51; i += 1) rows.push(root(`TASK-P${String(i).padStart(2, '0')}`, { values: { ...M1V, effort: 5 } }));
  // created_at DESC, id DESC (PR5 order).
  return rows.sort((a, b) => (a.created_at === b.created_at ? (a.task_id < b.task_id ? 1 : -1) : a.created_at < b.created_at ? 1 : -1));
}

const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
const NUM_RE = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/;
/** PR5 filter grammar on the decoded query; returns {preds, keys} or a 422 detail. */
export function parseFilters(search) {
  const preds = [];
  for (const [name, value] of search) {
    if (!['attr_min', 'attr_max', 'attr_set', 'attr_not_set'].includes(name)) continue;
    if (name === 'attr_min' || name === 'attr_max') {
      const at = value.indexOf('=');
      if (at < 0) return { error: { code: 'attribute_filter_invalid', errors: { [name]: ['expected_key_equals_number'] } } };
      const key = value.slice(0, at); const bound = value.slice(at + 1);
      if (!KEY_RE.test(key)) return { error: { code: 'attribute_filter_invalid', errors: { [name]: ['invalid_key'] } } };
      if (!NUM_RE.test(bound)) return { error: { code: 'attribute_filter_invalid', errors: { [key]: ['expected_finite_json_number'] } } };
      preds.push({ op: name, key, bound: Number(bound) });
    } else {
      if (!KEY_RE.test(value)) return { error: { code: 'attribute_filter_invalid', errors: { [name]: ['invalid_key'] } } };
      preds.push({ op: name, key: value });
    }
  }
  const keys = [...new Set(preds.map((p) => p.key))].sort();
  for (const key of keys) {
    const def = DEFS.find((d) => d.key === key);
    if (!def) return { error: { code: 'attribute_filter_unknown_key', errors: { [key]: ['unknown_key'] } } };
    if (preds.some((p) => p.key === key && (p.op === 'attr_min' || p.op === 'attr_max')) && def.type !== 'numeric') {
      return { error: { code: 'attribute_filter_not_numeric', errors: { [key]: ['numeric_definition_required'] } } };
    }
    const min = preds.find((p) => p.key === key && p.op === 'attr_min');
    const max = preds.find((p) => p.key === key && p.op === 'attr_max');
    if (min && max && min.bound > max.bound) return { error: { code: 'attribute_filter_inverted_range', errors: { [key]: ['min_exceeds_max'] } } };
  }
  return { preds, keys };
}

function admits(task, search, preds) {
  if (task.parent_task_id) return false;
  const status = search.get('status');
  const agent = search.get('assigned_agent');
  if (status && task.status !== status) return false;
  if (agent && task.assigned_agent !== agent) return false;
  return preds.every((p) => {
    const has = Object.prototype.hasOwnProperty.call(task.values, p.key);
    if (p.op === 'attr_set') return has;
    if (p.op === 'attr_not_set') return !has;
    if (!has || typeof task.values[p.key] !== 'number') return false;
    return p.op === 'attr_min' ? task.values[p.key] >= p.bound : task.values[p.key] <= p.bound;
  });
}
const project = (task, keys) => {
  const { values, ...rest } = task;
  return { ...rest, attribute_values: Object.fromEntries(keys.map((k) => [k, Object.prototype.hasOwnProperty.call(values, k) ? { present: true, value: values[k] } : { present: false }])) };
};
function page(rows, search, preds, limit) {
  const matched = rows.filter((task) => admits(task, search, preds));
  const before = search.get('before');
  const start = before ? matched.findIndex((t) => t.task_id === before) + 1 : 0;
  const slice = before && start === 0 ? [] : matched.slice(start, start + limit);
  return { slice, full: slice.length === limit };
}

export function createTaskAttrListFixture(org) {
  const prefix = `/api/v1/orgs/${org}`;
  const rows = buildRoots();
  const fixture = {
    /** Scenario overrides: defs 'ok'|'hang'|'error'; roots {status, code, errors}|null; export {status, body, headers}|null. */
    mode: { defs: 'ok', roots: null, exportResponse: null, rootsDelayMs: 0 },
    hung: [],
    reset() { this.mode = { defs: 'ok', roots: null, exportResponse: null, rootsDelayMs: 0 }; },
    release() { for (const r of this.hung.splice(0)) { try { r.end(); } catch { /* closed */ } } },
    handle(request, response, path, url, row) {
      const send = (status, body, headers = {}) => {
        row.status = status;
        response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers });
        response.end(typeof body === 'string' ? body : JSON.stringify(body));
      };
      if (path === `${prefix}/task-attributes/definitions`) {
        if (this.mode.defs === 'hang') { this.hung.push(response); return true; }
        if (this.mode.defs === 'error') { send(503, { detail: { code: 'attribute_storage_unavailable' } }); return true; }
        send(200, { definitions: DEFS.map(({ _order, ...d }) => d), editability: EDITABILITY });
        return true;
      }
      if (path === `${prefix}/tasks/roots` || path === `${prefix}/tasks/roots/export`) {
        const isExport = path.endsWith('/export');
        if (isExport && this.mode.exportResponse) {
          const r = typeof this.mode.exportResponse === 'function' ? this.mode.exportResponse(row) : this.mode.exportResponse;
          if (r.hang) { this.hung.push(response); return true; }
          send(r.status, r.body, r.headers); return true;
        }
        if (!isExport && this.mode.roots && (!this.mode.roots.when || this.mode.roots.when(url.searchParams))) {
          send(this.mode.roots.status, { detail: { code: this.mode.roots.code, ...(this.mode.roots.errors ? { errors: this.mode.roots.errors } : {}) } });
          return true;
        }
        const parsed = parseFilters(url.searchParams);
        if (parsed.error) { send(422, { detail: parsed.error }); return true; }
        const limit = Number(url.searchParams.get('limit') || 20);
        const result = page(rows, url.searchParams, parsed.preds, limit);
        if (!isExport) {
          const body = { tasks: result.slice.map((t) => project(t, parsed.keys)), next_cursor: result.full ? result.slice.at(-1).task_id : null };
          const go = () => send(200, body);
          if (this.mode.rootsDelayMs) setTimeout(go, this.mode.rootsDelayMs); else go();
          return true;
        }
        const selected = [...new Set([...DEFS.filter((d) => !d.archived).map((d) => d.key), ...parsed.keys])].sort();
        const format = url.searchParams.get('format') || 'json';
        const headers = {
          'content-disposition': `attachment; filename="task-roots-${org}-20261125T120000Z.${format}"; filename*=UTF-8''task-roots-${org}-20261125T120000Z.${format}`,
          'x-happyranch-export-limit': String(limit),
          'x-happyranch-page-full': result.full ? 'true' : 'false',
          ...(result.full ? { 'x-happyranch-next-cursor': result.slice.at(-1).task_id } : {}),
        };
        const tasks = result.slice.map((t) => ({ task_id: t.task_id, brief_excerpt: t.brief.slice(0, 200), status: t.status, assigned_agent: t.assigned_agent, created_at: t.created_at, attribute_values: project(t, selected).attribute_values }));
        if (format === 'csv') {
          const cell = (v) => { const s = String(v); const n = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s; return /[",\r\n]/.test(n) ? `"${n.replace(/"/g, '""')}"` : n; };
          const lines = [['task_id', 'brief_excerpt', 'status', 'assigned_agent', 'created_at', ...selected.map((k) => `attribute:${k}`)].join(',')];
          for (const t of tasks) lines.push([t.task_id, t.brief_excerpt, t.status, t.assigned_agent ?? '', t.created_at, ...selected.map((k) => (t.attribute_values[k].present ? t.attribute_values[k].value : ''))].map(cell).join(','));
          send(200, `${lines.join('\r\n')}\r\n`, { ...headers, 'content-type': 'text/csv; charset=utf-8' });
          return true;
        }
        send(200, {
          format: 'happyranch.task-roots-export.v1',
          columns: ['task_id', 'brief_excerpt', 'status', 'assigned_agent', 'created_at', ...selected.map((k) => `attribute:${k}`)],
          attribute_definitions: selected.map((k) => { const d = DEFS.find((x) => x.key === k); return { key: k, definition_id: d.definition_id, version: 1, type: d.type, name: d.name, archived: d.archived }; }),
          tasks, limit, page_full: result.full, next_cursor: result.full ? result.slice.at(-1).task_id : null,
        }, headers);
        return true;
      }
      return false;
    },
  };
  return fixture;
}

const q = JSON.stringify;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const FORBIDDEN_EN = ['history', 'initial', 'original', 'total', 'cutoff', 'as of', 'complete', 'all matching'];
const FORBIDDEN_ZH = ['历史', '初始', '原始', '总数', '截止', '截至', '完整', '全部匹配'];

/** Browser-side spies: anchor download names and object-URL creation. */
const SPIES = `(() => {
  window.__downloads = []; window.__objectUrls = 0;
  const create = URL.createObjectURL.bind(URL);
  URL.createObjectURL = (blob) => { window.__objectUrls += 1; window.__lastBlob = blob; return create(blob); };
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () { if (this.download) { window.__downloads.push(this.download); return; } return click.call(this); };
})();`;

export async function runTaskAttrListCases(h, { org, only }) {
  const { openPage, closePage, evaluate, waitTrue, clickSrc, check, beginCase, endCase, capture, tr, base, seedLocale, chineseNavigator, ledger, fixture, crossTabSwitch, cdp } = h;
  const tasksUrl = (search = '') => `${base}/orgs/${org}/tasks${search}`;
  const rootsPath = `/api/v1/orgs/${org}/tasks/roots`;
  const exportPath = `${rootsPath}/export`;
  const defsPath = `/api/v1/orgs/${org}/task-attributes/definitions`;
  const run = (id) => !only || only.split(',').includes(id);
  const sp = (row) => new URLSearchParams(row.search);
  const attrs = (row) => [...sp(row)].filter(([k]) => k.startsWith('attr_'));
  const rootsRows = (from) => ledger.slice(from).filter((r) => r.path === rootsPath);
  const ordinaryRows = (from) => rootsRows(from).filter((r) => sp(r).get('limit') === '50' && !isAttention(r));
  // The attention owner is the filter-free status=escalated traversal (no agent, no attr).
  const isAttention = (r) => sp(r).get('status') === 'escalated' && attrs(r).length === 0 && !sp(r).has('assigned_agent');
  const exportRows = (from) => ledger.slice(from).filter((r) => r.path === exportPath);
  const open = async (search, { locale = 'en', width = 1440 } = {}) => {
    const page = await openPage(tasksUrl(search), { init: `${seedLocale(locale)}\n${chineseNavigator}\n${SPIES}`, width, height: width === 390 ? 844 : 900 });
    await waitTrue(page, `document.documentElement.lang === ${q(locale)}`, `lang ${locale}`, 8000);
    return page;
  };
  const text = (page) => evaluate(page, 'document.body.innerText');
  const rowIds = (page) => evaluate(page, `[...document.querySelectorAll('[data-tasks-responsive-list] section li a')].map(a => a.getAttribute('href').split('/').pop())`);
  const overflow = (page) => evaluate(page, 'document.documentElement.scrollWidth > document.documentElement.clientWidth');
  const settle = async (page, expr, label, ms = 10000) => { const ok = await waitTrue(page, expr, label, ms); await pause(300); return ok; };
  const hasText = (s) => `document.body.innerText.includes(${q(s)})`;
  // Rendered (non-hidden, laid-out) attention-scope notes and their text.
  const visibleScopeNotes = `[...document.querySelectorAll('[data-testid="tasks-attention-scope"]')].filter((e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden').map((e) => e.innerText.trim())`;
  const viewports = [1440, 390];
  const locales = ['en', 'zh-CN'];
  const F05 = '?status=in_progress&agent=agent-a&attr_min=fx_delta%3D0&attr_set=comment&attr_set=ready&attr_set=risk';
  const exportBtn = `document.querySelector('[data-testid="tasks-export-open"]')`;

  // ---------------------------------------------------------------- F05
  if (run('F05')) {
    beginCase('F05', 'multi-key AND + row values + paging + chip removal resets cursor (Fixture B; fx_delta)');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset();
      const from = ledger.length;
      const page = await open(F05, { locale, width });
      await settle(page, `document.querySelectorAll('[data-testid="task-row-attribute-values"]').length > 0`, 'row values');
      const owners = ordinaryRows(from);
      check(`F05 ${locale} ${width} only the in_progress ordinary owner requests`, [...new Set(owners.map((r) => sp(r).get('status')))], ['in_progress']);
      check(`F05 ${locale} ${width} owner params`, owners.length ? { agent: sp(owners[0]).get('assigned_agent'), attrs: attrs(owners[0]) } : null,
        { agent: 'agent-a', attrs: [['attr_min', 'fx_delta=0'], ['attr_set', 'comment'], ['attr_set', 'ready'], ['attr_set', 'risk']] });
      check(`F05 ${locale} ${width} raw query keeps %3D`, owners[0]?.search.includes('attr_min=fx_delta%3D0'), true);
      const ids = await rowIds(page);
      check(`F05 ${locale} ${width} M1 admitted`, ids.includes('TASK-M1'), true);
      check(`F05 ${locale} ${width} D1-D7 and child excluded`, ids.filter((id) => /^TASK-D/.test(id)), []);
      const m1 = await evaluate(page, `(() => { const a = [...document.querySelectorAll('[data-tasks-responsive-list] section li')].find(li => li.querySelector('a[href$="/TASK-M1"]')); const v = a && a.querySelector('[data-testid="task-row-attribute-values"]'); return v ? { inline: [...v.children].filter(c => c.tagName === 'SPAN').map(c => c.innerText.trim()), more: v.querySelector('summary')?.innerText.trim() ?? null } : null; })()`);
      check(`F05 ${locale} ${width} M1 inline values sorted comment/fx_delta/ready`, m1?.inline, [
        `Comment (comment) ${tr(locale, 'tasks.filters.attr.emptyString')}`, 'Delta (fx_delta) 0', `Ready (ready) ${tr(locale, 'tasks.filters.attr.false')}`]);
      check(`F05 ${locale} ${width} disclosure label`, m1?.more, tr(locale, 'tasks.filters.attr.showMore', { n: 1 }));
      check(`F05 ${locale} ${width} unfiltered key effort absent from rows`, (await text(page)).includes('Effort (effort)'), false);
      check(`F05 ${locale} ${width} no horizontal overflow`, await overflow(page), false);
      await capture(page, `f05-${locale}-${width}`, { case: 'F05', locale, viewport: width });
      if (locale === 'en' && width === 1440) {
        // Disclosure is keyboard-operable: focus summary + Enter.
        await evaluate(page, `(() => { const s = document.querySelector('a[href$="/TASK-M1"]').closest('li').querySelector('summary'); s.focus(); return true; })()`);
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' }, page.sessionId);
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 }, page.sessionId);
        await pause(300);
        check('F05 disclosure opens by keyboard and reveals risk 2', await evaluate(page, `(() => { const d = document.querySelector('a[href$="/TASK-M1"]').closest('li').querySelector('details'); return !!d && d.open && d.innerText.includes('Risk (risk) 2'); })()`), true);
        // Page 2: scroll to the sentinel.
        const p2From = ledger.length;
        await evaluate(page, `(() => { document.querySelector('[data-tasks-responsive-list]').parentElement.lastElementChild.scrollIntoView(); window.scrollTo(0, document.body.scrollHeight); document.querySelectorAll('main, [class*="overflow"]').forEach(e => { e.scrollTop = e.scrollHeight; }); return true; })()`);
        await waitTrue(page, 'false', 'page 2 settle', 2500);
        const p2 = ordinaryRows(p2From).filter((r) => sp(r).has('before'));
        check('F05 page-2 GET carries same status/agent/attr + before', p2.length ? { status: sp(p2[0]).get('status'), agent: sp(p2[0]).get('assigned_agent'), attrs: attrs(p2[0]), before: !!sp(p2[0]).get('before') } : 'no page-2 request',
          { status: 'in_progress', agent: 'agent-a', attrs: [['attr_min', 'fx_delta=0'], ['attr_set', 'comment'], ['attr_set', 'ready'], ['attr_set', 'risk']], before: true });
        await capture(page, 'f05-en-1440-page2', { case: 'F05', locale, viewport: width, state: 'after page 2' });
        // Remove the ready chip.
        const rmFrom = ledger.length;
        await clickSrc(page, `[...document.querySelectorAll('[data-testid="tasks-attr-chips"] button')].find(b => b.getAttribute('aria-label').includes('Ready'))`);
        await settle(page, `!location.search.includes('attr_set=ready')`, 'chip removed');
        await waitTrue(page, 'false', 'refetch settle', 1500);
        const first = ordinaryRows(rmFrom)[0];
        check('F05 after chip removal: first GET has no before and no attr_set=ready', first ? { before: sp(first).has('before'), ready: attrs(first).some(([k, v]) => k === 'attr_set' && v === 'ready') } : 'no request', { before: false, ready: false });
        check('F05 chip removal URL', new URLSearchParams(await evaluate(page, 'location.search')).getAll('attr_set'), ['comment', 'risk']);
      }
      await closePage(page);
    }
    endCase();
  }

  // ---------------------------------------------------------------- F01 + F08 (form)
  if (run('F01')) {
    beginCase('F01', 'form Apply: effort >= 3 AND effort <= 7 -> canonical URL, every ordinary owner carries attr; F08 invalid draft sends nothing');
    for (const width of viewports) {
      fixture.reset();
      const page = await open('', { width });
      await settle(page, `document.querySelectorAll('[data-tasks-responsive-list] section li').length > 0`, 'rows');
      await clickSrc(page, `[...document.querySelectorAll('button[aria-expanded]')].find(b => b.innerText.includes(${q(tr('en', 'tasks.page.filter'))}))`);
      await settle(page, `!!document.querySelector('[data-testid="tasks-attr-clauses"]')`, 'clauses');
      const add = `[...document.querySelectorAll('[data-testid="tasks-attr-clauses"] button')].find(b => b.innerText.includes(${q(tr('en', 'tasks.filters.attr.addCondition'))}))`;
      const setSel = (i, sel, value) => evaluate(page, `(() => { const s = document.querySelectorAll('.tasks-attr-clause ${sel}')[${i}]; const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set; set.call(s, ${q(value)}); s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
      const setVal = (i, value) => evaluate(page, `(() => { const s = document.querySelectorAll('.tasks-attr-clause input')[${i}]; const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(s, ${q(value)}); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
      await clickSrc(page, add); await pause(150);
      await setSel(0, '.tasks-attr-key select', 'effort'); await pause(100);
      await setSel(0, '.tasks-attr-op select', 'ge'); await pause(100);
      // F08: empty value -> field error, no request, URL unchanged.
      const invFrom = ledger.length;
      const urlBefore = await evaluate(page, 'location.search');
      await clickSrc(page, `[...document.querySelectorAll('form button[type="submit"]')][0]`);
      await pause(800);
      check(`F08 ${width} empty value shows field error`, await evaluate(page, hasText(tr('en', 'tasks.filters.attr.invalid', { name: 'Effort' }))), true);
      check(`F08 ${width} no request on invalid Apply`, ledger.slice(invFrom).filter((r) => r.path.startsWith('/api/')).map((r) => r.path), []);
      check(`F08 ${width} URL unchanged`, await evaluate(page, 'location.search'), urlBefore);
      if (width === 1440) await capture(page, 'f08-en-1440-invalid', { case: 'F08', locale: 'en', viewport: width });
      await setVal(0, '01'); await pause(100);
      await clickSrc(page, `[...document.querySelectorAll('form button[type="submit"]')][0]`); await pause(500);
      check(`F08 ${width} leading-zero bound refused client-side`, await evaluate(page, hasText(tr('en', 'tasks.filters.attr.invalidNumber', { name: 'Effort' }))), true);
      await setVal(0, '3'); await pause(100);
      await clickSrc(page, add); await pause(150);
      await setSel(1, '.tasks-attr-key select', 'effort'); await pause(100);
      await setSel(1, '.tasks-attr-op select', 'le'); await pause(100);
      await setVal(1, '7'); await pause(100);
      if (width === 390) await capture(page, 'f01-en-390-form', { case: 'F01', locale: 'en', viewport: width, state: 'draft' });
      const from = ledger.length;
      await clickSrc(page, `[...document.querySelectorAll('form button[type="submit"]')][0]`);
      await settle(page, `location.search.includes('attr_min')`, 'applied');
      await waitTrue(page, 'false', 'fetch settle', 1500);
      check(`F01 ${width} canonical URL`, await evaluate(page, 'location.search'), '?attr_min=effort%3D3&attr_max=effort%3D7');
      const owners = ordinaryRows(from);
      check(`F01 ${width} all 7 ordinary owners carry the clause`, [...new Set(owners.map((r) => sp(r).get('status')))].sort(), ['cancelled', 'completed', 'escalated', 'failed', 'in_progress', 'pending', 'superseded'].sort());
      check(`F01 ${width} every ordinary owner attr`, [...new Set(owners.map((r) => JSON.stringify(attrs(r))))], [q([['attr_min', 'effort=3'], ['attr_max', 'effort=7']])]);
      check(`F01 ${width} chips text`, await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-attr-chips"] > span > b')].map(b => b.innerText)`), ['Effort ≥ 3', 'Effort ≤ 7']);
      check(`F01 ${width} rows show Effort (effort) 4`, await evaluate(page, hasText('Effort (effort) 4')), true);
      check(`F01 ${width} no horizontal overflow`, await overflow(page), false);
      await capture(page, `f01-en-${width}`, { case: 'F01', locale: 'en', viewport: width });
      await closePage(page);
    }
    endCase();
  }

  // ---------------------------------------------------------------- F-CLEAR-DRAFT (round-1 review LOW)
  if (run('F-CLEAR-DRAFT')) {
    beginCase('F-CLEAR-DRAFT', 'Clear on an unfiltered URL resets the status + attribute drafts; a later Apply stays unfiltered');
    fixture.reset();
    const page = await open('');
    await settle(page, `document.querySelectorAll('[data-tasks-responsive-list] section li').length > 0`, 'rows');
    await clickSrc(page, `[...document.querySelectorAll('button[aria-expanded]')].find(b => b.innerText.includes(${q(tr('en', 'tasks.page.filter'))}))`);
    await settle(page, `!!document.querySelector('[data-testid="tasks-attr-clauses"]')`, 'clauses');
    await clickSrc(page, `document.querySelector('[data-testid="tasks-status-set"] input[value="pending"]')`); await pause(100);
    await clickSrc(page, `[...document.querySelectorAll('[data-testid="tasks-attr-clauses"] button')].find(b => b.innerText.includes(${q(tr('en', 'tasks.filters.attr.addCondition'))}))`); await pause(150);
    await evaluate(page, `(() => { const s = document.querySelector('.tasks-attr-clause .tasks-attr-key select'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, 'effort'); s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`); await pause(100);
    await evaluate(page, `(() => { const s = document.querySelector('.tasks-attr-clause input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, '3'); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`); await pause(100);
    const draftState = `({ clauses: document.querySelectorAll('.tasks-attr-clause').length, statuses: [...document.querySelectorAll('[data-testid="tasks-status-set"] input:checked')].map(e => e.value), search: location.search })`;
    check('F-CLEAR-DRAFT draft precondition (pending + one clause, URL unfiltered)', await evaluate(page, draftState), { clauses: 1, statuses: ['pending'], search: '' });
    await clickSrc(page, `[...document.querySelectorAll('form button')].find(b => b.innerText.trim() === ${q(tr('en', 'tasks.filters.clear'))})`); await pause(300);
    check('F-CLEAR-DRAFT Clear empties every draft control; URL stays unfiltered', await evaluate(page, draftState), { clauses: 0, statuses: [], search: '' });
    await capture(page, 'f-clear-draft-en-1440-cleared', { case: 'F-CLEAR-DRAFT', locale: 'en', viewport: 1440, state: 'cleared' });
    const from = ledger.length;
    await clickSrc(page, `[...document.querySelectorAll('form button[type="submit"]')][0]`);
    await waitTrue(page, `location.search !== ''`, 'observe any reactivated draft', 1500);
    check('F-CLEAR-DRAFT Apply after Clear keeps the URL unfiltered', await evaluate(page, 'location.search'), '');
    check('F-CLEAR-DRAFT Apply after Clear sends no attr_* roots request', rootsRows(from).filter((r) => attrs(r).length > 0).map((r) => r.search), []);
    await closePage(page);
    endCase();
  }

  // ---------------------------------------------------------------- URL04
  if (run('URL04')) {
    beginCase('URL04', 'non-canonical hand-edited URL is sent byte-exact and not rewritten on load');
    fixture.reset();
    const from = ledger.length;
    const search = '?attr_set=ready&attr_max=effort%3D9007199254740993&attr_min=risk%3D1e%2B2';
    const page = await open(search);
    await settle(page, `!document.body.innerText.includes(${q(tr('en', 'tasks.list.loading'))})`, 'loaded');
    const owner = ordinaryRows(from)[0];
    check('URL04 request keeps 1e%2B2 and the big integer verbatim', owner ? [owner.search.includes('attr_min=risk%3D1e%2B2'), owner.search.includes('attr_max=effort%3D9007199254740993')] : null, [true, true]);
    check('URL04 request keeps URL order', owner ? attrs(owner).map(([k]) => k) : null, ['attr_set', 'attr_max', 'attr_min']);
    check('URL04 address bar not rewritten', await evaluate(page, 'location.search'), search);
    await closePage(page);
    endCase();
  }

  // ---------------------------------------------------------------- DISC01/02
  if (run('DISC')) {
    beginCase('DISC02', 'definitions 503: chip keeps raw key, list stays filtered, Retry refetches only definitions');
    for (const locale of locales) for (const width of (locale === 'en' ? viewports : [1440])) {
      fixture.reset(); fixture.mode.defs = 'error';
      const from = ledger.length;
      const page = await open('?attr_min=effort%3D9', { locale, width });
      await settle(page, `!!document.querySelector('[data-testid="tasks-attr-chips"]')`, 'chips');
      await clickSrc(page, `[...document.querySelectorAll('button[aria-expanded]')].find(b => b.innerText.includes(${q(tr(locale, 'tasks.page.filter'))}))`);
      await settle(page, hasText(tr(locale, 'tasks.filters.attr.defsError')), 'defs error', 15000);
      check(`DISC02 ${locale} ${width} chip raw key`, await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-attr-chips"] > span > b')].map(b => b.innerText)`), ['effort ≥ 9']);
      check(`DISC02 ${locale} ${width} every ordinary request carries the clause`, ordinaryRows(from).every((r) => attrs(r).length === 1 && attrs(r)[0][1] === 'effort=9') && ordinaryRows(from).length > 0, true);
      check(`DISC02 ${locale} ${width} rows are exactly the filtered set (ESC1 only in attention)`, (await rowIds(page)).filter((id) => id !== 'TASK-ESC1'), []);
      check(`DISC02 ${locale} ${width} no overflow`, await overflow(page), false);
      await capture(page, `disc02-${locale}-${width}-error`, { case: 'DISC02', locale, viewport: width });
      if (locale === 'en' && width === 1440) {
        fixture.mode.defs = 'ok';
        const rFrom = ledger.length;
        await clickSrc(page, `[...document.querySelectorAll('[data-testid="tasks-attr-clauses"] button')].find(b => b.innerText.includes(${q(tr('en', 'tasks.filters.attr.defsRetry'))}))`);
        await settle(page, `!document.body.innerText.includes(${q(tr('en', 'tasks.filters.attr.defsError'))})`, 'retry ok');
        check('DISC02 Retry sends only a definitions GET', [...new Set(ledger.slice(rFrom).map((r) => r.path))], [defsPath]);
        check('DISC02 chip resolves to the definition name', await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-attr-chips"] > span > b')].map(b => b.innerText)`), ['Effort ≥ 9']);
        check('DISC02 URL unchanged', await evaluate(page, 'location.search'), '?attr_min=effort%3D9');
      }
      await closePage(page);
    }
    endCase();
    beginCase('DISC01', 'definitions pending: chip renders raw key and roots GETs already carry the clause');
    const locale = 'en';
    fixture.reset(); fixture.mode.defs = 'hang';
    const from = ledger.length;
    const page = await open('?attr_min=effort%3D3');
    await settle(page, `!!document.querySelector('[data-testid="tasks-attr-chips"]')`, 'chips');
    await clickSrc(page, `[...document.querySelectorAll('button[aria-expanded]')].find(b => b.innerText.includes(${q(tr(locale, 'tasks.page.filter'))}))`); await pause(400);
    check('DISC01 picker shows defsLoading', await evaluate(page, hasText(tr('en', 'tasks.filters.attr.defsLoading'))), true);
    check('DISC01 raw-key chip', await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-attr-chips"] > span > b')].map(b => b.innerText)`), ['effort ≥ 3']);
    check('DISC01 roots GETs carry the clause while definitions pend', ordinaryRows(from).length > 0 && ordinaryRows(from).every((r) => attrs(r)[0]?.[1] === 'effort=3'), true);
    await capture(page, 'disc01-en-1440-pending', { case: 'DISC01', locale: 'en', viewport: 1440 });
    fixture.release();
    await closePage(page);
    endCase();
  }

  // ---------------------------------------------------------------- REF01/REF02/ACC01
  if (run('REF')) {
    beginCase('REF01', 'unknown-key 422 is one refusal alert, not an empty list; export disabled');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset();
      const from = ledger.length;
      const page = await open('?attr_set=no_such_key', { locale, width });
      await settle(page, `!!document.querySelector('[data-testid="tasks-filter-refusal"]')`, 'refusal');
      check(`REF01 ${locale} ${width} one alert naming the key`, await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-filter-refusal"]')].map(e => e.innerText.trim())`), [tr(locale, 'tasks.filters.attr.unavailable', { key: 'no_such_key' })]);
      check(`REF01 ${locale} ${width} no ordinary rows`, (await rowIds(page)).filter((id) => id !== 'TASK-ESC1'), []);
      check(`REF01 ${locale} ${width} no filtered-empty state`, await evaluate(page, hasText(tr(locale, 'tasks.filters.attr.zero'))), false);
      check(`REF01 ${locale} ${width} export disabled`, await evaluate(page, `${exportBtn}.disabled`), true);
      check(`REF01 ${locale} ${width} no overflow`, await overflow(page), false);
      check(`REF01 ${locale} ${width} server answered 422`, ordinaryRows(from).some((r) => r.status === 422), true);
      await capture(page, `ref01-${locale}-${width}`, { case: 'REF01', locale, viewport: width });
      if (locale === 'en' && width === 1440) {
        const eFrom = ledger.length;
        await clickSrc(page, exportBtn).catch(() => undefined); await pause(500);
        check('REF01 clicking disabled Export sends no export request', exportRows(eFrom).length, 0);
      }
      await closePage(page);
    }
    endCase();
    beginCase('REF01b', 'refusal after another owner already loaded rows removes those rows');
    fixture.reset();
    const pageB = await open('?status=in_progress&status=completed&attr_min=effort%3D3');
    await settle(pageB, `document.querySelectorAll('[data-tasks-responsive-list] section li').length > 0`, 'rows');
    check('REF01b in_progress rows loaded first', (await rowIds(pageB)).length > 0, true);
    fixture.mode.roots = { status: 422, code: 'attribute_filter_unknown_key', errors: { effort: ['unknown_key'] }, when: (s) => s.get('status') === 'completed' };
    // Fresh load: in_progress answers 200 with rows, completed answers 422.
    await evaluate(pageB, 'location.reload()').catch(() => undefined);
    await settle(pageB, `!!document.querySelector('[data-testid="tasks-filter-refusal"]')`, 'refusal b');
    check('REF01b refusal shown', await evaluate(pageB, hasText(tr('en', 'tasks.filters.attr.unavailable', { key: 'effort' }))), true);
    check('REF01b no ordinary rows even though in_progress answered 200', (await rowIds(pageB)).filter((id) => id !== 'TASK-ESC1'), []);
    await capture(pageB, 'ref01b-en-1440', { case: 'REF01b', locale: 'en', viewport: 1440 });
    await closePage(pageB);
    endCase();
    beginCase('REF02', 'not-numeric / inverted / unmapped codes each show once');
    for (const [search, key, params, mode] of [
      ['?attr_min=ready%3D1', 'tasks.filters.attr.notNumeric', { key: 'ready' }, null],
      ['?attr_min=effort%3D5&attr_max=effort%3D2', 'tasks.filters.attr.inverted', { key: 'effort' }, null],
      ['?attr_set=effort', 'tasks.filters.attr.refusedOther', { code: 'attribute_filter_future_x' }, { status: 422, code: 'attribute_filter_future_x' }],
      ['?attr_min=effort', 'tasks.filters.attr.invalidUrl', { reason: 'expected_key_equals_number' }, null],
    ]) {
      fixture.reset(); if (mode) fixture.mode.roots = mode;
      const page = await open(search);
      await settle(page, `!!document.querySelector('[data-testid="tasks-filter-refusal"]')`, 'refusal');
      check(`REF02 ${search}`, await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-filter-refusal"]')].map(e => e.innerText.trim())`), [tr('en', key, params)]);
      check(`REF02 ${search} URL not rewritten`, await evaluate(page, 'location.search'), search);
      await closePage(page);
    }
    endCase();
    beginCase('ACC01', 'list 403: one permission alert, no rows, no empty state, export disabled');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset(); fixture.mode.roots = { status: 403, code: 'forbidden', when: (s) => s.has('attr_min') };
      const page = await open('?attr_min=effort%3D3', { locale, width });
      await settle(page, `!!document.querySelector('[data-testid="tasks-filter-refusal"]')`, 'permission');
      check(`ACC01 ${locale} ${width} permission alert`, await evaluate(page, `document.querySelector('[data-testid="tasks-filter-refusal"]').innerText.trim()`), tr(locale, 'tasks.filters.attr.permission'));
      check(`ACC01 ${locale} ${width} no ordinary rows`, (await rowIds(page)).filter((id) => id !== 'TASK-ESC1'), []);
      check(`ACC01 ${locale} ${width} export disabled`, await evaluate(page, `${exportBtn}.disabled`), true);
      check(`ACC01 ${locale} ${width} no overflow`, await overflow(page), false);
      await capture(page, `acc01-${locale}-${width}`, { case: 'ACC01', locale, viewport: width });
      await closePage(page);
    }
    endCase();
  }

  // ---------------------------------------------------------------- ATT01 / EMP01 / LOAD01
  if (run('ATT')) {
    beginCase('ATT01', 'attention traversal stays filter-free; scope note while a clause is applied');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset();
      const from = ledger.length;
      const page = await open('?attr_min=effort%3D3', { locale, width });
      const noteSettled = await settle(page, `${visibleScopeNotes}.length > 0`, 'scope note');
      const att = rootsRows(from).filter((r) => sp(r).get('status') === 'escalated' && sp(r).get('limit') === '50');
      check(`ATT01 ${locale} ${width} one escalated request without attr (attention) and one with (ordinary)`, att.map((r) => attrs(r).length).sort(), [0, 1]);
      check(`ATT01 ${locale} ${width} note visible`, { settled: noteSettled, notes: await evaluate(page, visibleScopeNotes) }, { settled: true, notes: [tr(locale, 'tasks.filters.attr.attentionScope')] });
      check(`ATT01 ${locale} ${width} no overflow`, await overflow(page), false);
      await capture(page, `att01-${locale}-${width}`, { case: 'ATT01', locale, viewport: width });
      await closePage(page);
    }
    fixture.reset();
    const pageR = await open('?attr_min=effort%3D3');
    const before = await settle(pageR, `${visibleScopeNotes}.length > 0`, 'scope note before chip removal');
    await clickSrc(pageR, `document.querySelector('[data-testid="tasks-attr-chips"] button')`);
    const removed = await settle(pageR, `!location.search.includes('attr_min')`, 'chip removed');
    await settle(pageR, `${visibleScopeNotes}.length === 0`, 'scope note removed');
    check('ATT01 note removed when the last clause is cleared', { before, removed, notes: await evaluate(pageR, visibleScopeNotes), text: await evaluate(pageR, hasText(tr('en', 'tasks.filters.attr.attentionScope'))) }, { before: true, removed: true, notes: [], text: false });
    await closePage(pageR);
    fixture.reset();
    const page = await open('');
    const rowsSettled = await settle(page, `document.querySelectorAll('[data-tasks-responsive-list] section li').length > 0`, 'rows');
    check('ATT01 note absent without clauses', { rowsSettled, notes: await evaluate(page, visibleScopeNotes), text: await evaluate(page, hasText(tr('en', 'tasks.filters.attr.attentionScope'))) }, { rowsSettled: true, notes: [], text: false });
    await closePage(page);
    endCase();
    beginCase('EMP01', 'filtered empty: f.zero + body + Clear filters; export enabled');
    for (const locale of locales) {
      fixture.reset();
      const pageE = await open('?status=completed&attr_min=effort%3D10', { locale });
      await settle(pageE, hasText(tr(locale, 'tasks.filters.attr.zero')), 'zero');
      check(`EMP01 ${locale} zero body`, await evaluate(pageE, hasText(tr(locale, 'tasks.filters.attr.zeroBody'))), true);
      check(`EMP01 ${locale} export enabled`, await evaluate(pageE, `${exportBtn}.disabled`), false);
      await capture(pageE, `emp01-${locale}-1440`, { case: 'EMP01', locale, viewport: 1440 });
      await closePage(pageE);
    }
    endCase();
    beginCase('LOAD01', 'loading: skeleton text, no Not set / empty state, export disabled');
    fixture.reset(); fixture.mode.rootsDelayMs = 4000;
    const pageL = await open('?attr_min=effort%3D3');
    await pause(700);
    check('LOAD01 export disabled while loading', await evaluate(pageL, `${exportBtn}.disabled`), true);
    check('LOAD01 no Not set / zero while loading', await evaluate(pageL, `document.body.innerText.includes(${q(tr('en', 'tasks.filters.attr.notSet'))}) || document.body.innerText.includes(${q(tr('en', 'tasks.filters.attr.zero'))})`), false);
    await capture(pageL, 'load01-en-1440', { case: 'LOAD01', locale: 'en', viewport: 1440 });
    await closePage(pageL);
    endCase();
  }

  // ---------------------------------------------------------------- Export
  const openExport = async (page) => {
    await settle(page, `${exportBtn} && !${exportBtn}.disabled`, 'export enabled', 12000);
    await clickSrc(page, exportBtn);
    await settle(page, `!!document.querySelector('[data-testid="tasks-export-dialog"]')`, 'dialog');
  };
  const dialogText = (page) => evaluate(page, `document.querySelector('[data-testid="tasks-export-dialog"]').innerText`);
  const clickDialog = (page, label) => clickSrc(page, `[...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].find(b => b.innerText.trim().endsWith(${q(label)}))`);
  const copyHits = (s, locale) => (locale === 'en' ? FORBIDDEN_EN.filter((w) => s.toLowerCase().includes(w)) : FORBIDDEN_ZH.filter((w) => s.includes(w)));
  const copyLog = [];

  if (run('EXP')) {
    beginCase('EXP01', 'JSON export sends the visible list predicates + limit=1000; receipt; Content-Disposition filename');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset();
      const page = await open(F05, { locale, width });
      await openExport(page);
      const summary = await dialogText(page);
      copyLog.push([locale, 'summary', summary]);
      check(`EXP01 ${locale} ${width} summary lists scope/status/agent/clauses`, [summary.includes(tr(locale, 'tasks.export.scopeRoots')), summary.includes('agent-a'), summary.includes('Delta ≥ 0')], [true, true, true]);
      check(`EXP01 ${locale} ${width} default limit 1000`, await evaluate(page, `document.getElementById('tasks-export-limit').value`), '1000');
      if (width === 1440) await capture(page, `exp01-${locale}-${width}-summary`, { case: 'EXP01', locale, viewport: width, state: 'summary' });
      const from = ledger.length;
      await clickDialog(page, tr(locale, 'tasks.export.export'));
      await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt');
      const ex = exportRows(from);
      check(`EXP01 ${locale} ${width} exactly one export request, no extra roots reads`, [ex.length, rootsRows(from).length], [1, 0]);
      check(`EXP01 ${locale} ${width} export params`, ex[0] ? [...sp(ex[0])] : null, [['format', 'json'], ['limit', '1000'], ['status', 'in_progress'], ['assigned_agent', 'agent-a'], ['attr_min', 'fx_delta=0'], ['attr_set', 'comment'], ['attr_set', 'ready'], ['attr_set', 'risk']]);
      check(`EXP01 ${locale} ${width} receipt`, (await evaluate(page, `document.querySelector('[data-testid="tasks-export-receipt"]').innerText.trim()`)), tr(locale, 'tasks.export.receipt', { count: 52, fmt: 'JSON' }));
      const ready = await dialogText(page);
      copyLog.push([locale, 'ready', ready]);
      await clickDialog(page, tr(locale, 'tasks.export.download')); await pause(300);
      check(`EXP01 ${locale} ${width} download name from Content-Disposition`, await evaluate(page, 'window.__downloads'), [`task-roots-${org}-20261125T120000Z.json`]);
      check(`EXP01 ${locale} ${width} no overflow`, await overflow(page), false);
      await capture(page, `exp01-${locale}-${width}-ready`, { case: 'EXP01', locale, viewport: width, state: 'ready' });
      await closePage(page);
    }
    endCase();

    beginCase('EXP02', 'CSV export: .csv name, bytes are the server body (no BOM, CRLF), formula cells as served');
    fixture.reset();
    {
      const page = await open('?status=pending&attr_set=comment');
      await openExport(page);
      await clickSrc(page, `document.querySelector('input[name="tasks-export-format"][value="csv"]')`); await pause(200);
      const from = ledger.length;
      await clickDialog(page, tr('en', 'tasks.export.export'));
      await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt');
      check('EXP02 format=csv sent', exportRows(from)[0] ? sp(exportRows(from)[0]).get('format') : null, 'csv');
      await clickDialog(page, tr('en', 'tasks.export.download')); await pause(300);
      check('EXP02 csv name', await evaluate(page, 'window.__downloads'), [`task-roots-${org}-20261125T120000Z.csv`]);
      const bytes = await evaluate(page, `window.__lastBlob.text().then(t => ({ bom: t.charCodeAt(0) === 0xFEFF, header: t.split('\\r\\n')[0], crlf: t.includes('\\r\\n'), html: t.includes('<img src=x onerror=alert(1)>') }))`);
      check('EXP02 bytes', bytes, { bom: false, header: 'task_id,brief_excerpt,status,assigned_agent,created_at,attribute:comment,attribute:effort,attribute:fx_delta,attribute:ready,attribute:risk', crlf: true, html: true });
      check('EXP02 receipt counts CSV records', await evaluate(page, `document.querySelector('[data-testid="tasks-export-receipt"]').innerText.trim()`), tr('en', 'tasks.export.receipt', { count: 3, fmt: 'CSV' }));
      await closePage(page);
    }
    endCase();

    beginCase('EXP09', 'status mapping: all -> no status; one -> status; two -> disabled + statusScope + no request');
    for (const locale of locales) {
      fixture.reset();
      const pageC = await open('?status=in_progress&status=completed&attr_min=effort%3D3', { locale });
      await settle(pageC, `document.querySelectorAll('[data-tasks-responsive-list] section li').length > 0`, 'rows');
      check(`EXP09 ${locale} two statuses: export disabled`, await evaluate(pageC, `${exportBtn}.disabled`), true);
      check(`EXP09 ${locale} statusScope note`, await evaluate(pageC, `document.querySelector('[data-testid="tasks-export-status-scope"]')?.innerText.trim()`), tr(locale, 'tasks.export.statusScope'));
      const from = ledger.length;
      await clickSrc(pageC, exportBtn).catch(() => undefined); await pause(400);
      check(`EXP09 ${locale} no export request`, exportRows(from).length, 0);
      await capture(pageC, `exp09-${locale}-1440-two-status`, { case: 'EXP09', locale, viewport: 1440 });
      await closePage(pageC);
    }
    {
      const pageA = await open('?attr_min=effort%3D3');
      await openExport(pageA);
      const from = ledger.length;
      await clickDialog(pageA, tr('en', 'tasks.export.export'));
      await settle(pageA, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt');
      check('EXP09 all statuses: no status param (shared all-status link)', exportRows(from)[0] ? sp(exportRows(from)[0]).has('status') : null, false);
      await closePage(pageA);
    }
    endCase();

    beginCase('CAP01', 'row limit 1-1000: invalid shows limitInvalid with no request; forced 422 shows refused');
    {
      fixture.reset();
      const page = await open('?attr_min=effort%3D3');
      await openExport(page);
      for (const bad of ['1001', '0', '1.5']) {
        await evaluate(page, `(() => { const s = document.getElementById('tasks-export-limit'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, ${q(bad)}); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
        const from = ledger.length;
        await clickDialog(page, tr('en', 'tasks.export.export')); await pause(400);
        check(`CAP01 limit ${bad} refused client-side`, [await evaluate(page, hasText(tr('en', 'tasks.export.limitInvalid'))), exportRows(from).length], [true, 0]);
      }
      fixture.mode.exportResponse = { status: 422, body: { detail: { code: 'task_export_invalid', errors: { limit: ['integer_1_1000'] } } } };
      await evaluate(page, `(() => { const s = document.getElementById('tasks-export-limit'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, '1000'); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
      const from = ledger.length;
      await clickDialog(page, tr('en', 'tasks.export.export'));
      await settle(page, hasText(tr('en', 'tasks.export.refused', { reason: 'integer_1_1000' })), 'refused');
      check('CAP01 limit=1000 sent, forced 422 shown, no file', [exportRows(from)[0] && sp(exportRows(from)[0]).get('limit'), await evaluate(page, 'window.__objectUrls')], ['1000', 0]);
      await capture(page, 'cap01-en-1440-refused', { case: 'CAP01', locale: 'en', viewport: 1440 });
      await closePage(page);
    }
    endCase();

    beginCase('CAP02', 'page full: receipt + moreMayMatch; next page repeats filters + limit with before=cursor');
    for (const locale of locales) for (const width of viewports) {
      fixture.reset();
      const page = await open('?status=in_progress&attr_min=effort%3D5', { locale, width });
      await openExport(page);
      await evaluate(page, `(() => { const s = document.getElementById('tasks-export-limit'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, '2'); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
      const from = ledger.length;
      await clickDialog(page, tr(locale, 'tasks.export.export'));
      await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt');
      const receipt = await evaluate(page, `document.querySelector('[data-testid="tasks-export-receipt"]').innerText`);
      check(`CAP02 ${locale} ${width} receipt + more-may-match`, receipt.split('\n').map((s) => s.trim()).filter(Boolean), [tr(locale, 'tasks.export.receipt', { count: 2, fmt: 'JSON' }), tr(locale, 'tasks.export.moreMayMatch')]);
      copyLog.push([locale, 'pageFull', await dialogText(page)]);
      check(`CAP02 ${locale} ${width} no overflow`, await overflow(page), false);
      await capture(page, `cap02-${locale}-${width}`, { case: 'CAP02', locale, viewport: width });
      const first = exportRows(from)[0];
      const cursor = first ? 'TASK-P49' : null;
      const nFrom = ledger.length;
      await clickDialog(page, tr(locale, 'tasks.export.nextPage'));
      await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt 2');
      await pause(300);
      const next = exportRows(nFrom)[0];
      check(`CAP02 ${locale} ${width} next page = same params + before`, next ? { same: [...sp(next)].filter(([k]) => k !== 'before').join('&') === [...sp(first)].join('&'), before: sp(next).get('before') !== null } : null, { same: true, before: true });
      check(`CAP02 ${locale} ${width} cursor fixture sanity`, !!cursor, true);
      await closePage(page);
    }
    endCase();

    beginCase('CAP03', 'not full: receipt only, no more-may-match, no next page');
    {
      fixture.reset();
      const page = await open('?status=pending&attr_set=comment');
      await openExport(page);
      await clickDialog(page, tr('en', 'tasks.export.export'));
      await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt');
      check('CAP03 no moreMayMatch / next', [await evaluate(page, hasText(tr('en', 'tasks.export.moreMayMatch'))), await evaluate(page, hasText(tr('en', 'tasks.export.nextPage')))], [false, false]);
      copyLog.push(['en', 'notFull', await dialogText(page)]);
      await closePage(page);
    }
    endCase();

    beginCase('CAP04-05', '413 too_many_attribute_columns / encoded_body_too_large: message, no Download, no Blob URL');
    for (const [reason, key] of [['too_many_attribute_columns', 'tasks.export.tooManyColumns'], ['encoded_body_too_large', 'tasks.export.bodyTooLarge']]) for (const locale of locales) {
      fixture.reset(); fixture.mode.exportResponse = { status: 413, body: { detail: { code: 'task_export_too_large', errors: { export: [reason] } } } };
      const page = await open('?attr_min=effort%3D3', { locale });
      await openExport(page);
      await clickDialog(page, tr(locale, 'tasks.export.export'));
      await settle(page, hasText(tr(locale, key)), reason);
      check(`CAP ${reason} ${locale} message, no download/objectURL`, [await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].some(b => b.innerText.trim().endsWith(${q(tr(locale, 'tasks.export.download'))}))`), await evaluate(page, 'window.__objectUrls')], [false, 0]);
      copyLog.push([locale, reason, await dialogText(page)]);
      await capture(page, `cap-${reason}-${locale}-1440`, { case: 'CAP04-05', locale, viewport: 1440 });
      await closePage(page);
    }
    endCase();

    beginCase('ACC02-EXP06', 'export 403 / 401x2 / 503: own copy, no file; 401 is sent twice');
    for (const [label, response, key, sends] of [
      ['403', { status: 403, body: { detail: { code: 'forbidden' } } }, 'tasks.export.forbidden', 1],
      ['401', { status: 401, body: { detail: 'unauthorized' } }, 'tasks.export.permLost', 2],
      ['503', { status: 503, body: { detail: { code: 'unavailable' } } }, 'tasks.export.failed', null],
    ]) for (const locale of locales) {
      fixture.reset(); fixture.mode.exportResponse = response;
      const page = await open('?attr_min=effort%3D3', { locale });
      await openExport(page);
      const from = ledger.length;
      await clickDialog(page, tr(locale, 'tasks.export.export'));
      await settle(page, hasText(tr(locale, key)), label, 20000);
      check(`${label} ${locale} message`, await evaluate(page, hasText(tr(locale, key))), true);
      check(`${label} ${locale} no download/objectURL`, [await evaluate(page, 'window.__downloads.length'), await evaluate(page, 'window.__objectUrls')], [0, 0]);
      if (sends !== null) check(`${label} ${locale} export sends`, exportRows(from).length, sends);
      copyLog.push([locale, label, await dialogText(page)]);
      if (locale === 'en') await capture(page, `exp-${label}-en-1440`, { case: 'ACC02-EXP06', locale, viewport: 1440 });
      await closePage(page);
    }
    endCase();

    beginCase('EXP04', 'double-click guard: one request while preparing');
    {
      fixture.reset(); fixture.mode.exportResponse = { hang: true };
      const page = await open('?attr_min=effort%3D3');
      await openExport(page);
      const from = ledger.length;
      await evaluate(page, `(() => { const b = [...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].find(x => x.innerText.trim().endsWith(${q(tr('en', 'tasks.export.export'))})); b.click(); b.click(); return true; })()`);
      await pause(600);
      check('EXP04 one request + disabled while preparing', [exportRows(from).length, await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].some(x => x.disabled)`)], [1, true]);
      // EXP08: close while preparing; a late response never shows a receipt.
      await clickDialog(page, tr('en', 'tasks.export.cancel')); await pause(300);
      fixture.release(); await pause(400);
      check('EXP08 closed while preparing: no dialog, no receipt', await evaluate(page, `!document.querySelector('[data-testid="tasks-export-dialog"]') && !document.querySelector('[data-testid="tasks-export-receipt"]')`), true);
      await closePage(page);
    }
    endCase();
  }

  // ---------------------------------------------------------------- EXP-CONT-413-LOWER (round-1 review MEDIUM)
  if (run('EXP-CONT-413-LOWER')) {
    beginCase('EXP-CONT-413-LOWER', 'continuation-page 413, then a lower row limit: Retry repeats the FAILED page (before=<failed cursor>, new limit), never page 1');
    fixture.reset();
    const page = await open('?status=in_progress&attr_min=effort%3D5');
    await openExport(page);
    const setLimit = (value) => evaluate(page, `(() => { const s = document.getElementById('tasks-export-limit'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, ${q(value)}); s.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
    await setLimit('2');
    const firstFrom = ledger.length;
    await clickDialog(page, tr('en', 'tasks.export.export'));
    await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt page 1');
    const first = exportRows(firstFrom)[0];
    fixture.mode.exportResponse = { status: 413, body: { detail: { code: 'task_export_too_large', errors: { export: ['encoded_body_too_large'] } } } };
    const failedFrom = ledger.length;
    await clickDialog(page, tr('en', 'tasks.export.nextPage'));
    await settle(page, hasText(tr('en', 'tasks.export.bodyTooLarge')), '413 shown');
    const failed = exportRows(failedFrom)[0];
    const failedBefore = failed ? sp(failed).get('before') : null;
    check('EXP-CONT-413-LOWER the failed request was a continuation page', [!!first, failedBefore !== null, failed && sp(failed).get('limit')], [true, true, '2']);
    fixture.mode.exportResponse = null;
    await setLimit('1'); await pause(200);
    // Soft probes (no throw) so the old head reports every intended RED.
    const lowered = await evaluate(page, `({ alert: document.body.innerText.includes(${q(tr('en', 'tasks.export.bodyTooLarge'))}), retry: [...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].some(b => b.innerText.trim().endsWith(${q(tr('en', 'tasks.export.retry'))})), limit: document.getElementById('tasks-export-limit').value })`);
    check('EXP-CONT-413-LOWER lowering the limit keeps the failure + Retry (no silent return to a page-1 Export)', lowered, { alert: true, retry: true, limit: '1' });
    await capture(page, 'exp-cont-413-lower-en-1440-lowered', { case: 'EXP-CONT-413-LOWER', locale: 'en', viewport: 1440, state: '413-limit-lowered' });
    const retryFrom = ledger.length;
    await evaluate(page, `(() => { const bs = [...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')]; const b = bs.find(x => x.innerText.trim().endsWith(${q(tr('en', 'tasks.export.retry'))})) || bs.find(x => x.innerText.trim().endsWith(${q(tr('en', 'tasks.export.export'))})); b.click(); return true; })()`);
    await settle(page, `!!document.querySelector('[data-testid="tasks-export-receipt"]')`, 'receipt retry');
    const retried = exportRows(retryFrom);
    check('EXP-CONT-413-LOWER exactly one retry request', retried.length, 1);
    const r = retried[0];
    check('EXP-CONT-413-LOWER retry ledger: before = failed cursor, limit = 1, other predicates unchanged', r ? { before: sp(r).get('before'), limit: sp(r).get('limit'), rest: [...sp(r)].filter(([k]) => k !== 'before' && k !== 'limit').join('&') } : null, { before: failedBefore, limit: '1', rest: [...sp(failed)].filter(([k]) => k !== 'before' && k !== 'limit').join('&') });
    const receipt = await evaluate(page, `document.querySelector('[data-testid="tasks-export-receipt"]').innerText`);
    check('EXP-CONT-413-LOWER honest receipt: 1 row + more may match + next page offered', [receipt.split('\n').map((x) => x.trim()).filter(Boolean), await evaluate(page, `[...document.querySelectorAll('[data-testid="tasks-export-dialog"] button')].some(b => b.innerText.trim().endsWith(${q(tr('en', 'tasks.export.nextPage'))}))`)], [[tr('en', 'tasks.export.receipt', { count: 1, fmt: 'JSON' }), tr('en', 'tasks.export.moreMayMatch')], true]);
    await capture(page, 'exp-cont-413-lower-en-1440-retried', { case: 'EXP-CONT-413-LOWER', locale: 'en', viewport: 1440, state: 'retried' });
    await closePage(page);
    endCase();
  }

  // ---------------------------------------------------------------- LOC01
  if (run('LOC01')) {
    beginCase('LOC01', 'en -> zh-CN -> en with export dialog open: CSV + limit kept, same focused node, zero /api per switch');
    fixture.reset();
    const page = await open('?status=in_progress&attr_min=effort%3D3');
    await openExport(page);
    await clickSrc(page, `document.querySelector('input[name="tasks-export-format"][value="csv"]')`); await pause(150);
    await evaluate(page, `(() => { const s = document.getElementById('tasks-export-limit'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, '250'); s.dispatchEvent(new Event('input', { bubbles: true })); s.focus(); window.__limitNode = s; return true; })()`);
    const urlBefore = await evaluate(page, 'location.href');
    for (const locale of ['zh-CN', 'en']) {
      const from = ledger.length;
      await crossTabSwitch(page, locale);
      check(`LOC01 -> ${locale} zero /api requests`, ledger.slice(from).map((r) => `${r.method} ${r.path}`), []);
      check(`LOC01 -> ${locale} state kept`, await evaluate(page, `({ open: !!document.querySelector('[data-testid="tasks-export-dialog"]'), csv: document.querySelector('input[name="tasks-export-format"][value="csv"]').checked, limit: document.getElementById('tasks-export-limit').value, sameFocus: document.activeElement === window.__limitNode, url: location.href })`), { open: true, csv: true, limit: '250', sameFocus: true, url: urlBefore });
      check(`LOC01 -> ${locale} dialog retranslated`, await evaluate(page, hasText(tr(locale, 'tasks.export.limitLabel'))), true);
      if (locale === 'zh-CN') await capture(page, 'loc01-zh-CN-1440-dialog', { case: 'LOC01', locale, viewport: 1440 });
    }
    await closePage(page);
    endCase();
  }

  // ---------------------------------------------------------------- COPY01 / HOS
  if (run('EXP')) {
    beginCase('COPY01', 'no forbidden promise terms in any captured dialog state (en + zh-CN)');
    check('COPY01 nonvacuous states captured', copyLog.length >= 8, true);
    for (const [locale, state, s] of copyLog) check(`COPY01 ${locale} ${state}`, copyHits(s, locale), []);
    endCase();
  }
  if (run('HOS')) {
    beginCase('HOS01-03', 'HTML literal, RTL isolated, 4000-char value wraps (no 390 overflow)');
    for (const width of viewports) {
      fixture.reset();
      const page = await open('?status=pending&attr_set=comment', { width });
      await settle(page, `document.querySelectorAll('[data-testid="task-row-attribute-values"]').length >= 3`, 'values');
      check(`HOS01 ${width} literal text, no injected img`, [await evaluate(page, hasText('<img src=x onerror=alert(1)>')), await evaluate(page, `document.querySelectorAll('img[src="x"]').length`)], [true, 0]);
      check(`HOS02 ${width} RTL value isolated in bdi dir=auto`, await evaluate(page, `[...document.querySelectorAll('[data-testid="task-row-attribute-values"] bdi[dir="auto"]')].some(b => b.textContent.includes('مرحبا'))`), true);
      check(`HOS03 ${width} no horizontal overflow with 4000-char value`, await overflow(page), false);
      await capture(page, `hos-en-${width}`, { case: 'HOS01-03', locale: 'en', viewport: width });
      await closePage(page);
    }
    endCase();
  }
}

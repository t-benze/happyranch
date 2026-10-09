/** Naming-only adapter: shipping SPA and real loopback daemon, never modeA mocks. */
import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { capture } from './screenshot-harness/harness.mjs';

const args = process.argv.slice(2);
function argument(key) {
  const index = args.indexOf(key);
  if (index < 0 || !args[index + 1]) throw new Error(`missing ${key}`);
  return args[index + 1];
}
const base = new URL(argument('--base-url'));
if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || !base.port)
  throw new Error('a disposable loopback daemon origin is required');
const out = resolve(argument('--out'));
const scenario = argument('--scenario');
if (!['agent', 'founder', 'picker'].includes(scenario)) throw new Error('unknown naming scenario');
const session = `naming-${scenario}-${process.pid}`;
await mkdir(out, { recursive: true });

function pw(id, argv) {
  return new Promise((accept, reject) => {
    const child = spawn('playwright-cli', [`-s=${id}`, ...argv], { stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = []; let bytes = 0; let failure;
    const timer = setTimeout(() => { failure = new Error('browser CLI deadline'); child.kill('SIGTERM'); }, 45000);
    const killTimer = setTimeout(() => child.kill('SIGKILL'), 50000);
    const consume = (chunk) => {
      bytes += chunk.length;
      if (bytes > 65536) { failure = new Error('browser CLI output cap'); child.kill('SIGTERM'); }
      else chunks.push(chunk);
    };
    child.stdout.on('data', consume); child.stderr.on('data', consume);
    child.on('error', (error) => { clearTimeout(timer); clearTimeout(killTimer); reject(error); });
    child.on('close', (code) => {
      clearTimeout(timer); clearTimeout(killTimer);
      const output = Buffer.concat(chunks).toString();
      if (failure || code !== 0 || output.includes('### Error')) reject(failure ?? new Error(output));
      else accept(output);
    });
  });
}
function result(output) {
  const match = /### Result\s*\n([\s\S]*?)(?=\n### |$)/.exec(output);
  if (!match) throw new Error('browser returned no assertion result');
  let value = JSON.parse(match[1].trim());
  if (typeof value === 'string') value = JSON.parse(value);
  if (value?.proof !== 'real-naming-ui') throw new Error('browser assertion receipt differs');
  return value;
}

// Stringified into the CLI's supported run-code function expression. No mocked
// responses or npm Playwright import. The only failure injection is an aborted
// upload transport; every rename, stale readback and successful compose is real.
async function interaction(page, base, scenario, uploadFile) {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  await page.context().route('**/*', (route) => {
    const url = new URL(route.request().url());
    return url.origin === base || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort('blockedbyclient');
  });
  page.setDefaultTimeout(10000); page.setDefaultNavigationTimeout(15000);
  const writes = []; const responses = []; const taskQueries = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes('/api/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method()))
      writes.push({ path: url.pathname, method: request.method(), body: request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : { multipart: true } });
    if (url.pathname.endsWith('/tasks/roots')) taskQueries.push(url.searchParams.get('assigned_agent'));
  });
  page.on('response', (response) => {
    if (response.url().endsWith('/addressable-name')) responses.push(response.status());
  });
  await page.goto(base + '/orgs/alpha/agents/maker');
  await page.evaluate(() => {
    localStorage.setItem('happyranch.ui.locale', 'en'); localStorage.setItem('happyranch.theme', 'light');
  });
  await page.reload();
  const bootstrap = await page.request.get(base + '/api/v1/auth/bootstrap');
  check(bootstrap.ok(), 'real localhost bootstrap refused');
  const { token } = await bootstrap.json();
  const headers = { Authorization: `Bearer ${token}` };
  async function identities() {
    const response = await page.request.get(base + '/api/v1/orgs/alpha/identities', { headers });
    check(response.ok(), 'real identity read refused'); return (await response.json()).identities;
  }
  async function externalRename(id, name) {
    const previous = (await identities()).find((row) => row.canonical_id === id);
    const path = id === 'founder' ? '/founder' : '/agents/' + id;
    const response = await page.request.put(base + '/api/v1/orgs/alpha' + path + '/addressable-name', {
      headers, data: { addressable_name: name, expected_name_revision: previous.name_revision },
    });
    check(response.status() === 200, 'concurrent real rename refused');
  }
  async function locale(input, locale) {
    const original = await input.elementHandle();
    await input.focus();
    await input.evaluate((el) => el.setSelectionRange(0, 4));
    const value = await input.inputValue(); const before = writes.length;
    // A second real tab produces the supported native storage event; no reload
    // or remount of the control under observation.
    const peer = await page.context().newPage();
    await peer.goto(base + '/');
    await peer.evaluate((next) => localStorage.setItem('happyranch.ui.locale', next), locale);
    await peer.close(); await page.bringToFront();
    await page.waitForFunction((next) => document.documentElement.lang === next, locale);
    check(await original.evaluate((el) => el.isConnected), 'locale remounted input');
    check(await input.inputValue() === value, 'locale lost draft/selected ID');
    check(await original.evaluate((el) => document.activeElement === el && el.selectionEnd === 4), 'locale lost focus/selection');
    check(writes.length === before, 'locale emitted a product mutation');
  }
  if (scenario === 'agent') {
    const editor = page.getByRole('region', { name: 'Agent name', exact: true });
    const input = editor.getByLabel('Addressable name');
    await input.fill('BrowserSam');
    const saved = page.waitForResponse((r) => r.url().endsWith('/agents/maker/addressable-name') && r.request().method() === 'PUT');
    await editor.getByRole('button', { name: 'Save name', exact: true }).click();
    check((await saved).status() === 200, 'agent UI rename did not commit');
    await editor.getByRole('status').filter({ hasText: 'Saved.' }).waitFor();
    await input.fill('KeepDraft');
    const expected = (await identities()).find((row) => row.canonical_id === 'maker').name_revision;
    await externalRename('maker', 'ExternalSam');
    const stale = page.waitForResponse((r) => r.url().endsWith('/agents/maker/addressable-name') && r.request().method() === 'PUT');
    await editor.getByRole('button', { name: 'Save name', exact: true }).click();
    check((await stale).status() === 409, 'UI did not exercise real stale CAS');
    await editor.getByRole('button', { name: 'Resubmit name', exact: true }).waitFor();
    check(await input.inputValue() === 'KeepDraft', 'stale response lost draft');
    check(!(await editor.getByRole('status').textContent()).includes('Saved.'), 'failed CAS attributed success');
    await locale(input, 'zh-CN');
    const current = (await identities()).find((row) => row.canonical_id === 'maker');
    check(current.addressable_name === 'ExternalSam' && current.name_revision === expected + 1, 'readback fabricated draft commit');
    check(writes.filter((r) => r.path.endsWith('/addressable-name')).length === 2, 'blind retry on stale result');
    return { proof: 'real-naming-ui', scenario, writes, responses, persistedName: 'ExternalSam', revision: current.name_revision };
  }
  if (scenario === 'founder') {
    await page.goto(base + '/orgs/alpha/settings/organization');
    const editor = page.getByRole('region', { name: 'Founder name', exact: true });
    await editor.getByLabel('Addressable name').fill('HumanBoss');
    const pending = page.waitForResponse((r) => r.url().endsWith('/founder/addressable-name'));
    await editor.getByRole('button', { name: 'Save name', exact: true }).click();
    check((await pending).status() === 200, 'founder UI rename did not commit');
    await editor.getByRole('status').filter({ hasText: 'Saved.' }).waitFor();
    const resolved = await page.request.post(base + '/api/v1/orgs/alpha/identities/resolve', {
      headers, data: { addresses: ['founder', 'HumanBoss'], context: 'thread_recipient' },
    });
    check(resolved.status() === 200, 'human resolution refused');
    const resolvedBody = await resolved.json();
    check(resolvedBody.resolutions.every((r) => r.identity.kind === 'founder' && r.identity.canonical_id === 'founder' && r.eligible), 'founder became an agent');
    return { proof: 'real-naming-ui', scenario, writes, responses, resolved: resolvedBody };
  }
  await externalRename('maker', 'PickerSam');
  await page.goto(base + '/orgs/alpha/tasks');
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  const agent = page.getByLabel('Assigned agent (exact name)');
  await agent.fill('PickerSam');
  await page.getByRole('option', { name: /PickerSam · maker/ }).click();
  check(await agent.inputValue() === 'maker', 'task picker selected a label instead of ID');
  await externalRename('maker', 'NewPickerSam');
  await locale(agent, 'zh-CN');
  const applied = page.waitForRequest((request) => request.url().includes('/tasks/roots?') && new URL(request.url()).searchParams.get('assigned_agent') === 'maker').catch(() => {
    check(taskQueries.includes('maker'), 'task query never submitted canonical ID');
  });
  await page.getByRole('button', { name: '应用', exact: true }).click();
  await applied;
  // Return to en in a fresh route; the business interaction remains one path.
  await page.evaluate(() => localStorage.setItem('happyranch.ui.locale', 'en'));
  await page.goto(base + '/orgs/alpha/threads');
  await page.getByRole('button', { name: /New thread/i }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Subject', { exact: true }).fill('Naming browser selected IDs');
  const recipient = dialog.getByLabel('Recipients (comma-separated agent names)');
  await recipient.fill('NewPickerSam');
  await page.getByRole('option', { name: /NewPickerSam · maker/ }).click();
  check(await recipient.inputValue() === 'maker, ', 'thread picker selected a label');
  await externalRename('maker', 'FinalPickerSam');
  const body = dialog.getByLabel('Body (Markdown)');
  await body.fill('Keep literal @unrecognized');
  await dialog.locator('input[type=file]').setInputFiles(uploadFile);
  // Real browser transport loss, no fake daemon success. UI selection and
  // draft must remain; the failed artifact is never called persisted evidence.
  const uploadRoute = (url) => url.pathname === '/api/v1/orgs/alpha/artifacts';
  await page.route(uploadRoute, (route) => route.abort('failed'));
  await dialog.getByRole('button', { name: 'Send', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  check(await recipient.inputValue() === 'maker, ' && await body.inputValue() === 'Keep literal @unrecognized', 'upload failure lost selected ID/draft');
  check(writes.filter((r) => r.method === 'POST' && r.path.endsWith('/threads')).length === 0, 'failed upload composed a thread');
  await locale(recipient, 'zh-CN');
  await locale(recipient, 'en');
  await page.unroute(uploadRoute);
  const composed = page.waitForResponse((r) => r.url().endsWith('/api/v1/orgs/alpha/threads') && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Send', exact: true }).click();
  const response = await composed; check(response.status() === 200, 'real compose refused');
  const receipt = await response.json();
  const sent = writes.find((r) => r.method === 'POST' && r.path.endsWith('/threads'));
  check(JSON.stringify(sent.body.recipients) === '["maker"]', 'thread POST changed bound principal');
  return { proof: 'real-naming-ui', scenario, writes, responses, taskQueries, threadId: receipt.thread_id };
}

let opened = false;
try {
  await pw(session, ['open']); opened = true;
  const uploadFile = resolve(out, 'proof.txt');
  await writeFile(uploadFile, 'naming upload\n');
  const expression = `async page => (${interaction.toString()})(page, ${JSON.stringify(base.origin)}, ${JSON.stringify(scenario)}, ${JSON.stringify(uploadFile)})`;
  const file = resolve(out, 'interaction.js'); await writeFile(file, expression);
  const proof = result(await pw(session, ['run-code', `--filename=${file}`]));
  await pw(session, ['close']); opened = false;
  // Reuse capture primitives with real API/SPA URLs. Read/draft-only visual
  // prep reuses the editor/picker path; it creates no extra business cases.
  if (scenario === 'picker') {
    const screenshots = [];
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      for (const locale of ['en', 'zh-CN']) {
        for (const theme of ['light', 'dark']) {
          for (const route of ['agents/maker', 'settings/organization', 'threads', 'tasks']) {
            const name = `${route.replaceAll('/', '-')}-${width}-${locale}-${theme}.png`;
            const prep = [['run-code', `async page => {
              await page.evaluate(next => localStorage.setItem('happyranch.ui.locale', next), ${JSON.stringify(locale)});
              await page.reload();
              const editor = page.getByRole('region', {name: /Agent name|Agent 名称|Founder name|创始人名称/});
              if (${JSON.stringify(route)} === 'agents/maker' || ${JSON.stringify(route)} === 'settings/organization') {
                await editor.waitFor();
                const input = editor.locator('input'); await input.fill('VisualDraft'); await input.focus();
                if (!await input.isVisible()) throw new Error('editor control is unreachable');
              } else if (${JSON.stringify(route)} === 'threads') {
                await page.getByRole('button', {name: ${JSON.stringify(locale === 'en' ? 'New thread' : '新建会话')}, exact:true}).click();
                const dialog = page.getByRole('dialog');
                const recipient = dialog.getByLabel(${JSON.stringify(locale === 'en' ? 'Recipients (comma-separated agent names)' : '收件人（以逗号分隔的智能体名称）')});
                await recipient.fill('maker, '); await recipient.focus();
                if (!await recipient.isVisible()) throw new Error('recipient control is unreachable');
              } else {
                await page.getByRole('button', {name: ${JSON.stringify(locale === 'en' ? 'Filter' : '筛选')}, exact:true}).click();
                const agent = page.getByLabel(${JSON.stringify(locale === 'en' ? 'Assigned agent (exact name)' : '负责智能体（精确名称）')});
                await agent.fill('maker'); await agent.focus();
                if (!await agent.isVisible()) throw new Error('task control is unreachable');
              }
            }`]];
            const path = resolve(out, name);
            try {
              await capture({ url: base.origin + '/orgs/alpha/' + route, out: path,
                viewport: [width, height], theme, prep, session: session + '-visual' });
            } finally {
              // Shared capture catches close errors; an explicit supported close
              // recheck here makes unresolved owned cleanup a visible failure.
              await pw(session + '-visual', ['close']);
            }
            if ((await stat(path)).size > 4 * 1024 * 1024) throw new Error('screenshot cap exceeded');
            screenshots.push(name);
          }
        }
      }
    }
    proof.screenshots = screenshots;
  }
  const childRows = (await readFile(resolve(out, 'native-children.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  const children = childRows.filter((row) => row.kind === 'spawn' && row.pid);
  const until = Date.now() + 5000;
  let alive = [];
  do {
    alive = [];
    for (const child of children) {
      try {
        const native = await readFile(`/proc/${child.pid}/stat`, 'utf8');
        const fields = native.slice(native.lastIndexOf(') ') + 2).split(' ');
        if (fields[0] !== 'Z' && (!child.start || fields[19] === child.start)) alive.push(child.pid);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if (alive.length) await new Promise((accept) => setTimeout(accept, 50));
  } while (alive.length && Date.now() < until);
  await writeFile(resolve(out, 'browser-closure.json'), JSON.stringify({children, remainingPids: alive}) + '\n');
  if (alive.length) throw new Error('owned browser/CLI child closure incomplete: ' + alive.join(','));
  proof.ownedChildrenClosed = true;
  await writeFile(resolve(out, 'result.json'), JSON.stringify(proof, null, 2) + '\n');
} finally {
  if (opened) await pw(session, ['close']); // Cleanup failure propagates.
}

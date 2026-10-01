/**
 * THR-229 R3 browser receipt: real SPA -> Vite proxy -> owned real daemon/DB.
 *
 * Usage:
 *   node scripts/screenshot-harness/shot-thr229-v2-policy.mjs /absolute/output/dir
 *
 * The fixture bearer never leaves browser sessionStorage and is never logged.
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { access, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..', '..');
const REPO_ROOT = resolve(WEB_ROOT, '..');
const PYTHON = process.env.THR229_PYTHON || '/home/benze/projects/happyranch/.venv/bin/python3';
const OUT = resolve(process.argv[2] || join(HERE, 'out', 'task-8768-thr229-v2-policy'));
const SESSION = `task8768-thr229-${process.pid}`;
const PLAYWRIGHT_TMP = process.env.THR229_PLAYWRIGHT_TMP || '/tmp/TASK-8768-pw';
const STARTER_TO = 'Escalate when the next action requires a product or external-contract change, significant architecture change, or substantial development effort beyond the approved scope. Also escalate decisions explicitly reserved for the founder that lack applicable authorization. Existing approval carries through ordinary implementation and recovery within its scope.';
const STARTER_NOT = 'Continue implementation, debugging, review corrections, testing, CI waits, evidence collection and worker reassignment within approved scope. Failed reviews, retries, incomplete worker results and recoverable execution failures alone do not require founder escalation. Continue to enforce the required review, QA and merge gates.';
const SECOND_TO = 'Escalate approved browser proof when BOTH policy texts require a new external commitment.';
const SECOND_NOT = 'Continue bounded implementation and verification when BOTH edited texts remain inside approved scope.';
const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function child(command, args, options = {}) {
  const proc = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options });
  let stdout = '';
  let stderr = '';
  proc.stdout.on('data', (data) => { stdout += data; });
  proc.stderr.on('data', (data) => { stderr += data; });
  const done = new Promise((resolvePromise, reject) => {
    proc.on('error', reject);
    proc.on('exit', (code, signal) => resolvePromise({ code, signal, stdout, stderr }));
  });
  return { proc, done, stdout: () => stdout, stderr: () => stderr };
}

function pw(args) {
  return new Promise((resolvePromise, reject) => {
    const proc = spawn('playwright-cli', [`-s=${SESSION}`, ...args], {
      stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, TMPDIR: PLAYWRIGHT_TMP },
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (data) => { stdout += data; });
    proc.stderr.on('data', (data) => { stderr += data; });
    proc.on('error', reject);
    proc.on('exit', (code) => code === 0
      ? resolvePromise(stdout)
      : reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${stderr}`)));
  });
}

function parseEvalOutput(output) {
  const trimmed = output.trim();
  const marker = '### Result';
  const index = trimmed.lastIndexOf(marker);
  let raw = index === -1 ? trimmed : trimmed.slice(index + marker.length).trim();
  const nextMarker = raw.indexOf('\n###');
  if (nextMarker !== -1) raw = raw.slice(0, nextMarker).trim();
  const inner = JSON.parse(raw);
  return typeof inner === 'string' ? JSON.parse(inner) : inner;
}

async function evalJson(expression) {
  return parseEvalOutput(await pw(['eval', `async () => JSON.stringify(await (${expression}))`]));
}

async function waitForPage(predicate, description, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await evalJson(`(${predicate})`);
    if (latest) return latest;
    await sleep(100);
  }
  throw new Error(`timed out waiting for ${description}; latest=${JSON.stringify(latest)}`);
}

async function pageState() {
  return evalJson(`({
    url: location.href,
    text: document.body.innerText,
    textareas: [...document.querySelectorAll('textarea')].map((el) => ({
      label: el.closest('label')?.childNodes[0]?.textContent?.trim() || '', value: el.value,
    })),
    status: document.querySelector('[role="status"]')?.textContent || null,
  })`);
}

async function fillPair(whatTo, whatNot) {
  const result = await evalJson(`(() => {
    const values = ${JSON.stringify([whatTo, whatNot])};
    const fields = [...document.querySelectorAll('textarea')];
    if (fields.length !== 2) return { ok: false, count: fields.length };
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    fields.forEach((field, index) => {
      setter.call(field, values[index]);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    return { ok: true, values: fields.map((field) => field.value) };
  })()`);
  assert(result.ok && result.values[0] === whatTo && result.values[1] === whatNot,
    `could not fill exact pair: ${JSON.stringify(result)}`);
}

async function clickButton(label) {
  const clicked = await evalJson(`(() => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === ${JSON.stringify(label)});
    if (!button) return false;
    button.click();
    return true;
  })()`);
  assert(clicked, `button not found: ${label}`);
}

async function savePair() {
  await clickButton('Save & activate');
  await waitForPage(`document.body.innerText.includes('Save and activate both policy texts?')`, 'paired confirmation');
  await clickButton('Confirm save & activate');
}

async function screenshot(name) {
  const path = join(OUT, `${name}.png`);
  await pw(['screenshot', '--full-page', `--filename=${path}`]);
  await access(path);
  return path;
}

async function freePort() {
  const server = createServer();
  await new Promise((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolvePromise);
  });
  const port = server.address().port;
  await new Promise((resolvePromise) => server.close(resolvePromise));
  return port;
}

async function waitHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch { /* bounded readiness poll */ }
    await sleep(100);
  }
  throw new Error(`server did not become ready: ${url}`);
}

async function startFixture(root) {
  const processHandle = spawn(PYTHON, [join(HERE, 'thr229-v2-policy-fixture.py'), root], {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONPATH: REPO_ROOT, PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let buffer = '';
  let stderr = '';
  let nextId = 1;
  const pending = new Map();
  let readyResolve;
  let readyReject;
  const readyPromise = new Promise((resolvePromise, reject) => { readyResolve = resolvePromise; readyReject = reject; });
  processHandle.stderr.on('data', (data) => { stderr += data; });
  processHandle.stdout.on('data', (data) => {
    buffer += data;
    for (;;) {
      const newline = buffer.indexOf('\n');
      if (newline < 0) break;
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (message.event === 'ready') readyResolve(message);
      else if (pending.has(message.id)) {
        const { resolve: resolvePending, reject } = pending.get(message.id);
        pending.delete(message.id);
        if (message.ok) resolvePending(message.result);
        else reject(new Error(message.error));
      }
    }
  });
  processHandle.on('exit', (code) => {
    if (code && pending.size) for (const { reject } of pending.values()) reject(new Error(`fixture exited ${code}: ${stderr}`));
    if (code) readyReject(new Error(`fixture exited ${code}: ${stderr}`));
  });
  const ready = await Promise.race([
    readyPromise,
    sleep(30000).then(() => { throw new Error(`fixture readiness timeout: ${stderr}`); }),
  ]);
  const rpc = (command) => new Promise((resolvePromise, reject) => {
    const id = nextId++;
    pending.set(id, { resolve: resolvePromise, reject });
    processHandle.stdin.write(`${JSON.stringify({ id, command })}\n`);
  });
  return { processHandle, ready, rpc, stderr: () => stderr };
}

await mkdir(OUT, { recursive: true });
await mkdir(PLAYWRIGHT_TMP, { recursive: true });
const ownedRoot = await mkdtemp(join(OUT, 'owned-fixture-'));
const evidence = {
  classification: 'REAL isolated browser persistence proof',
  simulated: [],
  output_dir: OUT,
  fixture_root: ownedRoot,
  screenshots: {},
};
let fixture;
let vite;
let browserOpen = false;
try {
  fixture = await startFixture(ownedRoot);
  assert(fixture.ready.port !== 8765, 'fixture daemon must not use live port 8765');
  const portFile = Number((await readFile(join(fixture.ready.home, 'daemon.port'), 'utf8')).trim());
  assert(portFile === fixture.ready.port, 'daemon.port does not identify the owned fixture');
  evidence.daemon = { port: fixture.ready.port, daemon_port_file: portFile, home: fixture.ready.home };

  const vitePort = await freePort();
  const viteBinary = join(WEB_ROOT, 'node_modules', '.bin', 'vite');
  vite = child(viteBinary, ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'], {
    cwd: WEB_ROOT,
    env: { ...process.env, HAPPYRANCH_DAEMON_HOME: fixture.ready.home },
  });
  const base = `http://127.0.0.1:${vitePort}`;
  await waitHttp(base);
  evidence.vite = { port: vitePort, command: `${viteBinary} --host 127.0.0.1 --port ${vitePort} --strictPort` };

  await pw(['open']);
  browserOpen = true;
  await pw(['resize', '1440', '1000']);
  await pw(['goto', base]);
  await pw(['sessionstorage-delete', 'happyranch.token']);
  const policyUrl = `${base}/orgs/isolated-org/agents/engineering_manager/team-escalation-policy`;
  await pw(['goto', policyUrl]);
  await waitForPage(`document.querySelectorAll('textarea').length === 2`, 'empty eligible editor');
  const projection = await evalJson(`fetch('/api/v1/orgs/isolated-org/agents/engineering_manager/team-escalation-policy', {
    headers: { Authorization: 'Bearer ' + sessionStorage.getItem('happyranch.token') },
  }).then((response) => response.json())`);
  assert(projection.v2_starter.policy_id === 'team-8c85b6639e62e10b-dual-text', 'server projected the wrong Engineering starter identity');
  assert(projection.v2_starter.what_to_escalate === STARTER_TO, 'server starter changed What to escalate bytes');
  assert(projection.v2_starter.what_not_to_escalate === STARTER_NOT, 'server starter changed What not to escalate bytes');
  const emptyState = await pageState();
  assert(emptyState.textareas[0].value === projection.v2_starter.what_to_escalate, 'empty editor did not use server What to escalate starter bytes');
  assert(emptyState.textareas[1].value === projection.v2_starter.what_not_to_escalate, 'empty editor did not use server What not to escalate starter bytes');
  assert(emptyState.text.includes('Owned by the Engineering team, not by this agent.'), 'Engineering ownership copy is not team-derived');
  assert(emptyState.text.includes('← Back to Engineering Manager'), 'Engineering back link is not manager-derived');
  const emptyDb = await fixture.rpc('snapshot');
  assert(emptyDb.counts.releases === 0 && emptyDb.counts.activations === 0, 'empty GET wrote a policy release');
  evidence.screenshots.empty = await screenshot('01-loaded-empty');

  await fillPair(projection.v2_starter.what_to_escalate, projection.v2_starter.what_not_to_escalate);
  await savePair();
  await waitForPage(`document.body.innerText.includes('Saved and activated immutable v2 release')`, 'first paired save readback');
  const firstDb = await fixture.rpc('snapshot');
  assert(firstDb.counts.releases === 1 && firstDb.counts.activations === 1, 'first pair did not commit exactly one release and activation');
  assert(firstDb.receipts.length === 1, 'first paired receipt is missing');
  const firstReceipt = firstDb.receipts[0];
  assert(firstDb.history[0].what_to_escalate === STARTER_TO && firstDb.history[0].what_not_to_escalate === STARTER_NOT,
    'first history row did not preserve exact starter bytes');

  await pw(['reload']);
  await waitForPage(`document.body.innerText.includes(${JSON.stringify(firstReceipt.release_id)}) && document.body.innerText.includes('Immutable dual-text history')`, 'full reload active/history readback');
  const reloadState = await pageState();
  const reloadedDb = await fixture.rpc('snapshot');
  assert(reloadedDb.selector.selector_id === firstReceipt.selector_id, 'selector changed across full reload');
  assert(reloadedDb.selector.selector_epoch === firstReceipt.selector_epoch, 'selector epoch changed across full reload');
  assert(reloadedDb.history[0].release_id === firstReceipt.release_id, 'release changed across full reload');
  assert(reloadedDb.history[0].policy_digest === firstReceipt.policy_digest, 'digest changed across full reload');
  assert(reloadState.text.includes(firstReceipt.activation_id), 'active activation identity missing after reload');
  assert(reloadState.text.includes(STARTER_TO) && reloadState.text.includes(STARTER_NOT), 'immutable history omitted paired texts');
  evidence.screenshots.active_history = await screenshot('02-active-readback-history');

  await fillPair(SECOND_TO, SECOND_NOT);
  await fixture.rpc('fault_on');
  const beforeFault = await fixture.rpc('snapshot');
  await savePair();
  await waitForPage(`document.body.innerText.includes('The save result is unknown.')`, 'injected atomic failure');
  const afterFault = await fixture.rpc('snapshot');
  assert(JSON.stringify(afterFault.counts) === JSON.stringify(beforeFault.counts), 'failed pair left policy table residue');
  assert(afterFault.selector.selector_id === beforeFault.selector.selector_id, 'failed pair changed active selector');
  assert(afterFault.history[0].release_id === beforeFault.history[0].release_id, 'failed pair changed active release');
  evidence.screenshots.atomic_failure = await screenshot('03-atomic-failure-zero-residue');

  await fixture.rpc('fault_off');
  await clickButton('Retry exact save & activate');
  await waitForPage(`document.body.innerText.includes('Saved and activated immutable v2 release') && document.body.innerText.includes(${JSON.stringify(SECOND_TO)})`, 'successful exact retry');
  const retryDb = await fixture.rpc('snapshot');
  assert(retryDb.counts.releases === beforeFault.counts.releases + 1, 'retry did not add exactly one release');
  assert(retryDb.counts.activations === beforeFault.counts.activations + 1, 'retry did not add exactly one activation');
  assert(retryDb.history[0].what_to_escalate === SECOND_TO && retryDb.history[0].what_not_to_escalate === SECOND_NOT,
    'retry did not atomically commit both edited texts');

  const beforeInvalid = await fixture.rpc('snapshot');
  const invalidStatus = await evalJson(`(async () => {
    sessionStorage.setItem('happyranch.token', 'invalid-fixture-token');
    const response = await fetch('/api/v1/orgs/isolated-org/agents/engineering_manager/team-escalation-policy/v2/releases', {
      method: 'POST', headers: { Authorization: 'Bearer invalid-fixture-token', 'Content-Type': 'application/json' }, body: '{}',
    });
    return response.status;
  })()`);
  assert(invalidStatus === 401, `invalid fixture token returned ${invalidStatus}, expected 401`);
  const afterInvalid = await fixture.rpc('snapshot');
  assert(JSON.stringify(afterInvalid.counts) === JSON.stringify(beforeInvalid.counts), 'invalid token wrote a policy row');

  const devStatus = await evalJson(`(async () => {
    const bootstrap = await fetch('/api/v1/auth/bootstrap').then((response) => response.json());
    sessionStorage.setItem('happyranch.token', bootstrap.token);
    const response = await fetch('/api/v1/orgs/isolated-org/agents/dev_agent/team-escalation-policy', {
      headers: { Authorization: 'Bearer ' + sessionStorage.getItem('happyranch.token') },
    });
    return response.status;
  })()`);
  assert(devStatus === 404, `dev_agent policy target returned ${devStatus}, expected 404`);
  await pw(['goto', `${base}/orgs/isolated-org/agents/dev_agent/team-escalation-policy`]);
  await waitForPage(`document.body.innerText.includes('Not found.')`, 'worker unavailable page');
  const unavailable = await pageState();
  assert(unavailable.textareas.length === 0, 'worker unavailable page exposed a policy editor');
  evidence.screenshots.unavailable = await screenshot('04-worker-unavailable');

  const contentPolicyUrl = `${base}/orgs/isolated-org/agents/content_manager/team-escalation-policy`;
  await pw(['goto', contentPolicyUrl]);
  await waitForPage(`document.querySelectorAll('textarea').length === 2`, 'Content eligible editor');
  const contentProjection = await evalJson(`fetch('/api/v1/orgs/isolated-org/agents/content_manager/team-escalation-policy', {
    headers: { Authorization: 'Bearer ' + sessionStorage.getItem('happyranch.token') },
  }).then((response) => response.json())`);
  const contentState = await pageState();
  assert(contentProjection.team === 'content' && contentProjection.target_manager === 'content_manager', 'Content projection identity is wrong');
  assert(contentProjection.v2_starter.policy_id === 'team-ed7002b439e9ac84-dual-text', 'server projected the wrong Content starter identity');
  assert(contentState.text.includes('Owned by the Content team, not by this agent.'), 'Content ownership copy is not team-derived');
  assert(!contentState.text.includes('Owned by the Engineering team, not by this agent.'), 'Content surface leaked Engineering ownership copy');
  assert(contentState.text.includes('Content · Content Manager'), 'Content page header is not team/manager-derived');
  assert(contentState.text.includes('← Back to Content Manager'), 'Content back link is not manager-derived');
  assert(contentState.url === contentPolicyUrl, 'Content navigation resolved to the wrong policy route');
  await fillPair(contentProjection.v2_starter.what_to_escalate, contentProjection.v2_starter.what_not_to_escalate);
  await savePair();
  await waitForPage(`document.body.innerText.includes('Saved and activated immutable v2 release')`, 'Content paired save readback');
  const contentDb = await fixture.rpc('snapshot_content');
  assert(contentDb.receipts.length === 1, 'Content paired receipt is missing');
  const contentReceipt = contentDb.receipts[0];
  assert(contentReceipt.team === 'content', 'Content paired receipt has the wrong team');
  assert(contentDb.selector.selector_id === contentReceipt.selector_id, 'Content selector does not match its receipt');
  assert(contentDb.history[0].release_id === contentReceipt.release_id, 'Content history does not contain its release');
  assert(contentDb.history[0].what_to_escalate === contentProjection.v2_starter.what_to_escalate,
    'Content history changed What to escalate bytes');
  assert(contentDb.history[0].what_not_to_escalate === contentProjection.v2_starter.what_not_to_escalate,
    'Content history changed What not to escalate bytes');
  await pw(['reload']);
  await waitForPage(`document.body.innerText.includes(${JSON.stringify(contentReceipt.release_id)}) && document.body.innerText.includes('Immutable dual-text history')`, 'Content full reload active/history readback');
  const contentReload = await pageState();
  assert(contentReload.text.includes(contentReceipt.activation_id), 'Content activation identity is missing after reload');
  assert(!contentReload.text.includes('Owned by the Engineering team, not by this agent.'), 'Content readback leaked Engineering ownership copy');
  evidence.screenshots.content_copy_navigation = await screenshot('05-content-copy-navigation');

  evidence.assertions = {
    empty_exact_starters: true,
    first_receipt: firstReceipt,
    reload_identity: {
      release_id: firstReceipt.release_id, activation_id: firstReceipt.activation_id,
      selector_id: firstReceipt.selector_id, selector_epoch: firstReceipt.selector_epoch,
      policy_digest: firstReceipt.policy_digest,
    },
    history_control_audit: firstDb.control_audit,
    atomic_fault_zero_residue: { before: beforeFault.counts, after: afterFault.counts },
    successful_retry_receipt: retryDb.receipts.at(-1),
    invalid_token_status: invalidStatus,
    invalid_token_no_rows: true,
    dev_agent_status: devStatus,
    dev_agent_editor_count: unavailable.textareas.length,
    engineering_copy_navigation: {
      owner: 'Owned by the Engineering team, not by this agent.',
      back_link: '← Back to Engineering Manager',
    },
    content_copy_navigation: {
      owner: 'Owned by the Content team, not by this agent.',
      back_link: '← Back to Content Manager',
      heading: 'Content · Content Manager',
      url: contentPolicyUrl,
      engineering_owner_absent: true,
    },
    content_create_activate_readback: {
      receipt: contentReceipt,
      selector_id: contentDb.selector.selector_id,
      selector_epoch: contentDb.selector.selector_epoch,
      release_id: contentDb.history[0].release_id,
      active_identity_visible_after_reload: true,
    },
  };
  await writeFile(join(OUT, 'browser-receipt.json'), `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify({ ok: true, receipt: join(OUT, 'browser-receipt.json'), screenshots: evidence.screenshots }, null, 2));
} finally {
  if (browserOpen) await pw(['close']).catch(() => {});
  if (vite) {
    vite.proc.kill('SIGTERM');
    await Promise.race([vite.done, sleep(5000)]).catch(() => {});
    evidence.vite_stderr = vite.stderr().slice(-4000);
  }
  if (fixture) {
    await fixture.rpc('fault_off').catch(() => {});
    await fixture.rpc('stop').catch(() => {});
    fixture.processHandle.stdin.end();
    if (fixture.processHandle.exitCode === null) {
      await Promise.race([
        new Promise((resolvePromise) => fixture.processHandle.once('exit', resolvePromise)),
        sleep(5000),
      ]);
    }
    if (fixture.processHandle.exitCode === null) {
      fixture.processHandle.kill('SIGTERM');
      await Promise.race([
        new Promise((resolvePromise) => fixture.processHandle.once('exit', resolvePromise)),
        sleep(5000),
      ]);
    }
    if (fixture.stderr()) await writeFile(join(OUT, 'fixture-stderr.log'), fixture.stderr());
  }
}

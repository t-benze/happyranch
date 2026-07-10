/**
 * THR-085 SLICE A evidence (TASK-2535): executor-binary REGISTRATION UI
 * (registration-only) under Settings → Executors → "CLI binary paths".
 *
 * Mode A (prod build + node /api mock). Two states × two themes:
 *   - registered : GET /executor-binaries returns a mix (valid / invalid /
 *                   unregistered) → per-kind rows, NO fresh-env banner.
 *   - freshenv   : GET /executor-binaries returns [] → the actionable
 *                   "register your CLI" blocked banner + all four kinds
 *                   unregistered (the manual-entry field IS the remediation).
 *
 * Run: node scripts/screenshot-harness/shot-2535.mjs   (needs `npm run build`)
 * Out: web/scripts/screenshot-harness/out/thr085-{registered,freshenv}-{light,dark}.png
 */
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Mutable registry the mock serves; swapped between the two states.
const REGISTERED = [
  { kind: 'claude', path: '/opt/homebrew/bin/claude', valid: true },
  { kind: 'codex', path: '/usr/local/bin/codex', valid: true },
  { kind: 'pi', path: '/Users/founder/.local/bin/pi', valid: false },
  // opencode intentionally absent → shows the "not registered" row alongside
  // valid + invalid, without tripping the fresh-env banner.
];
const state = { entries: REGISTERED };

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  // Settings page boot gate — the Executors tab ignores the body, it only
  // needs a truthy {system, org} snapshot to render the panel.
  { path: `/api/v1/orgs/${SLUG}/settings`, json: () => ({ system: {}, org: {} }) },
  { path: '/api/v1/executor-binaries', json: () => ({ entries: state.entries }) },
];

function pw(session, args) {
  return new Promise((resolve, reject) => {
    const p = spawn('playwright-cli', [`-s=${session}`, ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let err = '';
    p.stderr.on('data', (d) => (err += d));
    p.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${err}`)),
    );
    p.on('error', reject);
  });
}

async function capture(baseUrl, stateName, entries, theme) {
  state.entries = entries;
  const s = `x${stateName[0]}${theme[0]}`;
  const url = `${baseUrl}/orgs/${SLUG}/settings/executors`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', url]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1400);
    await pw(s, ['screenshot', `--filename=${join(OUT, `thr085-${stateName}-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2535] capturing registered ${theme}…`);
    await capture(srv.url, 'registered', REGISTERED, theme);
    // eslint-disable-next-line no-console
    console.log(`[shot-2535] capturing freshenv ${theme}…`);
    await capture(srv.url, 'freshenv', [], theme);
  }
  // eslint-disable-next-line no-console
  console.log('[shot-2535] done ->', OUT);
} finally {
  await srv.close();
}

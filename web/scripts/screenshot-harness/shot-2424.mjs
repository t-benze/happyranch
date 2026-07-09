/**
 * THR-069 msg85 evidence (TASK-2424): per-participant Remove control + confirm
 * step on the thread detail rail, mirroring the invite UX.
 *
 * Mode A (prod build + node /api mock). Renders the thread detail for an OPEN
 * thread so the founder-only Remove (×) affordance shows on each participant
 * row, then opens the confirm dialog before firing.
 *
 * Flow per theme:
 *   detail (participants + × controls) -> screenshot "control"
 *   -> click "Remove qa_engineer" -> confirm dialog -> screenshot "confirm"
 *
 * Run: node scripts/screenshot-harness/shot-2424.mjs   (needs `npm run build`)
 * Out: web/scripts/screenshot-harness/out/remove-{control,confirm}-{light,dark}.png
 */
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, defaultApiRoutes, findDist } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, 'out');
const SLUG = 'demo';
const THREAD_ID = 'THR-DEMO-1';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PARTICIPANTS = ['founder', 'frontend_engineer', 'qa_engineer'];

function threadDetail() {
  return {
    thread_id: THREAD_ID,
    subject: 'Ship the remove-participant control',
    status: 'open',
    started_at: '2026-07-09T02:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 3,
    summary: null,
    transcript_path: null,
    composed_from_dream_id: null,
    participants: PARTICIPANTS,
    messages: [],
  };
}

const messages = [
  {
    seq: 1,
    speaker: 'founder',
    kind: 'message',
    body_markdown: 'Please wire up a per-participant Remove control, mirroring Invite.',
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: '2026-07-09T02:00:00Z',
    responder_status: [],
  },
  {
    seq: 2,
    speaker: 'frontend_engineer',
    kind: 'message',
    body_markdown: 'On it — building the confirm-gated Remove affordance now.',
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: '2026-07-09T02:01:00Z',
    responder_status: [],
  },
];

const agents = PARTICIPANTS.filter((n) => n !== 'founder').map((name) => ({
  name,
  team: 'engineering',
  role: 'worker',
  executor: 'claude',
  description: null,
  repos: {},
  system_prompt: '',
}));

const api = [
  ...defaultApiRoutes({ orgs: [{ slug: SLUG, root: '/tmp/demo' }] }),
  { path: `/api/v1/orgs/${SLUG}/agents`, json: { agents } },
  { path: `/api/v1/orgs/${SLUG}/threads`, json: () => ({ threads: [threadDetail()] }) },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}`, json: () => threadDetail() },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/messages`, json: { messages } },
  { path: `/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/tasks`, json: [] },
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

async function runTheme(baseUrl, theme) {
  const s = `t2424-${theme}`;
  const url = `${baseUrl}/orgs/${SLUG}/threads/${THREAD_ID}`;
  await pw(s, ['open']);
  try {
    await pw(s, ['resize', '1440', '900']);
    await pw(s, ['goto', url]);
    await pw(s, ['localstorage-set', 'happyranch.theme', theme]);
    await pw(s, ['reload']);
    await sleep(1400);
    // Participants rail shows the founder-only Remove (×) control per row.
    await pw(s, ['screenshot', `--filename=${join(OUT, `remove-control-${theme}.png`)}`]);

    // Open the confirm gate for one participant (mirrors invite UX).
    await pw(s, ['click', "getByRole('button', { name: /Remove qa_engineer/ })"]);
    await sleep(600);
    await pw(s, ['screenshot', `--filename=${join(OUT, `remove-confirm-${theme}.png`)}`]);
  } finally {
    await pw(s, ['close']).catch(() => {});
  }
}

await mkdir(OUT, { recursive: true });
const srv = await createServer({ root: findDist(), api });
try {
  for (const theme of ['light', 'dark']) {
    // eslint-disable-next-line no-console
    console.log(`[shot-2424] capturing ${theme}…`);
    await runTheme(srv.url, theme);
  }
  // eslint-disable-next-line no-console
  console.log('[shot-2424] done ->', OUT);
} finally {
  await srv.close();
}

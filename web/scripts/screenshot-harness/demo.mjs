/**
 * Worked example + evidence generator for the FE screenshot harness.
 *
 * Runs ONE mode and writes light+dark PNGs. This is the reference the README
 * points to — copy a block into your own task script and adapt the content.
 *
 *   node scripts/screenshot-harness/demo.mjs a   # prod build + /api mock (Agents page)
 *   node scripts/screenshot-harness/demo.mjs b   # standalone dist-CSS (button + swatches)
 *   node scripts/screenshot-harness/demo.mjs c   # prop-driven Button, no providers
 *   node scripts/screenshot-harness/demo.mjs d   # A-mode dock via WS mock
 *   node scripts/screenshot-harness/demo.mjs all # every mode
 *
 * Output: web/scripts/screenshot-harness/out/mode-<x>-<theme>.png
 */
import { join } from 'node:path';
import { modeAProdApi, modeBDistCss, modeCProp, modeDWsDock, WEB_ROOT } from './harness.mjs';

const OUT = join(WEB_ROOT, 'scripts', 'screenshot-harness', 'out');

async function runA() {
  return modeAProdApi({
    route: '/orgs/demo/agents',
    outDir: OUT,
    name: 'mode-a',
    // The Agents page needs its list route; unmatched /api already -> {}.
    api: [{ path: '/api/v1/orgs/demo/agents', json: { agents: [] } }],
  });
}

async function runB() {
  // Arbitrary markup against the REAL compiled Tailwind. Reproduce the Button
  // default markup + a token swatch row — every class here is used in the app
  // build, so the JIT'd dist CSS carries it.
  const markup = `
  <button class="bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:ring-ring inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none">Primary</button>
  <button class="bg-secondary text-secondary-foreground border-border inline-flex h-9 items-center justify-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium">Secondary</button>
  <div class="flex flex-col gap-2">
    <span class="bg-accent h-8 w-24 rounded-md"></span>
    <span class="bg-surface-raised border-border-subtle h-8 w-24 rounded-md border"></span>
  </div>`;
  return modeBDistCss({ markup, outDir: OUT, name: 'mode-b' });
}

async function runC() {
  return modeCProp({
    importPath: '@/design-system/primitives/Button',
    exportName: 'Button',
    render: `<>
      <Button variant='default'>Default</Button>
      <Button variant='secondary'>Secondary</Button>
      <Button variant='destructive'>Danger</Button>
      <Button variant='outline'>Outline</Button>
    </>`,
    outDir: OUT,
    name: 'mode-c',
  });
}

async function runD() {
  return modeDWsDock({
    route: '/orgs/demo/agents',
    outDir: OUT,
    name: 'mode-d',
    // Agents page renders cleanly under the dock; provide its list route.
    api: [{ path: '/api/v1/orgs/demo/agents', json: { agents: [] } }],
    activeConv: 'c1',
    conversations: [
      { id: 'c1', title: 'Weekly spend review', created_at: '2026-07-01T10:00:00Z', active: true },
      { id: 'c2', title: 'Deploy checklist', created_at: '2026-07-02T09:00:00Z', active: false },
    ],
    historyByConv: {
      c1: [
        {
          prompt: 'How much did we spend last week?',
          started_at: '2026-07-01T10:00:05Z',
          frames: [
            { type: 'turn_start' },
            { type: 'text_delta', text: 'Last week total spend was $432.17 across 3 orgs. ' },
            { type: 'tool_call', name: 'query_metrics' },
            { type: 'tool_result', name: 'query_metrics', ok: true },
            { type: 'text_delta', text: 'The largest line was compute at $310.' },
            { type: 'turn_end' },
          ],
        },
      ],
    },
  });
}

const MODES = { a: runA, b: runB, c: runC, d: runD };

const arg = (process.argv[2] || 'all').toLowerCase();
const toRun = arg === 'all' ? Object.keys(MODES) : [arg];

for (const m of toRun) {
  if (!MODES[m]) {
    console.error(`unknown mode '${m}' (use a|b|c|d|all)`);
    process.exit(2);
  }
  console.log(`\n=== mode ${m.toUpperCase()} ===`);
  const shots = await MODES[m]();
  for (const s of shots) console.log('  wrote', s);
}
console.log('\ndone.');

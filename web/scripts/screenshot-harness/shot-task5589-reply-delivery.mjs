/** TASK-5589 compact Reply Delivery rail evidence (Mode C, production component). */
import { join } from 'node:path';
import { capture, startViteHarness, WEB_ROOT } from './harness.mjs';

const OUT = join(WEB_ROOT, 'scripts', 'screenshot-harness', 'out', 'task-5589');
const base = `{
  agent_name: 'ops_lead', state: 'queued', from_seq: 8, through_seq: 9,
  coalesced_message_count: 2, started_at: null,
  updated_at: '2026-08-24T13:00:00Z', last_terminal_reason: null
}`;
const cases = [
  { name: 'loading-production-width', viewport: [372, 360], render: `<RailShell><p className="text-text-muted text-xs" role="status">Loading reply delivery…</p></RailShell>` },
  { name: 'empty-production-width', viewport: [372, 360], render: `<RailShell><p className="text-text-muted text-xs">No current reply deliveries</p><ReplyDeliveryStrip entries={[]} nowMs={Date.parse('2026-08-24T13:01:00Z')} /></RailShell>` },
  { name: 'error-production-width', viewport: [372, 360], render: `<RailShell><p className="text-feedback-danger text-xs" role="alert">Couldn’t load reply delivery</p></RailShell>` },
  { name: 'populated-production-width', viewport: [372, 500], render: `<RailShell><ReplyDeliveryStrip entries={[{...${base}, agent_name:'frontend_engineer_primary', state:'running', from_seq:9, through_seq:9, coalesced_message_count:1, started_at:'2026-08-24T13:00:00Z'}, {...${base}, agent_name:'qa_engineer'}, {...${base}, agent_name:'support_lead', state:'retry_required', last_terminal_reason:'timeout'}]} nowMs={Date.parse('2026-08-24T13:01:00Z')} /></RailShell>` },
  { name: 'multi-agent-narrow-closed', viewport: [320, 520], render: `<RailShell><ReplyDeliveryStrip entries={[{...${base}, agent_name:'frontend_engineer_primary', state:'running', started_at:'2026-08-24T13:00:00Z'}, {...${base}, agent_name:'frontend_engineer_secondary', state:'running', started_at:'2026-08-24T13:00:20Z'}, {...${base}, agent_name:'qa_engineer'}, {...${base}, agent_name:'support_engineer', from_seq:9, through_seq:9, coalesced_message_count:1}]} nowMs={Date.parse('2026-08-24T13:01:00Z')} /></RailShell>` },
  { name: 'multi-agent-wide-open', viewport: [960, 520], open: true, render: `<RailShell><ReplyDeliveryStrip entries={[{...${base}, agent_name:'frontend_engineer_primary', state:'running', started_at:'2026-08-24T13:00:00Z'}, {...${base}, agent_name:'frontend_engineer_secondary', state:'running', started_at:'2026-08-24T13:00:20Z'}, {...${base}, agent_name:'qa_engineer'}, {...${base}, agent_name:'support_engineer', from_seq:9, through_seq:9, coalesced_message_count:1}]} nowMs={Date.parse('2026-08-24T13:01:00Z')} /></RailShell>` },
];

for (const testCase of cases) {
  const render = `<div className="w-rail max-w-full p-4"><h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Reply delivery</h3>${testCase.render}</div>`;
  const vite = await startViteHarness({
    importPath: '@/features/threads/ReplyDeliveryStrip',
    exportName: 'ReplyDeliveryStrip',
    render: render.replaceAll('<RailShell>', '<div aria-label="Reply delivery">').replaceAll('</RailShell>', '</div>'),
  });
  try {
    for (const theme of ['light', 'dark']) {
      await capture({
        url: vite.url(theme),
        out: join(OUT, `${testCase.name}-${theme}.png`),
        viewport: testCase.viewport,
        appTheme: false,
        prep: testCase.open ? [['click', "getByText('2 queued deliveries')"]] : [],
        session: `task5589-${testCase.name}-${theme}`,
      });
    }
  } finally {
    await vite.stop();
  }
}

console.log(`TASK-5589 evidence written to ${OUT}`);

/**
 * Generate a static HTML evidence report for THR-105 Todos LIGHT fidelity.
 *
 * Reads the state-map and diff-report produced by the capture + diff scripts,
 * then writes an index.html with side-by-side reference/build comparisons and a
 * variance disposition table. All assets are local/relative.
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const OUT = process.argv[2] || join(HERE, 'out', 'thr105-complete-light');

const REF_STATES = {
  'todos-list-light.png': 'List (all groups)',
  'todos-detail-weekly_armed_schedule-101-light.png': 'Weekly armed detail (SCHEDULE-101)',
};

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function main() {
  await mkdir(OUT, { recursive: true });

  const stateMap = JSON.parse(await readFile(join(OUT, 'state-map.json'), 'utf8'));
  const diffReport = JSON.parse(await readFile(join(OUT, 'diff-report.json'), 'utf8'));

  const diffByName = Object.fromEntries(diffReport.comparisons.map((c) => [c.name, c]));

  const refRows = Object.entries(REF_STATES)
    .map(([builtFile, state]) => {
      const name = builtFile === 'todos-list-light.png' ? 'todos-list' : 'todos-detail-weekly-armed';
      const diff = diffByName[name];
      const refFile =
        builtFile === 'todos-list-light.png'
          ? 'reference-todos-list-light.png'
          : 'reference-todos-detail-armed-light.png';
      const diffText = diff?.error
        ? `Error: ${escapeHtml(diff.error)}`
        : `${escapeHtml(diff?.differingPixels?.toLocaleString() ?? '?')} px / ${escapeHtml(
            diff?.totalPixels?.toLocaleString() ?? '?',
          )} px (${escapeHtml(diff?.percentDiff?.toString() ?? '?')}%) differ`;
      return `
    <tr>
      <td>${escapeHtml(state)}</td>
      <td><code>${escapeHtml(refFile)}</code></td>
      <td><code>${escapeHtml(builtFile)}</code></td>
      <td>${diffText}</td>
    </tr>
    <tr>
      <td colspan="4" class="side-by-side">
        <div><img src="../reference/${escapeHtml(refFile)}" alt="reference"><p>Reference</p></div>
        <div><img src="${escapeHtml(builtFile)}" alt="built"><p>Built</p></div>
      </td>
    </tr>`;
    })
    .join('');

  const capturedRows = stateMap.states
    .filter((s) => !REF_STATES[s.file])
    .map(
      (s) => `
    <tr>
      <td>${escapeHtml(s.state)}</td>
      <td><code>${escapeHtml(s.file)}</code></td>
      <td><a href="${escapeHtml(s.file)}">view</a></td>
    </tr>`,
    )
    .join('');

  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>THR-105 Todos LIGHT fidelity evidence — TASK-4129</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; max-width: 1400px; margin: 0 auto; padding: 24px; color: #111; background: #fff; }
  h1, h2 { font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0; }
  th, td { text-align: left; padding: 10px 12px; border: 1px solid #ddd; vertical-align: top; }
  th { background: #f5f5f5; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: #f5f5f5; padding: 2px 4px; border-radius: 4px; }
  .side-by-side { display: flex; gap: 16px; }
  .side-by-side > div { flex: 1; }
  .side-by-side img { width: 100%; border: 1px solid #ddd; border-radius: 8px; }
  .variance { background: #fff8e1; }
  .pass { color: #1b5e20; }
  .fail { color: #b71c1c; }
</style>
</head>
<body>
<h1>THR-105 Todos — 1440×900 LIGHT fidelity evidence</h1>
<p>Generated: ${escapeHtml(diffReport.generatedAt)} · Viewport: ${escapeHtml(
    diffReport.viewport.join('×'),
  )} · Content crop: ${escapeHtml(JSON.stringify(diffReport.comparisons[0]?.crop))}</p>

<h2>Reference vs built comparisons (numeric pixel diff)</h2>
<p>Diff is computed over the page content area only, excluding the shared AppShell
sidebar/topbar (V9 — out of scope). The two approved reference states are the
LIGHT list and the weekly-armed detail from TASK-4096.</p>
<table>
  <thead>
    <tr><th>State</th><th>Reference file</th><th>Built file</th><th>Numeric diff</th></tr>
  </thead>
  <tbody>
    ${refRows}
  </tbody>
</table>

<h2>All captured states</h2>
<table>
  <thead>
    <tr><th>State</th><th>File</th><th>Link</th></tr>
  </thead>
  <tbody>
    ${capturedRows}
  </tbody>
</table>

<h2>Variance disposition</h2>
<table>
  <thead>
    <tr><th>ID</th><th>Element</th><th>Measured delta</th><th>Disposition</th></tr>
  </thead>
  <tbody>
    <tr class="variance"><td>V1</td><td>Eyebrow label font-family</td><td>Reference IBM Plex Mono; build uses app mono token</td><td>Blocked — requires shared design-system font/token addition</td></tr>
    <tr class="variance"><td>V2</td><td>Page H1 size/weight/tracking</td><td>Reference 33px/500/−0.66px; build uses shared text-display token</td><td>Blocked — requires shared design-system display token</td></tr>
    <tr class="variance"><td>V3</td><td>Filter-tab / status-pill padding</td><td>1–2px off multiple axes</td><td>Local Todos-only adjustment possible; considered in this pass</td></tr>
    <tr class="variance"><td>V4</td><td>Status-pill exact colors</td><td>15–25 rgb-unit deltas, same hue family</td><td>Blocked — build correctly reuses semantic tokens; bespoke colors require shared token change</td></tr>
    <tr class="variance"><td>V5</td><td>Detail-page composition</td><td>Structural (next-fire banner, schedule card, activity placement, blockquote)</td><td>Blocked by V8 (missing schema field for distinct H1/normalized-commitment text); structural redesign alone cannot reach reference</td></tr>
    <tr><td>V6</td><td>Edit-dialog button label</td><td>Reference "Edit"; build already uses "Edit"</td><td>Closed</td></tr>
    <tr><td>V7</td><td>Row/detail schedule-description phrasing</td><td>Reference status-aware; build already status-aware</td><td>Closed</td></tr>
    <tr class="variance"><td>V8</td><td>Detail "Normalized commitment" card text</td><td>Reference shows distinct longer sentence; API has one normalized_brief field</td><td>Blocked — schema field addition required (forbidden)</td></tr>
    <tr class="variance"><td>V9</td><td>Sidebar chrome</td><td>Reference older nav (Spend, no Health/Skills); build current shared Sidebar</td><td>Out of scope — shared AppShell/Sidebar, not Todos feature code</td></tr>
  </tbody>
</table>

<h2>Reproduction</h2>
<pre><code>cd web
npm run build
node scripts/screenshot-harness/thr105-todos-quick.mjs scripts/screenshot-harness/out/thr105-complete-light
node scripts/screenshot-harness/thr105-todos-diff-light.mjs scripts/screenshot-harness/out/thr105-complete-light
node scripts/screenshot-harness/thr105-todos-report-light.mjs scripts/screenshot-harness/out/thr105-complete-light</code></pre>
</body>
</html>`;

  const reportPath = join(OUT, 'index.html');
  await writeFile(reportPath, html);
  console.log(`Report written to ${reportPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

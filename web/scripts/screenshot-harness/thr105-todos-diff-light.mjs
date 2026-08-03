/**
 * Deterministic numeric LIGHT pixel-diff for THR-105 Todos — TASK-4129.
 *
 * Compares the two approved reference states (list and weekly-armed detail)
 * against the current built captures using the browser's canvas API via
 * playwright-cli. The result is posted back to a local /api endpoint so we
 * never have to parse playwright-cli's formatted stdout.
 *
 * Output: out/thr105-complete-light/diff-report.json
 */
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from './harness.mjs';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const OUT = process.argv[2] || join(HERE, 'out', 'thr105-complete-light');
const REF_DIR = join(HERE, 'reference');
const SESSION = 'thr105-diff';

const COMPARISONS = [
  {
    name: 'todos-list',
    reference: 'reference-todos-list-light.png',
    built: 'todos-list-light.png',
    state: 'List (all groups)',
  },
  {
    name: 'todos-detail-weekly-armed',
    reference: 'reference-todos-detail-armed-light.png',
    built: 'todos-detail-weekly_armed_schedule-101-light.png',
    state: 'Weekly armed detail (SCHEDULE-101)',
  },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pw(args) {
  return new Promise((res, rej) => {
    const proc = spawn('playwright-cli', [`-s=${SESSION}`, ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let err = '';
    proc.stderr.on('data', (d) => (err += d));
    proc.on('exit', (code) => {
      if (code !== 0) return rej(new Error(`playwright-cli ${args.join(' ')} failed (${code}): ${err}`));
      res();
    });
    proc.on('error', rej);
  });
}

async function compareImages(refPath, builtPath) {
  let capturedResult = null;
  let capturedError = null;

  const srv = await createServer({
    root: HERE,
    api: [
      {
        path: '/api/result',
        method: 'POST',
        handler: async (req, res) => {
          let body = '';
          req.on('data', (d) => (body += d));
          req.on('end', () => {
            try {
              capturedResult = JSON.parse(body);
            } catch (e) {
              capturedError = e.message;
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end('{}');
          });
        },
      },
      {
        path: '/api/compare.html',
        handler: (_req, res) => {
          const html = `<!doctype html>
<html><head><meta charset="utf-8"></head>
<body>
<img id="ref" src="/api/ref.png" crossorigin="anonymous" style="display:none">
<img id="built" src="/api/built.png" crossorigin="anonymous" style="display:none">
<canvas id="c" style="display:none"></canvas>
<script>
async function go() {
  const ref = document.getElementById('ref');
  const built = document.getElementById('built');
  await Promise.all([
    new Promise((r, rej) => { ref.onload = r; ref.onerror = rej; }),
    new Promise((r, rej) => { built.onload = r; built.onerror = rej; })
  ]);
  const crop = { x: 230, y: 60, w: 1210, h: 840 };
  const c = document.getElementById('c');
  c.width = crop.w; c.height = crop.h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(ref, -crop.x, -crop.y);
  const refData = ctx.getImageData(0, 0, crop.w, crop.h).data;
  ctx.clearRect(0, 0, crop.w, crop.h);
  ctx.drawImage(built, -crop.x, -crop.y);
  const builtData = ctx.getImageData(0, 0, crop.w, crop.h).data;
  let diff = 0;
  let alphaDiff = 0;
  for (let i = 0; i < refData.length; i += 4) {
    const r = Math.abs(refData[i] - builtData[i]);
    const g = Math.abs(refData[i + 1] - builtData[i + 1]);
    const b = Math.abs(refData[i + 2] - builtData[i + 2]);
    const a = Math.abs(refData[i + 3] - builtData[i + 3]);
    if (r > 1 || g > 1 || b > 1 || a > 1) diff++;
    if (a > 1) alphaDiff++;
  }
  const result = {
    crop,
    width: crop.w,
    height: crop.h,
    totalPixels: crop.w * crop.h,
    differingPixels: diff,
    alphaDifferences: alphaDiff,
    percentDiff: Number(((diff / (crop.w * crop.h)) * 100).toFixed(4)),
  };
  await fetch('/api/result', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(result),
  });
}
go().catch(e => {
  fetch('/api/result', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ error: e.message }),
  });
});
</script>
</body></html>`;
          res.writeHead(200, { 'Content-Type': 'text/html' });
          res.end(html);
        },
      },
      {
        path: '/api/ref.png',
        handler: async (_req, res) => {
          const data = await readFile(refPath);
          res.writeHead(200, { 'Content-Type': 'image/png' });
          res.end(data);
        },
      },
      {
        path: '/api/built.png',
        handler: async (_req, res) => {
          const data = await readFile(builtPath);
          res.writeHead(200, { 'Content-Type': 'image/png' });
          res.end(data);
        },
      },
    ],
  });

  try {
    await pw(['open']);
    await pw(['resize', '800', '600']);
    await pw(['goto', `${srv.url}/api/compare.html`]);
    await sleep(2500);
    if (capturedError) throw new Error(capturedError);
    if (!capturedResult) throw new Error('No diff result received from browser');
    return capturedResult;
  } finally {
    await pw(['close']).catch(() => {});
    await srv.close();
  }
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const results = [];

  for (const cmp of COMPARISONS) {
    const refPath = join(REF_DIR, cmp.reference);
    const builtPath = join(OUT, cmp.built);
    try {
      const result = await compareImages(refPath, builtPath);
      results.push({ ...cmp, ...result });
    } catch (err) {
      results.push({ ...cmp, error: err.message });
    }
  }

  const report = {
    generatedAt: new Date().toISOString(),
    viewport: [1440, 900],
    tolerance: { percent: 1.0, pixel: 1 },
    comparisons: results,
  };

  const reportPath = join(OUT, 'diff-report.json');
  await writeFile(reportPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  console.log(`\nReport written to ${reportPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

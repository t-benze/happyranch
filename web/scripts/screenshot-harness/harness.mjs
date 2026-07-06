/**
 * Composable FE screenshot harness — the reusable module every FE task imports
 * to capture real-viewport Playwright evidence in one of four modes. FE tasks
 * COMPOSE these primitives (they do NOT fork a copy). See README.md for the
 * mode decision tree (also landed in the shared KB:
 * `fe-screenshot-harness-mode-decision-tree`).
 *
 * Guardrails baked in:
 *   - Zero new npm dependency. Static/API/WS servers use only Node built-ins;
 *     screenshots shell out to the venv Playwright via the `playwright-cli`
 *     binary (never an imported npm playwright).
 *   - Plain Node ESM (.mjs) under web/scripts/ so it stays OUT of `eslint src`,
 *     `tsc -b` (tsconfig include = src only), and the `vite build` graph — it
 *     has no effect on Web CI.
 *
 * Primitives:
 *   - createServer({ root, api, ws })  -> static files + /api JSON mock + WS
 *   - defaultApiRoutes(opts)           -> the routes the SPA shell needs to boot
 *   - startViteHarness({ entry, props })-> vite dev serving a generated mount (mode C)
 *   - capture({ url, out, theme, ... })-> drive playwright-cli for one PNG
 *
 * Mode composers (thin wrappers over the primitives):
 *   - modeAProdApi, modeBDistCss, modeCProp, modeDWsDock
 */
import { createServer as httpCreateServer } from 'node:http';
import { spawn } from 'node:child_process';
import { readFile, writeFile, mkdir, rm, access } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { extname, join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { attachWsMock } from './ws-mock.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
/** web/ root, two levels up from web/scripts/screenshot-harness/. */
export const WEB_ROOT = resolve(HERE, '..', '..');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
};

// ---------------------------------------------------------------------------
// dist discovery
// ---------------------------------------------------------------------------

/** Return the built dist/ dir (modes A/B/D need it); throw a clear hint if absent. */
export function findDist() {
  const dist = join(WEB_ROOT, 'dist');
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error(
      `No built app at ${dist}. Modes A/B/D need it — run \`npm run build\` in web/ first.`,
    );
  }
  return dist;
}

/** Absolute path to the compiled Tailwind CSS in dist (mode B links this). */
export function findDistCss() {
  const dist = findDist();
  const html = readFileSync(join(dist, 'index.html'), 'utf8');
  const m = html.match(/href="([^"]+\.css)"/);
  if (!m) throw new Error('Could not find the built CSS href in dist/index.html');
  return m[1]; // e.g. /assets/index-abc123.css
}

// ---------------------------------------------------------------------------
// /api mock routes
// ---------------------------------------------------------------------------

/**
 * The routes the SPA shell needs to boot without white-screening
 * (MEM-007 / MEM-010). Spread these into `api` and append your page-specific
 * routes after them to override.
 *
 * @param {object} [opts]
 * @param {string} [opts.token]              bearer the SPA auto-bootstraps
 * @param {Array<{slug:string,root:string}>} [opts.orgs]
 * @param {boolean} [opts.assistantConfigured] assistant status.state = 'configured'
 */
export function defaultApiRoutes(opts = {}) {
  const {
    token = 'harness-token',
    orgs = [{ slug: 'demo', root: '/tmp/demo' }],
    assistantConfigured = false,
  } = opts;
  const routes = [
    { path: '/api/v1/auth/bootstrap', json: { token } },
    { path: '/api/v1/orgs', json: { orgs, broken: [] } },
  ];
  if (assistantConfigured) {
    routes.push(
      { path: '/api/v1/assistant/status', json: { state: 'configured' } },
      {
        path: '/api/v1/assistant/a-mode/status',
        json: { available: true, executor: 'claude' },
      },
    );
  }
  return routes;
}

function matchApiRoute(routes, method, pathname) {
  for (const r of routes) {
    if (r.method && r.method.toUpperCase() !== method) continue;
    if (typeof r.path === 'string' ? r.path === pathname : r.path.test(pathname)) {
      return r;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// server: static files + /api mock (+ optional WS)
// ---------------------------------------------------------------------------

/**
 * Stand up an http server that serves static files from `root` with SPA
 * fallback to index.html, mocks `/api/*` from `api` (unmatched -> `{}`), and
 * optionally mounts a WS mock.
 *
 * @param {object} opts
 * @param {string}  opts.root   directory to serve (e.g. findDist())
 * @param {Array}   [opts.api]  route list: { method?, path:string|RegExp, json | handler(req,res) }
 * @param {object}  [opts.ws]   { path, onConnect } forwarded to attachWsMock
 * @returns {Promise<{ port:number, url:string, server:import('node:http').Server, close:()=>Promise<void> }>}
 */
export async function createServer({ root, api = [], ws = null }) {
  const server = httpCreateServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const pathname = url.pathname;

    if (pathname.startsWith('/api/')) {
      const route = matchApiRoute(api, req.method, pathname);
      if (route && route.handler) return route.handler(req, res);
      const body = route ? route.json : {}; // unmatched /api -> {} so SPA doesn't crash
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(typeof body === 'function' ? body(url) : body));
      return;
    }

    // static file, else SPA fallback to index.html
    let filePath = join(root, decodeURIComponent(pathname));
    if (pathname === '/' || !existsSync(filePath) || !extname(filePath)) {
      filePath = join(root, 'index.html');
    }
    try {
      const data = await readFile(filePath);
      res.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });

  if (ws) attachWsMock(server, ws);

  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  return {
    port,
    url: `http://127.0.0.1:${port}`,
    server,
    close: () => new Promise((r) => server.close(() => r())),
  };
}

// ---------------------------------------------------------------------------
// vite dev harness (mode C — prop-driven, no providers)
// ---------------------------------------------------------------------------

/**
 * Spawn `vite` dev serving a generated entry that mounts ONE component with
 * explicit props and NO providers. Temp files are written under
 * web/.screenshot-harness-tmp/ (git-ignored) and removed on stop().
 *
 * @param {object} opts
 * @param {string} opts.importPath   module to import, e.g. '@/design-system/primitives/Button'
 * @param {string} opts.exportName   named export to mount, e.g. 'Button'
 * @param {string} opts.render       JSX string mounting the component with props,
 *                                    e.g. "<Button variant='default'>Send</Button>".
 *                                    The import + React are in scope.
 * @returns {Promise<{ url:(theme?:string)=>string, stop:()=>Promise<void> }>}
 */
export async function startViteHarness({ importPath, exportName, render }) {
  const tmpDir = join(WEB_ROOT, '.screenshot-harness-tmp');
  await mkdir(tmpDir, { recursive: true });
  const entryFile = join(tmpDir, 'entry.tsx');
  const htmlFile = join(tmpDir, 'harness.html');

  // Entry: no QueryClient / Router / DataContext — a bare mount. Theme the
  // CHROME with token classes so data-theme=dark actually flips the surface
  // (MEM-016), even when the component's own colors are fixed utilities.
  await writeFile(
    entryFile,
    `import React from 'react';
import { createRoot } from 'react-dom/client';
import '@/styles.css';
import { ${exportName} } from '${importPath}';

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get('theme') ?? 'light';

const el = document.getElementById('root')!;
createRoot(el).render(
  <div className="bg-surface-canvas text-fg border-border-subtle flex min-h-screen items-center justify-center gap-4 border p-16">
    ${render}
  </div>,
);
`,
  );
  await writeFile(
    htmlFile,
    `<!doctype html><html><head><meta charset="utf-8"></head>
<body class="bg-bg text-fg"><div id="root"></div>
<script type="module" src="./entry.tsx"></script></body></html>`,
  );

  const proc = spawn(
    'npx',
    ['--no-install', 'vite', '--port', '0', '--strictPort', 'false', '--host', '127.0.0.1'],
    { cwd: WEB_ROOT, stdio: ['ignore', 'pipe', 'pipe'] },
  );

  const port = await new Promise((res, rej) => {
    let out = '';
    const onData = (d) => {
      out += d.toString();
      const m = out.match(/localhost:(\d+)|127\.0\.0\.1:(\d+)/);
      if (m) res(Number(m[1] || m[2]));
    };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('exit', (code) => rej(new Error(`vite exited early (${code}): ${out}`)));
    setTimeout(() => rej(new Error(`vite did not report a port in 30s: ${out}`)), 30000);
  });

  const base = `http://127.0.0.1:${port}/.screenshot-harness-tmp/harness.html`;
  return {
    url: (theme = 'light') => `${base}?theme=${theme}`,
    stop: async () => {
      proc.kill('SIGTERM');
      await rm(tmpDir, { recursive: true, force: true });
    },
  };
}

// ---------------------------------------------------------------------------
// capture — drive playwright-cli (venv Playwright), never an npm import
// ---------------------------------------------------------------------------

function pw(session, args) {
  return new Promise((res, rej) => {
    const proc = spawn('playwright-cli', [`-s=${session}`, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
    let err = '';
    proc.stderr.on('data', (d) => (err += d));
    proc.on('exit', (code) =>
      code === 0 ? res() : rej(new Error(`playwright-cli ${args[0]} failed (${code}): ${err}`)),
    );
    proc.on('error', rej);
  });
}

/**
 * Capture one screenshot by driving playwright-cli through a single browser
 * session: open -> resize -> goto -> [theme] -> [prep steps] -> settle -> shot.
 *
 * @param {object} opts
 * @param {string}   opts.url
 * @param {string}   opts.out                absolute PNG path to write
 * @param {[number,number]} [opts.viewport]  default [1440,900] (shell is desktop-only, MEM-004)
 * @param {string}   [opts.theme]            'light' | 'dark'
 * @param {boolean}  [opts.appTheme]         true: flip via localStorage+reload (app modes);
 *                                           false: theme is baked into the URL (standalone modes)
 * @param {string[][]} [opts.prep]           extra playwright-cli arg arrays run before the shot
 * @param {number}   [opts.settleMs]         wait before shot (default 800)
 * @param {string}   [opts.session]          playwright-cli session name (default 'sshot')
 * @returns {Promise<string>} the PNG path
 */
export async function capture({
  url,
  out,
  viewport = [1440, 900],
  theme,
  appTheme = true,
  prep = [],
  settleMs = 800,
  session = 'sshot',
}) {
  await mkdir(dirname(out), { recursive: true });
  await pw(session, ['open']);
  try {
    await pw(session, ['resize', String(viewport[0]), String(viewport[1])]);
    await pw(session, ['goto', url]);
    if (theme && appTheme) {
      await pw(session, ['localstorage-set', 'happyranch.theme', theme]);
      await pw(session, ['reload']);
    }
    for (const step of prep) await pw(session, step);
    if (settleMs) await sleep(settleMs);
    await pw(session, ['screenshot', `--filename=${out}`]);
  } finally {
    await pw(session, ['close']).catch(() => {});
  }
  await access(out).catch(() => {
    throw new Error(`screenshot not written to ${out}`);
  });
  return out;
}

// ---------------------------------------------------------------------------
// mode composers
// ---------------------------------------------------------------------------

/**
 * Mode A — prod build + node /api mock. Full built app + realistic REST data.
 * Captures `route` in both light and dark.
 */
export async function modeAProdApi({ route, outDir, name, api = [], orgs, viewport }) {
  const srv = await createServer({
    root: findDist(),
    api: [...defaultApiRoutes({ orgs }), ...api],
  });
  try {
    const shots = [];
    for (const theme of ['light', 'dark']) {
      shots.push(
        await capture({
          url: `${srv.url}${route}`,
          out: join(outDir, `${name}-${theme}.png`),
          theme,
          viewport,
        }),
      );
    }
    return shots;
  } finally {
    await srv.close();
  }
}

/**
 * Mode B — standalone harness linking the built dist CSS. Renders arbitrary
 * `markup` against the REAL compiled Tailwind (tokens, dark overrides, every
 * built utility) with zero backend. Captures both themes (baked into markup via
 * data-theme on <html>).
 */
export async function modeBDistCss({ markup, outDir, name, viewport = [640, 360] }) {
  const dist = findDist();
  const cssHref = findDistCss();
  const page = (theme) => `<!doctype html>
<html data-theme="${theme}"><head><meta charset="utf-8"><link rel="stylesheet" href="${cssHref}"></head>
<body class="bg-bg text-fg"><div class="bg-surface-canvas text-fg flex min-h-screen items-center justify-center gap-4 p-16">
${markup}
</div></body></html>`;
  const tmpDir = join(dist, '.mode-b-tmp');
  await mkdir(tmpDir, { recursive: true });
  const srv = await createServer({ root: dist });
  try {
    const shots = [];
    for (const theme of ['light', 'dark']) {
      const rel = `/.mode-b-tmp/${name}-${theme}.html`;
      await writeFile(join(dist, rel), page(theme));
      shots.push(
        await capture({
          url: `${srv.url}${rel}`,
          out: join(outDir, `${name}-${theme}.png`),
          appTheme: false,
          viewport,
        }),
      );
    }
    return shots;
  } finally {
    await srv.close();
    await rm(tmpDir, { recursive: true, force: true });
  }
}

/**
 * Mode C — prop-driven, no-provider harness. Mounts ONE component with explicit
 * props via a vite dev server. Captures both themes (?theme=).
 */
export async function modeCProp({ importPath, exportName, render, outDir, name, viewport = [640, 360] }) {
  const vite = await startViteHarness({ importPath, exportName, render });
  try {
    const shots = [];
    for (const theme of ['light', 'dark']) {
      shots.push(
        await capture({
          url: vite.url(theme),
          out: join(outDir, `${name}-${theme}.png`),
          appTheme: false,
          viewport,
        }),
      );
    }
    return shots;
  } finally {
    await vite.stop();
  }
}

/**
 * Mode D — prod build + node /api mock + WS mock for the A-mode assistant dock.
 * Serves the full app, mocks the WS so the dock hydrates a `history` transcript,
 * opens the dock, and captures both themes.
 *
 * @param {object} opts
 * @param {string} opts.route              page to land on (dock is global), e.g. '/orgs/demo/dashboard'
 * @param {Array}  opts.conversations      ConversationSummary[] for the switcher
 * @param {object} opts.historyByConv      map convId -> persisted turns (history frame `turns`)
 * @param {string} opts.activeConv         id of the initially active conversation
 */
export async function modeDWsDock({
  route,
  outDir,
  name,
  conversations,
  historyByConv,
  activeConv,
  orgs,
  viewport,
  api: extraApi = [],
}) {
  let active = activeConv;
  const api = [
    ...defaultApiRoutes({ orgs, assistantConfigured: true }),
    ...extraApi,
    {
      path: '/api/v1/assistant/a-mode/conversations',
      handler: (req, res) => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(conversations.map((c) => ({ ...c, active: c.id === active }))));
      },
    },
    {
      // activate: POST /conversations/:id/activate
      path: /\/api\/v1\/assistant\/a-mode\/conversations\/[^/]+\/activate$/,
      handler: (req, res) => {
        active = req.url.split('/').slice(-2)[0];
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true }));
      },
    },
  ];

  const srv = await createServer({
    root: findDist(),
    api,
    ws: {
      path: '/api/v1/assistant/a-mode',
      onConnect: (conn) => {
        const turns = historyByConv[active] ?? [];
        conn.sendJson({ type: 'history', turns });
        conn.sendJson({ type: 'status', code: 'ready' });
      },
    },
  });
  try {
    const shots = [];
    for (const theme of ['light', 'dark']) {
      shots.push(
        await capture({
          url: `${srv.url}${route}`,
          out: join(outDir, `${name}-${theme}.png`),
          theme,
          viewport,
          settleMs: 1400,
          // Open the dock via the global Cmd-K hotkey (AssistantDockHost owns
          // it); also click any explicit [data-assistant-open] trigger.
          prep: [
            [
              'eval',
              "(()=>{const e=new KeyboardEvent('keydown',{key:'k',metaKey:true,ctrlKey:true,bubbles:true});document.dispatchEvent(e);window.dispatchEvent(e);const t=document.querySelector('[data-assistant-open]');if(t)t.click();})()",
            ],
          ],
        }),
      );
    }
    return shots;
  } finally {
    await srv.close();
  }
}
